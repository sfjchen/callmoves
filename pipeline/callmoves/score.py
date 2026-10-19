"""Step 7: score = novelty x size of the move over the next 5 minutes x volume spike x closeness to a flagged minute."""
from __future__ import annotations

import math

import pandas as pd

from .market import forward_return, minutes_to_nearest_flag
from .models import Claim, Moment, Segment

NOVEL_WEIGHT = 1.0
STALE_WEIGHT = 0.25
TIMING_SCALE_MIN = 3.0
VOL_CAP = 10.0   # a single block print without a price move should not dominate the score
KIND_WEIGHT = {"guidance": 1.0, "result": 0.8, "qualitative": 0.5}   # numbers are checkable, tone is not


def timing_weight(minutes_to_flag: float) -> float:
    if math.isinf(minutes_to_flag):
        return 0.1
    return math.exp(-minutes_to_flag / TIMING_SCALE_MIN)


def score_claim(claim: Claim, bars: pd.DataFrame) -> tuple[float, float, float, float]:
    ret5, vol_mult = forward_return(bars, claim.ts)
    dist = minutes_to_nearest_flag(bars, claim.ts)
    novelty = NOVEL_WEIGHT if claim.novel else STALE_WEIGHT
    score = novelty * KIND_WEIGHT.get(claim.kind, 0.5) * abs(ret5) * min(VOL_CAP, max(1.0, vol_mult)) * timing_weight(dist)
    return score, ret5, vol_mult, dist


def rank_moments(claims: list[Claim], segments: list[Segment], bars: pd.DataFrame, top_n: int = 5) -> list[Moment]:
    seg_by_id = {s.seg_id: s for s in segments}
    scored = []
    for c in claims:
        score, ret5, vol_mult, dist = score_claim(c, bars)
        scored.append((score, c, ret5, vol_mult, dist))
    scored.sort(key=lambda x: x[0], reverse=True)
    moments = []
    seen_minutes: set = set()
    for score, c, ret5, vol_mult, dist in scored:
        minute = c.ts.replace(second=0, microsecond=0)
        if minute in seen_minutes:      # one moment per minute, the best-scoring claim wins
            continue
        seen_minutes.add(minute)
        moments.append(Moment(
            rank=len(moments) + 1, claim_id=c.claim_id, seg_id=c.seg_id, ts=c.ts, t0_ms=seg_by_id[c.seg_id].time_of(c.quote),
            speaker=c.speaker, quote=c.quote, metric=c.metric, kind=c.kind, novel=bool(c.novel),
            matched_doc=c.matched_doc, matched_text=c.matched_text, adj_ret_5m=ret5, vol_mult=vol_mult,
            nearest_flag_min=(dist if not math.isinf(dist) else -1.0), score=score,
        ))
        if len(moments) == top_n:
            break
    return moments
