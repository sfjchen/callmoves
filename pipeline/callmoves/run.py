"""Run the whole pipeline on a call folder and write one JSON the web app reads.

    python -m callmoves.run fixtures/acme_q3_2026 --out ../web/data
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

from . import market
from .claims import extract_claims
from .models import CallOutput
from .novelty import judge_novelty
from .score import rank_moments
from .transcript import align, load_call


def _claims_with_novelty(fixture: Path, segments, release: str, prior: str):
    """Claude results are cached next to the call, keyed by the transcript and documents, so re-scoring is free."""
    import hashlib
    from .models import Claim

    key = hashlib.sha256((repr([(s.seg_id, s.text) for s in segments]) + release + prior).encode()).hexdigest()[:16]
    cache = fixture / "llm_claims.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        if data.get("key") == key:
            return [Claim(**c) for c in data["claims"]], True
    claims, llm_used = extract_claims(segments)
    claims = judge_novelty(claims, release, prior)
    if llm_used:
        cache.write_text(json.dumps({"key": key, "claims": [c.model_dump(mode="json") for c in claims]}))
    return claims, llm_used


def run(fixture: Path) -> CallOutput:
    meta, segments = load_call(fixture / "call.json")
    segments = align(meta, segments)
    bars_df = market.load_bars_csv(fixture / "bars.csv")
    bars_df = market.flag_abnormal(market.adjusted_returns(bars_df))
    release = (fixture / "release.txt").read_text()
    prior = (fixture / "prior_transcript.txt").read_text()
    # price first: only turns spoken in the 5 minutes before an abnormal minute are read for claims
    candidates = market.segments_before_flags(segments, bars_df) or segments
    claims, llm_used = _claims_with_novelty(fixture, candidates, release, prior)
    moments = rank_moments(claims, segments, bars_df)
    call_end = meta.call_start + timedelta(milliseconds=max(s.t1_ms for s in segments))
    return CallOutput(call=meta, call_end=call_end, segments=segments, bars=market.to_bars(bars_df),
                      claims=claims, moments=moments, llm_used=llm_used,
                      candidate_seg_ids=[s.seg_id for s in candidates])


def main(argv=None):
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out"))
    args = ap.parse_args(argv)
    result = run(args.fixture)
    args.out.mkdir(parents=True, exist_ok=True)
    call_id = f"{result.call.ticker}_{result.call.quarter}".replace(" ", "")
    path = args.out / f"{call_id}.json"
    path.write_text(result.model_dump_json(indent=1))
    print(f"{call_id}: {len(result.segments)} segments, {len(result.bars)} bars, {sum(b.flagged for b in result.bars)} "
          f"flagged minutes, {len(result.candidate_seg_ids)} candidate turns, {len(result.claims)} claims, "
          f"llm={'claude' if result.llm_used else 'rules'}")
    for m in result.moments:
        print(f"  #{m.rank} {m.ts:%H:%M} {m.speaker}: {m.quote[:80]}  adj5m={m.adj_ret_5m:+.2%} vol={m.vol_mult:.1f}x "
              f"novel={m.novel} {m.confidence} score={m.score:.4f}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
