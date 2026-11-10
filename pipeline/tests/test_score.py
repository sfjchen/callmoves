from datetime import timedelta

from conftest import T0, make_bars

from callmoves import market
from callmoves.models import Claim, Segment
from callmoves.score import rank_moments, timing_weight


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


def test_candidates_are_turns_in_the_minutes_before_a_flag_and_confidence_grades():
    from callmoves.score import confidence
    bars = market.flag_abnormal(market.adjusted_returns(make_bars(stock_bump={41: 0.012}, vol_bump={41: 5})))
    segs = [Segment(seg_id=i, t0_ms=m * 60_000, t1_ms=0, speaker="CFO", text="x", ts=T0 + timedelta(minutes=m)) for i, m in enumerate((10, 36, 38, 41, 42))]
    assert [s.seg_id for s in market.segments_before_flags(segs, bars)] == [1, 2, 3]
    assert confidence(True, 0.012, 5.0, 1.0) == "high"
    assert confidence(True, 0.003, 1.2, 4.0) == "medium"
    assert confidence(False, 0.012, 5.0, 1.0) == "low"


def test_open_reaction_gives_every_claim_the_same_move_ranked_by_novelty_and_kind_at_low_confidence():
    bars = market.adjusted_returns(make_bars())
    segs = [Segment(seg_id=i, t0_ms=m * 60_000, t1_ms=0, speaker="CFO", text="x", ts=T0 + timedelta(minutes=m))
            for i, m in enumerate((5, 20, 35))]
    claims = [
        Claim(claim_id=0, seg_id=0, speaker="CFO", quote="x", metric="revenue", kind="result", ts=segs[0].ts, novel=False),
        Claim(claim_id=1, seg_id=1, speaker="CFO", quote="x", metric="guidance", kind="guidance", ts=segs[1].ts, novel=True),
        Claim(claim_id=2, seg_id=2, speaker="CFO", quote="x", metric="tone", kind="qualitative", ts=segs[2].ts, novel=True),
    ]
    reaction = {"adj_ret": -0.04, "ret": -0.05, "etf_ret": -0.01, "vol_mult": 6.0}
    moments = rank_moments(claims, segs, bars, reaction=reaction)
    assert [m.claim_id for m in moments] == [1, 2, 0]
    assert all(m.adj_ret_5m == -0.04 and m.nearest_flag_min == -1.0 and m.confidence == "low" for m in moments)


def test_one_moment_per_price_event_and_claims_inside_an_event_are_pre_move():
    bars = market.adjusted_returns(make_bars(stock_bump={41: 0.01, 42: 0.01, 43: 0.01, 60: -0.01}))
    bars["flagged"] = bars.index.isin([41, 42, 43, 60])          # 1 event at 41-43, another at 60
    mins = (40, 39, 42, 58)
    segs = [Segment(seg_id=i, t0_ms=m * 60_000, t1_ms=0, speaker="CFO", text="x", ts=T0 + timedelta(minutes=m))
            for i, m in enumerate(mins)]
    claims = [Claim(claim_id=i, seg_id=i, speaker="CFO", quote="x", metric="m", kind="guidance", ts=s.ts, novel=True)
              for i, s in enumerate(segs)]
    moments = rank_moments(claims, segs, bars)
    assert len(moments) == 2 and {m.claim_id for m in moments} == {0, 3}   # 39 and 42 lose event 1 to 40
    assert not any(m.pre_move for m in moments)
    late = rank_moments(claims[2:3], segs, bars)[0]                        # said at 42, the event began at 41
    assert late.pre_move
