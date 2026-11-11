"""Scores claims and ranks them into moments: score = novelty x size of the move over the next 5 minutes x volume
spike x closeness to a flagged minute, at most 1 moment per price event."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import pandas as pd

from .market import (
    ET,
    WINDOW_MIN,
    flag_events,
    forward_window,
    minute_of,
    minutes_to_nearest_flag,
)
from .models import Claim, Moment, Segment
from .transcript import qa_start_ms

STALE_WEIGHT = 0.25
TIMING_SCALE_MIN = 3.0
VOL_CAP = 10.0   # a single block print without a price move should not dominate the score
CONSENSUS_GAP = 0.01   # a figure this far from consensus moves the stock even when the release printed it first
KIND_WEIGHT = {"guidance": 1.0, "result": 0.8, "qualitative": 0.5}   # numbers are checkable, tone is not
QUESTION_CHARS = 400   # analyst questions shown on a card are cut to this length
OPEN_MIN = 9 * 60 + 30   # 9:30 ET, the regular-session open
OPEN_SETTLE_MIN = 5      # the opening auction prices everything since the prior close, so these minutes are not
                         # tied to 1 statement


def timing_weight(minutes_to_flag: float) -> float:
    """Returns 1 at a flagged minute, decaying exponentially with distance, 0.1 when no flag follows."""
    if math.isinf(minutes_to_flag):
        return 0.1
    return math.exp(-minutes_to_flag / TIMING_SCALE_MIN)


def confidence(novel: bool, adj_ret: float, vol_mult: float, minutes_to_flag: float) -> str:
    """How much to trust the association. Attribution is uncertain by nature, so the top label is 'high', never
    'certain'."""
    if novel and abs(adj_ret) >= 0.004 and vol_mult >= 3 and minutes_to_flag <= 2:
        return "high"
    if novel and abs(adj_ret) >= 0.002 and minutes_to_flag <= 5:
        return "medium"
    return "low"


SPILL_MIN = 0.0015   # a peer move under 0.15% is flat


def spillover(ret: float, peer_ret: float | None) -> str | None:
    """Did peers move with the stock: "sector" when the peer index went the same way by at least half the stock's
    move, "company" when it stayed flat or went the other way, "mixed" in between, None without peers or when the
    stock itself barely moved."""
    if peer_ret is None or abs(ret) < SPILL_MIN:
        return None
    if peer_ret * ret > 0 and abs(peer_ret) >= max(SPILL_MIN, abs(ret) / 2):
        return "sector"
    if abs(peer_ret) < SPILL_MIN or peer_ret * ret < 0 or abs(peer_ret) <= abs(ret) / 4:
        return "company"
    return "mixed"


def market_relevant(claim: Claim) -> bool:
    """New information, or an EPS or revenue figure that beats or misses consensus by more than 1%."""
    return bool(claim.novel) or abs(claim.vs_consensus_pct or 0.0) > CONSENSUS_GAP


def touches_open(ts: datetime) -> bool:
    """True when a 5-minute window starting at ts touches the first OPEN_SETTLE_MIN minutes of the regular session,
    where the opening auction prices everything since the prior close."""
    et = ts.astimezone(ET)
    return OPEN_MIN - WINDOW_MIN <= et.hour * 60 + et.minute < OPEN_MIN + OPEN_SETTLE_MIN


def rank_moments(claims: list[Claim], segments: list[Segment], bars: pd.DataFrame, top_n: int = 5,
                 reaction: dict | None = None) -> list[Moment]:
    """Top claims, at most 1 per price event: a claim belongs to the event of the first flag in its 5-minute
    window, and the best-scoring claim wins it. A claim spoken after its event's first flagged minute is marked
    pre_move, since prices may have moved first and shaped what was said. Claims with no flag ahead keep 1 per
    5-minute window, since overlapping windows measure the same move, and claims under an open reaction (which all
    share 1 move) dedupe by minute. Claims whose 5-minute window
    touches the first OPEN_SETTLE_MIN minutes of the regular session are skipped: a 9:30 call's opening remarks
    would otherwise win on the opening auction's move. A Q&A moment carries the latest analyst question before it,
    since the market may be reacting to the exchange, not the answer alone."""
    seg_by_id = {s.seg_id: s for s in segments}
    qa_ms = qa_start_ms(segments)
    ev = flag_events(bars) if not reaction else None
    scored = []
    for c in claims:
        # with an open reaction (stock closed during the call) every claim shares that one move and has no timing
        w = reaction or forward_window(bars, c.ts)
        dist = float("inf") if reaction else minutes_to_nearest_flag(bars, c.ts)
        novelty = 1.0 if market_relevant(c) else STALE_WEIGHT
        scored.append((novelty * KIND_WEIGHT[c.kind] * abs(w["adj_ret"]) * min(VOL_CAP, max(1.0, w["vol_mult"]))
                       * timing_weight(dist), c, w, dist))
    scored.sort(key=lambda x: x[0], reverse=True)
    moments = []
    seen: set = set()
    for score, c, w, dist in scored:
        if not reaction and touches_open(c.ts):
            continue
        minute = minute_of(c.ts)
        key, pre_move = ("minute", minute), False
        if ev is not None and not math.isinf(dist):
            hit = ev[ev["ts"] >= minute].iloc[0]
            key, pre_move = ("event", int(hit["event"])), bool(hit["start"] < minute)
        elif not reaction:
            key = ("window", c.ts)
            if any(k[0] == "window" and abs(c.ts - k[1]) < timedelta(minutes=WINDOW_MIN) for k in seen):
                continue
        if key in seen:
            continue
        seen.add(key)
        seg = seg_by_id[c.seg_id]
        in_qa = qa_ms is not None and seg.t0_ms >= qa_ms
        asked = [s for s in segments if in_qa and qa_ms <= s.t0_ms < seg.t0_ms and "Analyst" in s.speaker]
        moments.append(Moment(
            rank=len(moments) + 1, claim_id=c.claim_id, seg_id=c.seg_id, ts=c.ts, t0_ms=seg.time_of(c.quote),
            speaker=c.speaker, quote=c.quote, metric=c.metric, kind=c.kind, novel=bool(c.novel),
            matched_doc=c.matched_doc, matched_text=c.matched_text, adj_ret_5m=w["adj_ret"], ret_5m=w["ret"],
            etf_ret_5m=w["etf_ret"], bench_ret_5m=w.get("bench_ret"),
            spy_ret_5m=w.get("spy_ret"), peer_ret_5m=w.get("peer_ret"),
            spillover=None if reaction else spillover(w["ret"], w.get("peer_ret")), vol_mult=w["vol_mult"],
            nearest_flag_min=(dist if not math.isinf(dist) else -1.0),
            vs_consensus_pct=c.vs_consensus_pct, consensus_role=c.consensus_role, pre_move=pre_move,
            section="qa" if in_qa else "prepared", question=asked[-1].text[:QUESTION_CHARS] if asked else None,
            score=score,
            confidence="low" if reaction else confidence(market_relevant(c), w["adj_ret"], w["vol_mult"], dist),
        ))
        if len(moments) == top_n:
            break
    return moments


def with_placebo(moments: list[Moment], bars: pd.DataFrame, start: datetime, end: datetime) -> list[Moment]:
    """Returns copies of the moments with placebo_pct: the share of the call's start minutes, from start to end,
    whose |5-minute adjusted return| is at least the moment's. Minutes whose window touches the opening auction are
    left out, as the scorer skips them, and so are minutes within WINDOW_MIN of the moment, whose windows share its
    move, and minutes with no trade in their window, which would count as no move. None when no minute is left."""
    t0, ts = minute_of(start), pd.to_datetime(bars["ts"])
    grid = [t0 + timedelta(minutes=i) for i in range(int((end - t0).total_seconds() // 60) + 1)]
    moves = [(t, abs(forward_window(bars, t)["adj_ret"])) for t in grid if not touches_open(t)
             and ((ts >= t) & (ts < t + timedelta(minutes=WINDOW_MIN))).any()]
    out = []
    for m in moments:
        pool = [r for t, r in moves if abs(t - minute_of(m.ts)) > timedelta(minutes=WINDOW_MIN)]
        pct = sum(r >= abs(m.adj_ret_5m) for r in pool) / len(pool) if pool else None
        out.append(m.model_copy(update={"placebo_pct": pct}))
    return out
