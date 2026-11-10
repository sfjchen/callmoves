"""Evaluation harness: precision at top k of the ranked moments against hand labels.

    python -m callmoves.eval fixtures/acme_q3_2026 --labels fixtures/acme_q3_2026/labels.json

A label is a wall-clock time a person says moved the stock. A moment is a hit when it lands within TOLERANCE of a
label, the spec's success metric (3 of the top 5 within 2 minutes of a labeled move). Several moments may land on
the same label and each counts, so the harness also lists the labels no moment reached.

    python -m callmoves.eval data/wfc_q3_2026 --placebo

The placebo needs no labels: it asks whether statements line up with price moves better than the same statements
moved 15 to 60 minutes away do.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from . import market
from .claims import extract_claims_rules
from .models import CallOutput, Moment
from .novelty import judge_novelty_rules
from .run import run
from .score import rank_moments
from .transcript import align, load_call

TOLERANCE = timedelta(minutes=2)
SHIFTS_MIN = [s for s in range(-60, 61, 5) if abs(s) >= 15]   # far enough that no shifted claim keeps its own move


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
        i = min(range(len(labels)), key=lambda i: abs(labels[i].ts - ts))
        return i if abs(labels[i].ts - ts) <= tol else None

    hits = [nearest(m.ts) for m in moments] if labels else [None] * len(moments)
    precision = {k: sum(h is not None for h in hits[:k]) / max(1, len(hits[:k])) for k in ks}
    return Evaluation(hits=hits, precision=precision, missed=[l for i, l in enumerate(labels) if i not in hits])


def placebo(call: Path, shifts: list[int] = SHIFTS_MIN, k: int = 3) -> tuple[float, list[float]]:
    """Ranks rule-based claims from every turn against the real bars, then again with every claim moved by each
    shift, and returns the real top-k score sum and the shifted sums. Claims come from all turns, not the
    price-first candidates, so the real run gets no head start, and the rule extractor keeps every run offline and
    identical. If statements carry no timing signal, the real sum is just 1 draw among the shifted ones."""
    meta, segments = load_call(call / "call.json")
    segments = align(meta, segments)
    bars = market.flag_abnormal(market.adjusted_returns(market.load_bars_csv(call / "bars.csv")))
    release, prior = (call / "release.txt").read_text(), (call / "prior_transcript.txt").read_text()
    claims = [judge_novelty_rules(c, release, prior) for c in extract_claims_rules(segments)]

    def top_sum(cs) -> float:
        return sum(m.score for m in rank_moments(cs, segments, bars, top_n=k))

    fake = [top_sum([c.model_copy(update={"ts": c.ts + timedelta(minutes=s)}) for c in claims]) for s in shifts]
    return top_sum(claims), fake


def main(argv=None):
    """Scores 1 call against its labels and prints each moment as hit or miss, precision at 3 and 5, and the labels
    no moment reached. A call folder runs the pipeline, a JSON written by callmoves.run is read as is."""
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("call", type=Path, help="call folder (runs the pipeline) or a JSON written by callmoves.run")
    ap.add_argument("--labels", type=Path, help="JSON list of {ts, note}, ts ISO with offset")
    ap.add_argument("--placebo", action="store_true", help="compare against claims shifted in time, no labels")
    args = ap.parse_args(argv)
    if args.placebo:
        real, fake = placebo(args.call)
        p = (1 + sum(f >= real for f in fake)) / (1 + len(fake))
        print(f"top-3 score real {real:.4f}, shifted median {sorted(fake)[len(fake) // 2]:.4f}, "
              f"beaten by {sum(f >= real for f in fake)}/{len(fake)} shifts, p = {p:.2f}")
        return
    if not args.labels:
        ap.error("--labels or --placebo is required")
    labels = [Label(**l) for l in json.loads(args.labels.read_text())]
    moments = run(args.call).moments if args.call.is_dir() else \
        CallOutput.model_validate_json(args.call.read_text()).moments
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
