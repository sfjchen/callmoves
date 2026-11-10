"""Step 7: score = novelty x size of the move over the next 5 minutes x volume spike x closeness to a flagged minute."""
from __future__ import annotations

import math

import pandas as pd

from .market import flag_events, forward_window, minutes_to_nearest_flag
from .models import Claim, Moment, Segment

NOVEL_WEIGHT = 1.0
STALE_WEIGHT = 0.25
TIMING_SCALE_MIN = 3.0
VOL_CAP = 10.0   # a single block print without a price move should not dominate the score
CONSENSUS_GAP = 0.01   # a figure this far from consensus moves the stock even when the release printed it first
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


def market_relevant(claim: Claim) -> bool:
    """New information, or an EPS or revenue figure that beats or misses consensus by more than 1%."""
    return bool(claim.novel) or abs(claim.vs_consensus_pct or 0.0) > CONSENSUS_GAP


def score_claim(claim: Claim, bars: pd.DataFrame, reaction: dict | None = None) -> tuple[float, dict, float]:
    """With an open reaction (stock closed during the call) every claim shares that one move and has no timing."""
    w = reaction or forward_window(bars, claim.ts)
    dist = float("inf") if reaction else minutes_to_nearest_flag(bars, claim.ts)
    novelty = NOVEL_WEIGHT if market_relevant(claim) else STALE_WEIGHT
    score = (novelty * KIND_WEIGHT.get(claim.kind, 0.5) * abs(w["adj_ret"])
             * min(VOL_CAP, max(1.0, w["vol_mult"])) * timing_weight(dist))
    return score, w, dist


def rank_moments(claims: list[Claim], segments: list[Segment], bars: pd.DataFrame, top_n: int = 5,
                 reaction: dict | None = None) -> list[Moment]:
    """Top claims, at most 1 per price event: a claim belongs to the event of the first flag in its 5-minute
    window, and the best-scoring claim wins it. A claim spoken after its event's first flagged minute is marked
    pre_move, since prices may have moved first and shaped what was said. Claims with no flag ahead (and all
    claims under an open reaction, which share 1 move) dedupe by minute instead."""
    seg_by_id = {s.seg_id: s for s in segments}
    ev = flag_events(bars) if not reaction else None
    scored = []
    for c in claims:
        score, w, dist = score_claim(c, bars, reaction)
        scored.append((score, c, w, dist))
    scored.sort(key=lambda x: x[0], reverse=True)
    moments = []
    seen: set = set()
    for score, c, w, dist in scored:
        minute = c.ts.replace(second=0, microsecond=0)
        key, pre_move = ("minute", minute), False
        if ev is not None and not math.isinf(dist):
            hit = ev[ev["ts"] >= c.ts].iloc[0]
            key, pre_move = ("event", int(hit["event"])), bool(hit["start"] < minute)
        if key in seen:
            continue
        seen.add(key)
        moments.append(Moment(
            rank=len(moments) + 1, claim_id=c.claim_id, seg_id=c.seg_id, ts=c.ts, t0_ms=seg_by_id[c.seg_id].time_of(c.quote),
            speaker=c.speaker, quote=c.quote, metric=c.metric, kind=c.kind, novel=bool(c.novel),
            matched_doc=c.matched_doc, matched_text=c.matched_text, adj_ret_5m=w["adj_ret"], ret_5m=w["ret"],
            etf_ret_5m=w["etf_ret"], vol_mult=w["vol_mult"], nearest_flag_min=(dist if not math.isinf(dist) else -1.0),
            vs_consensus_pct=c.vs_consensus_pct, consensus_role=c.consensus_role, pre_move=pre_move,
            score=score,
            confidence="low" if reaction else confidence(market_relevant(c), w["adj_ret"], w["vol_mult"], dist),
        ))
        if len(moments) == top_n:
            break
    return moments
