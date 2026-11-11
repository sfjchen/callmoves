"""Evaluation harness: precision at top k of the ranked moments against hand labels.

    python -m callmoves.eval fixtures/acme_q3_2026 --labels fixtures/acme_q3_2026/labels.json

A label is a wall-clock time a person says moved the stock. A moment is a hit when it lands within TOLERANCE of a
label, the spec's success metric (3 of the top 5 within 2 minutes of a labeled move). Several moments may land on
the same label and each counts, so the harness also lists the labels no moment reached.

    python -m callmoves.eval data/*/ --placebo

The placebo needs no labels: it asks whether statements line up with price moves better than the same statements
moved in time do. Each shift rotates every claim around the call by the same amount, wrapping past the end, so the
shifted claims stay inside the call, in the same session and with the same bars. Shifting claims outside the call
compared the call's volatility with quieter minutes instead (found 2026-11-10). Over several calls it adds a sign
test: under no timing signal, the real score beats the median shift in half the calls. Calls on the same day and
sector are not independent, so the pooled p-value is a rough guide, not a test of the shipped Claude path.

    python -m callmoves.eval data/*/ --stats

Stats sums what each call cost and took from its stats.json (written by ingest and run since 2026-11-10), against
the spec's target of a 60-minute call in under 15 minutes for under $2.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel

from . import market
from .claims import extract_claims_rules
from .estimates import match_consensus
from .models import CallOutput, Estimate, Moment, Stats
from .novelty import judge_novelty_rules
from .run import run
from .score import (
    KIND_WEIGHT,
    OPEN_MIN,
    OPEN_SETTLE_MIN,
    STALE_WEIGHT,
    market_relevant,
    rank_moments,
)
from .transcript import align, load_call, qa_start_ms

TOLERANCE = timedelta(minutes=2)
MIN_SHIFT_MIN = 15   # far enough that no shifted claim keeps its own move
SHIFT_STEP_MIN = 5


class Label(BaseModel):
    ts: datetime  # wall clock, tz-aware
    note: str = ""


class Evaluation(BaseModel):
    hits: list[int | None]       # per moment in rank order, the index of the label it landed on
    precision: dict[int, float]  # k -> hits among the top k, over the moments actually ranked (at most k)
    missed: list[Label]


def evaluate(moments: list[Moment], labels: list[Label], ks: tuple[int, ...] = (3, 5),
             tol: timedelta = TOLERANCE) -> Evaluation:
    """Matches each moment to its nearest label and scores the top k.
    Fewer than k moments are scored over their count."""
    def nearest(ts: datetime) -> int | None:
        """Returns the index of the label within tol of ts that is closest to it, else None."""
        i = min(range(len(labels)), key=lambda i: abs(labels[i].ts - ts))
        return i if abs(labels[i].ts - ts) <= tol else None

    hits = [nearest(m.ts) for m in moments] if labels else [None] * len(moments)
    precision = {k: sum(h is not None for h in hits[:k]) / max(1, len(hits[:k])) for k in ks}
    return Evaluation(hits=hits, precision=precision, missed=[l for i, l in enumerate(labels) if i not in hits])


def placebo(call: Path, k: int = 3, benchmark: str = market.BENCHMARK) -> tuple[float, list[float]]:
    """Ranks rule-based claims from every turn against the real bars, then again with every claim rotated around the
    call by each shift from MIN_SHIFT_MIN to the call length minus MIN_SHIFT_MIN, and returns the real top-k score
    sum and the shifted sums. Claims come from all turns, not the price-first candidates, so the real run gets no
    head start, and the rule extractor keeps every run offline and identical. If statements carry no timing signal,
    the real sum is just 1 draw among the shifted ones. `benchmark` picks what the stock's move is measured
    against."""
    meta, segments = load_call(call / "call.json")
    segments = align(meta, segments)
    start, span = segments[0].ts, segments[-1].ts - segments[0].ts
    shifts = range(MIN_SHIFT_MIN, int(span.total_seconds() // 60) - MIN_SHIFT_MIN + 1, SHIFT_STEP_MIN)
    bars, _, _ = market.load_call_bars(call, meta.ticker, meta.etf, meta.call_start, benchmark)
    release, prior = (call / "release.txt").read_text(), (call / "prior_transcript.txt").read_text()
    claims = [judge_novelty_rules(c, release, prior) for c in extract_claims_rules(segments)]

    def top_sum(cs) -> float:
        """Returns the summed score of the top k moments ranked from claims cs."""
        return sum(m.score for m in rank_moments(cs, segments, bars, top_n=k))

    fake = [top_sum([c.model_copy(update={"ts": start + (c.ts - start + timedelta(minutes=s)) % span}) for c in claims])
            for s in shifts]
    return top_sum(claims), fake


PRED_FEATURES = ("guidance", "result", "novel", "cons_gap", "qa", "pre_vol", "pre_move", "peer_pre", "log_min")
RIDGE = 1.0


def claim_rows(call: Path, benchmark: str) -> pd.DataFrame:
    """Every rule-based claim of 1 call that falls inside the call and outside the opening auction, with the
    features known by the statement's minute and the target, |abnormal return| over the 5 minutes from that minute.
    Features read only bars before the minute: trailing 20-minute volatility and the last 5 minutes' abnormal and
    peer moves, so nothing after the statement leaks in."""
    meta, segments = load_call(call / "call.json")
    segments = align(meta, segments)
    bars, _, _ = market.load_call_bars(call, meta.ticker, meta.etf, meta.call_start, benchmark)
    release, prior = (call / "release.txt").read_text(), (call / "prior_transcript.txt").read_text()
    est = json.loads((call / "estimates.json").read_text()).get("estimates", []) \
        if (call / "estimates.json").exists() else []
    claims = match_consensus([judge_novelty_rules(c, release, prior) for c in extract_claims_rules(segments)],
                             [Estimate(**e) for e in est])
    qa_ms = qa_start_ms(segments)
    seg_ms = {s.seg_id: s.t0_ms for s in segments}
    rows = []
    for c in claims:
        t0 = market.minute_of(c.ts)
        et = c.ts.astimezone(market.ET)
        if OPEN_MIN - market.WINDOW_MIN <= et.hour * 60 + et.minute < OPEN_MIN + OPEN_SETTLE_MIN:
            continue
        before = bars[bars["ts"] < t0]
        if len(before) < 10 or bars[bars["ts"] >= t0].empty:
            continue
        last5 = before.tail(market.WINDOW_MIN)
        peer = abs(last5["peer_px"].iloc[-1] / before["peer_px"].iloc[-6] - 1) \
            if "peer_px" in before and len(before) > 5 else 0.0
        rows.append({
            "call": call.name, "y": abs(market.forward_window(bars, c.ts)["adj_ret"]),
            "guidance": c.kind == "guidance", "result": c.kind == "result", "novel": bool(c.novel),
            "cons_gap": min(0.2, abs(c.vs_consensus_pct or 0.0)), "qa": qa_ms is not None and seg_ms[c.seg_id] >= qa_ms,
            "pre_vol": before["adj_ret"].tail(market.BASELINE_MIN).std(), "pre_move": abs(last5["adj_ret"].sum()),
            "peer_pre": peer, "log_min": math.log1p(max(0.0, (c.ts - meta.call_start).total_seconds() / 60)),
            "ex_ante": (1.0 if market_relevant(c) else STALE_WEIGHT) * KIND_WEIGHT[c.kind],
        })
    return pd.DataFrame(rows)


def predict(calls: list[Path], benchmark: str) -> None:
    """Leave-one-call-out check of a ridge model of the next 5 minutes' |abnormal return| from claim features.
    For each held-out call it compares the Spearman rank correlation with the realized move of: the model, the
    trailing volatility alone, and the current score's ex-ante part (novelty x kind weight, the factors known before
    the move). Prints per-model means and how often the model beats each alternative, with a sign test."""
    df = pd.concat([claim_rows(c, benchmark) for c in calls], ignore_index=True)
    df = df.dropna(subset=list(PRED_FEATURES))
    res = []
    for held in sorted(df["call"].unique()):
        tr, te = df[df["call"] != held], df[df["call"] == held]
        if len(te) < 5 or te["y"].nunique() < 2:
            continue
        x = tr[list(PRED_FEATURES)].astype(float)
        mu, sd = x.mean(), x.std().replace(0, 1)
        xs = np.column_stack([np.ones(len(x)), (x - mu) / sd])
        coef = np.linalg.solve(xs.T @ xs + RIDGE * np.eye(xs.shape[1]), xs.T @ np.log(tr["y"] + 1e-4))
        xt = np.column_stack([np.ones(len(te)), (te[list(PRED_FEATURES)].astype(float) - mu) / sd])
        pred = pd.Series(xt @ coef, index=te.index)
        res.append({"call": held, "n": len(te), "model": pred.corr(te["y"], method="spearman"),
                    "vol": te["pre_vol"].corr(te["y"], method="spearman"),
                    "ex_ante": te["ex_ante"].corr(te["y"], method="spearman")})
    r = pd.DataFrame(res)
    print(f"{len(df)} claims in {df['call'].nunique()} calls, {len(r)} held-out calls with 5+ claims, "
          f"benchmark {benchmark}")
    for col in ("model", "vol", "ex_ante"):
        print(f"  mean Spearman {col}: {r[col].mean():+.3f} (median {r[col].median():+.3f})")
    for other in ("vol", "ex_ante"):
        # a call where either correlation is undefined (a constant predictor) or both tie says nothing either way
        d = (r["model"] - r[other]).dropna()
        d = d[d != 0]
        wins, n = int((d > 0).sum()), len(d)
        p = sum(math.comb(n, i) for i in range(wins, n + 1)) / 2 ** n
        print(f"  model beats {other} in {wins}/{n} calls, 1-sided sign test p = {p:.3f}")


def main(argv=None):
    """Scores 1 call against its labels and prints each moment as hit or miss, precision at 3 and 5, and the labels
    no moment reached. A call folder runs the pipeline, a JSON written by callmoves.run is read as is."""
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("calls", type=Path, nargs="+",
                    help="call folder (runs the pipeline) or a JSON written by callmoves.run, several for --placebo")
    ap.add_argument("--labels", type=Path, help="JSON list of {ts, note}, ts ISO with offset")
    ap.add_argument("--placebo", action="store_true", help="compare against claims shifted in time, no labels")
    ap.add_argument("--benchmark", choices=market.BENCHMARKS, default=market.BENCHMARK,
                    help="placebo: measure moves against the sector ETF or a market model fitted before the call")
    ap.add_argument("--predict", action="store_true",
                    help="leave-one-call-out check of a next-5-minute move model from claim features")
    ap.add_argument("--stats", action="store_true", help="cost and processing time per call from stats.json")
    args = ap.parse_args(argv)
    if args.stats:
        rows = [Stats(**json.loads((c / "stats.json").read_text())) for c in args.calls if (c / "stats.json").exists()]
        rows = [r for r in rows if r.gpu_usd is not None and r.claude_usd is not None]
        for k, f in {"audio min": lambda r: r.audio_min, "processing min": lambda r: (r.ingest_s + r.claude_s) / 60,
                     "min per audio hour": lambda r: (r.ingest_s + r.claude_s) / r.audio_min,
                     "GPU $": lambda r: r.gpu_usd, "Claude $": lambda r: r.claude_usd,
                     "total $": lambda r: r.total_usd}.items():
            xs = sorted(f(r) for r in rows)
            print(f"{k}: median {xs[len(xs) // 2]:.2f}, min {xs[0]:.2f}, max {xs[-1]:.2f}")
        print(f"total ${sum(r.total_usd for r in rows):.2f} for {len(rows)} of {len(args.calls)} calls with both GPU "
              f"and Claude cost recorded, GPUs: "
              f"{dict(sorted(Counter(r.gpu for r in rows).items()))}")
        return
    if args.predict:
        predict(args.calls, args.benchmark)
        return
    if args.placebo:
        wins = 0
        for call in args.calls:
            real, fake = placebo(call, benchmark=args.benchmark)
            p, med = (1 + sum(f >= real for f in fake)) / (1 + len(fake)), sorted(fake)[len(fake) // 2]
            wins += real > med
            print(f"{call.name}: top-3 score real {real:.4f}, shifted median {med:.4f}, "
                  f"beaten by {sum(f >= real for f in fake)}/{len(fake)} shifts, p = {p:.2f}")
        n = len(args.calls)
        if n > 1:
            sign_p = sum(math.comb(n, i) for i in range(wins, n + 1)) / 2 ** n
            print(f"real beats the shifted median in {wins}/{n} calls, 1-sided sign test p = {sign_p:.3f}")
        return
    if not args.labels:
        ap.error("--labels or --placebo is required")
    labels = [Label(**l) for l in json.loads(args.labels.read_text())]
    call = args.calls[0]
    moments = run(call).moments if call.is_dir() else CallOutput.model_validate_json(call.read_text()).moments
    ev = evaluate(moments, labels)
    for m, h in zip(moments, ev.hits):
        tag = f"hit {labels[h].ts:%H:%M}" if h is not None else "miss"
        print(f"  #{m.rank} {m.ts:%H:%M} {tag}: {m.quote[:70]}")
    for k, p in ev.precision.items():
        print(f"precision@{k} = {p:.2f} ({sum(h is not None for h in ev.hits[:k])}/{len(ev.hits[:k])})")
    for l in ev.missed:
        print(f"missed label {l.ts:%H:%M}: {l.note}")


if __name__ == "__main__":
    main()
