"""Runs the whole pipeline on a call folder and writes one JSON the web app reads.

    python -m callmoves.run fixtures/acme_q3_2026 --out ../web/data
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from . import llm, market
from .claims import extract_claims
from .estimates import fetch_estimates, match_consensus
from .models import (
    Bar,
    CallOutput,
    CallSummary,
    Claim,
    Context,
    ContextSeries,
    Estimate,
    OpenReaction,
    Stats,
    TopMoment,
)
from .novelty import judge_novelty
from .publish import publish
from .score import rank_moments
from .transcript import align, load_call

SPARK_POINTS = 60   # library sparkline resolution
# topics a library card can show, matched on each claim's metric and quote; a call shows its 3 most frequent
THEMES = {
    "Guidance": r"guidance|outlook|full[- ]year|expect\w* .*(20\d\d|next quarter)",
    "NII": r"net interest income|\bNII\b|net interest margin", "AI": r"\bAI\b|artificial intelligence|data center",
    "Capex": r"capex|capital expenditure|capital spending", "Margins": r"margin", "Credit": r"credit|charge-off|"
    r"provision|delinquen", "Buybacks": r"buyback|repurchase|dividend", "Pricing": r"pricing|price increase",
    "Costs": r"expense|cost|efficien|head ?count", "Tariffs": r"tariff", "Deals": r"investment banking|M&A|pipeline",
    "Demand": r"demand|volume|traffic|orders|backlog", "Pipeline": r"approval|trial|launch|FDA",
}
CONTEXT_PAD = timedelta(minutes=15)   # comps panel minutes kept before and after the call


def _cached(path: Path, key: str, field: str, model, build):
    """Returns (items, stored): the items cached in path under key, else build() = (items, store) written to path
    when store is true. Reruns of an unchanged call are offline and free."""
    if path.exists():
        data = json.loads(path.read_text())
        if data.get("key") == key:
            return [model(**x) for x in data[field]], True
    items, store = build()
    if store:
        path.write_text(json.dumps({"key": key, field: [x.model_dump(mode="json") for x in items]}))
    return items, store


def run(fixture: Path) -> CallOutput:
    """Runs every step on a call folder: align, flag abnormal minutes, extract and judge claims, add consensus, rank
    moments, then fill the library summary and each segment's 5-minute adjusted return."""
    meta, segments = load_call(fixture / "call.json")
    segments = align(meta, segments)
    bars_df, ctx, fit = market.load_call_bars(fixture, meta.ticker, meta.etf, meta.call_start)
    release = (fixture / "release.txt").read_text()
    prior = (fixture / "prior_transcript.txt").read_text()
    call_end = meta.call_start + timedelta(milliseconds=max(s.t1_ms for s in segments) - meta.audio_offset_ms)
    # no trades during the call (an OTC ADR during a Hong Kong call): the next open is the only reaction,
    # so the whole call is read
    reaction = market.open_reaction(bars_df, meta.call_start, call_end)
    # price first: only turns spoken in the 5 minutes before an abnormal minute are read for claims
    candidates = segments if reaction else (market.segments_before_flags(segments, bars_df) or segments)

    def extract():
        """Returns (judged claims, llm_used) for the candidate turns."""
        claims, llm_used = extract_claims(candidates)
        return judge_novelty(claims, release, prior), llm_used

    stats_path = fixture / "stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    llm.USAGE.clear()
    t0 = time.time()
    ckey = hashlib.sha256((repr([(s.seg_id, s.text) for s in candidates]) + release + prior).encode()).hexdigest()[:16]
    claims, llm_used = _cached(fixture / "llm_claims.json", ckey, "claims", Claim, extract)
    if llm.USAGE:   # Claude ran this time (a cache hit costs nothing and keeps the recorded cost)
        stats |= {"claude_s": round(time.time() - t0), "claude_tokens": dict(llm.USAGE),
                  "claude_usd": round(sum(n * llm.PRICE[k] for k, n in llm.USAGE.items()) / 1e6, 4)}
        stats_path.write_text(json.dumps(stats))

    def fetch():
        """Returns (estimates, whether to cache them), an empty result is not cached."""
        rows = fetch_estimates(meta.ticker, meta.call_start, os.environ.get("ALPHA_VANTAGE_KEY"))
        return rows, bool(rows)   # so a failed fetch is retried on the next run

    key = f"{meta.ticker}|{meta.call_start.date()}|alphavantage"
    estimates, _ = _cached(fixture / "estimates.json", key, "estimates", Estimate, fetch)
    claims = match_consensus(claims, estimates)
    moments = rank_moments(claims, segments, bars_df, reaction=reaction)
    if not reaction:
        segments = [s.model_copy(update={"adj_ret_5m": market.forward_window(bars_df, s.ts)["adj_ret"]})
                    for s in segments]
    bars = [Bar(**{k: row[k] for k in ("ts", "px", "etf_px", "vol", "adj_ret", "cum_adj_ret", "vol_mult", "flagged")})
            for _, row in bars_df.iterrows()]
    context = None
    if ctx is not None:
        grid = bars_df[(bars_df["ts"] >= meta.call_start - CONTEXT_PAD) & (bars_df["ts"] <= call_end + CONTEXT_PAD)]
        wide = ctx[ctx["ts"] >= grid["ts"].min() - CONTEXT_PAD].pivot_table(
            index="ts", columns="sym", values="px", aggfunc="last").sort_index().ffill()
        at = pd.merge_asof(grid[["ts"]], wide.reset_index(), on="ts", direction="backward")
        peers = market.PEERS.get(meta.ticker, [])
        roles = {meta.ticker: "stock", meta.etf: "sector"} | {p: "peer" for p in peers} | market.CROSS_ASSETS
        context = Context(
            ts=list(at["ts"]), benchmark=fit["benchmark"], betas=fit["betas"], r2=fit["r2"],
            series=[ContextSeries(sym=sym, role=role, px=[None if pd.isna(x) else round(float(x), 4) for x in at[sym]])
                    for sym, role in roles.items() if sym in at])
    # the sparkline starts at 0 at the call start, so its last point is the move over the call
    base = next((b.cum_adj_ret for b in reversed(bars) if b.ts < meta.call_start), 0.0)
    cum = [b.cum_adj_ret - base for b in bars if meta.call_start <= b.ts <= call_end]
    step = max(1, math.ceil(len(cum) / SPARK_POINTS))
    top = moments[0] if moments else None
    counts = Counter(t for c in claims for t, rx in THEMES.items()
                     if re.search(rx, f"{c.metric} {c.quote}", re.IGNORECASE))
    summary = CallSummary(
        id=meta.call_id, ticker=meta.ticker, quarter=meta.quarter, company=meta.company, call_start=meta.call_start,
        etf=meta.etf, themes=[t for t, _ in counts.most_common(3)],
        n_moments=len(moments), n_flagged=sum(b.flagged for b in bars),
        spark=[c for i, c in enumerate(cum) if i % step == 0 or i == len(cum) - 1],
        top_moment=top and TopMoment(ts=top.ts, quote=top.quote, adj_ret_5m=top.adj_ret_5m, speaker=top.speaker,
                                  vol_mult=top.vol_mult, spillover=top.spillover))
    return CallOutput(call=meta, call_end=call_end, segments=segments, bars=bars, claims=claims, n_claims=len(claims),
                      summary=summary, moments=moments, llm_used=llm_used,
                      candidate_seg_ids=[s.seg_id for s in candidates],
                      open_reaction=OpenReaction(**reaction) if reaction else None, estimates=estimates,
                      stats=Stats(**stats) if stats else None, context=context)


def main(argv=None):
    """Runs 1 call folder, writes <out>/<call id>.json, prints the moments and, with --publish, upserts it."""
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--publish", action="store_true", help="also upsert the result into Supabase callmoves_calls")
    args = ap.parse_args(argv)
    result = run(args.fixture)
    args.out.mkdir(parents=True, exist_ok=True)
    call_id = result.call.call_id
    path = args.out / f"{call_id}.json"
    path.write_text(result.model_dump_json(indent=1))
    print(f"{call_id}: {len(result.segments)} segments, {len(result.bars)} bars, {sum(b.flagged for b in result.bars)} "
          f"flagged minutes, {len(result.candidate_seg_ids)} candidate turns, {len(result.claims)} claims, "
          f"llm={'claude' if result.llm_used else 'rules'}, {len(result.estimates)} estimates")
    for m in result.moments:
        print(f"  #{m.rank} {m.ts:%H:%M} {m.speaker}: {m.quote[:80]}  adj5m={m.adj_ret_5m:+.2%} vol={m.vol_mult:.1f}x "
              f"novel={m.novel} vs_cons={m.vs_consensus_pct} {m.confidence} score={m.score:.4f}")
    print(f"wrote {path}")
    if args.publish:
        print(f"published {publish(result)}")


if __name__ == "__main__":
    main()
