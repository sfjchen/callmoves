"""Step 7: score = novelty x size of the move over the next 5 minutes x volume spike x closeness to a flagged minute."""
from __future__ import annotations

import math

import pandas as pd

from .market import forward_window, minutes_to_nearest_flag
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


def confidence(novel: bool, adj_ret: float, vol_mult: float, minutes_to_flag: float) -> str:
    """How much to trust the association. Attribution is uncertain by nature, so the top label is 'high', never 'certain'."""
    if novel and abs(adj_ret) >= 0.004 and vol_mult >= 3 and minutes_to_flag <= 2:
        return "high"
    if novel and abs(adj_ret) >= 0.002 and minutes_to_flag <= 5:
        return "medium"
    return "low"


def score_claim(claim: Claim, bars: pd.DataFrame) -> tuple[float, dict, float]:
    w = forward_window(bars, claim.ts)
    dist = minutes_to_nearest_flag(bars, claim.ts)
    novelty = NOVEL_WEIGHT if claim.novel else STALE_WEIGHT
    score = (novelty * KIND_WEIGHT.get(claim.kind, 0.5) * abs(w["adj_ret"])
             * min(VOL_CAP, max(1.0, w["vol_mult"])) * timing_weight(dist))
    return score, w, dist


def rank_moments(claims: list[Claim], segments: list[Segment], bars: pd.DataFrame, top_n: int = 5) -> list[Moment]:
    seg_by_id = {s.seg_id: s for s in segments}
    scored = []
    for c in claims:
        score, w, dist = score_claim(c, bars)
        scored.append((score, c, w, dist))
    scored.sort(key=lambda x: x[0], reverse=True)
    moments = []
    seen_minutes: set = set()
    for score, c, w, dist in scored:
        minute = c.ts.replace(second=0, microsecond=0)
        if minute in seen_minutes:      # one moment per minute, the best-scoring claim wins
            continue
        seen_minutes.add(minute)
        moments.append(Moment(
            rank=len(moments) + 1, claim_id=c.claim_id, seg_id=c.seg_id, ts=c.ts, t0_ms=seg_by_id[c.seg_id].time_of(c.quote),
            speaker=c.speaker, quote=c.quote, metric=c.metric, kind=c.kind, novel=bool(c.novel),
            matched_doc=c.matched_doc, matched_text=c.matched_text, adj_ret_5m=w["adj_ret"], ret_5m=w["ret"],
            etf_ret_5m=w["etf_ret"], vol_mult=w["vol_mult"], nearest_flag_min=(dist if not math.isinf(dist) else -1.0),
            score=score, confidence=confidence(bool(c.novel), w["adj_ret"], w["vol_mult"], dist),
        ))
        if len(moments) == top_n:
            break
    return moments
