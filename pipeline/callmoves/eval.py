"""Evaluation harness: precision at top k of the ranked moments against hand labels.

    python -m callmoves.eval fixtures/acme_q3_2026 --labels fixtures/acme_q3_2026/labels.json

A label is a wall-clock time a person says moved the stock. A moment is a hit when it lands within TOLERANCE of a
label, the spec's success metric (3 of the top 5 within 2 minutes of a labeled move). Several moments may land on
the same label and each counts, so the harness also lists the labels no moment reached.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from .models import CallOutput, Moment
from .run import run

TOLERANCE = timedelta(minutes=2)


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


def main(argv=None):
    """Scores 1 call against its labels and prints each moment as hit or miss, precision at 3 and 5, and the labels
    no moment reached. A call folder runs the pipeline, a JSON written by callmoves.run is read as is."""
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("call", type=Path, help="call folder (runs the pipeline) or a JSON written by callmoves.run")
    ap.add_argument("--labels", type=Path, required=True, help="JSON list of {ts, note}, ts ISO with offset")
    args = ap.parse_args(argv)
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
