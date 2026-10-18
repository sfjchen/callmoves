from datetime import timedelta

from callmoves import market
from callmoves.models import CallMeta, Claim, Segment
from callmoves.score import rank_moments, timing_weight
from conftest import T0, make_bars


def test_timing_weight_decays_and_floors():
    assert timing_weight(0) == 1.0
    assert timing_weight(3) < timing_weight(1)
    assert timing_weight(float("inf")) == 0.1


def test_novel_claim_before_a_move_outranks_stale_claim_in_quiet_minute():
    bars = market.flag_abnormal(market.adjusted_returns(make_bars(stock_bump={41: 0.012}, vol_bump={41: 5})))
    segs = [Segment(seg_id=0, t0_ms=40 * 60_000, t1_ms=0, speaker="CFO", text="a", ts=T0 + timedelta(minutes=40)),
            Segment(seg_id=1, t0_ms=10 * 60_000, t1_ms=0, speaker="CFO", text="b", ts=T0 + timedelta(minutes=10))]
    claims = [
        Claim(claim_id=0, seg_id=0, speaker="CFO", quote="a", metric="guidance", kind="guidance", ts=segs[0].ts, novel=True),
        Claim(claim_id=1, seg_id=1, speaker="CFO", quote="b", metric="revenue", kind="result", ts=segs[1].ts, novel=False),
    ]
    moments = rank_moments(claims, segs, bars)
    assert [m.claim_id for m in moments] == [0, 1]
    assert moments[0].adj_ret_5m > 0.008 and moments[0].nearest_flag_min == 1.0
    assert moments[0].score > 5 * moments[1].score
