from datetime import timedelta

from conftest import T0, claim, make_bars, segs

from callmoves import market
from callmoves.score import confidence, rank_moments, timing_weight, with_placebo


def test_timing_weight_decays_and_floors():
    assert timing_weight(0) == 1.0
    assert timing_weight(3) < timing_weight(1)
    assert timing_weight(float("inf")) == 0.1


def test_novel_claim_before_a_move_outranks_stale_claim_in_quiet_minute():
    bars = market.flag_abnormal(market.adjusted_returns(make_bars(stock_bump={41: 0.012}, vol_bump={41: 5})))
    ss = segs([(40, "CFO", "a"), (10, "CFO", "b")])
    claims = [claim(0, ss[0], metric="guidance"), claim(1, ss[1], metric="revenue", kind="result", novel=False)]
    moments = rank_moments(claims, ss, bars)
    assert [m.claim_id for m in moments] == [0, 1]
    assert moments[0].adj_ret_5m > 0.008 and moments[0].nearest_flag_min == 1.0
    assert moments[0].score > 5 * moments[1].score


def test_candidates_are_turns_in_the_minutes_before_a_flag_and_confidence_grades():
    bars = market.flag_abnormal(market.adjusted_returns(make_bars(stock_bump={41: 0.012}, vol_bump={41: 5})))
    ss = segs([(m, "CFO", "x") for m in (10, 36, 38, 41, 42)])
    assert [s.seg_id for s in market.segments_before_flags(ss, bars)] == [1, 2, 3]
    assert confidence(True, 0.012, 5.0, 1.0) == "high"
    assert confidence(True, 0.003, 1.2, 4.0) == "medium"
    assert confidence(False, 0.012, 5.0, 1.0) == "low"


def test_open_reaction_gives_every_claim_the_same_move_ranked_by_novelty_and_kind_at_low_confidence():
    bars = market.adjusted_returns(make_bars())
    ss = segs([(m, "CFO", "x") for m in (5, 20, 35)])
    claims = [claim(0, ss[0], metric="revenue", kind="result", novel=False), claim(1, ss[1], metric="guidance"),
              claim(2, ss[2], metric="tone", kind="qualitative")]
    reaction = {"adj_ret": -0.04, "ret": -0.05, "etf_ret": -0.01, "vol_mult": 6.0}
    moments = rank_moments(claims, ss, bars, reaction=reaction)
    assert [m.claim_id for m in moments] == [1, 2, 0]
    assert all(m.adj_ret_5m == -0.04 and m.nearest_flag_min == -1.0 and m.confidence == "low" for m in moments)


def test_one_moment_per_price_event_and_claims_inside_an_event_are_pre_move():
    bars = market.adjusted_returns(make_bars(stock_bump={41: 0.01, 42: 0.01, 43: 0.01, 60: -0.01}))
    bars["flagged"] = bars.index.isin([41, 42, 43, 60])          # 1 event at 41-43, another at 60
    ss = segs([(m, "CFO", "x") for m in (40, 39, 42, 58)])
    claims = [claim(i, s) for i, s in enumerate(ss)]
    moments = rank_moments(claims, ss, bars)
    assert len(moments) == 2 and {m.claim_id for m in moments} == {0, 3}   # 39 and 42 lose event 1 to 40
    assert not any(m.pre_move for m in moments)
    late = rank_moments(claims[2:3], ss, bars)[0]                        # said at 42, the event began at 41
    assert late.pre_move


def test_qa_moments_carry_section_and_the_analyst_question_before_them():
    bars = market.adjusted_returns(make_bars(stock_bump={21: 0.01, 51: 0.01}))
    bars["flagged"] = bars.index.isin([21, 51])
    rows = [(20, "Management", "We expect margins near 30%."), (45, "Operator", "Our first question comes from Ann."),
            (46, "Analyst", "Can margins hold next year?"), (50, "Management", "Margins will expand next year.")]
    ss = segs(rows)
    by_claim = {m.claim_id: m for m in rank_moments([claim(i, ss[i]) for i in (0, 3)], ss, bars)}
    assert by_claim[0].section == "prepared" and by_claim[0].question is None
    assert by_claim[3].section == "qa" and by_claim[3].question == "Can margins hold next year?"


def test_claims_whose_window_touches_the_opening_auction_are_skipped():
    bars = make_bars(stock_bump={6: -0.02, 11: -0.02, 70: -0.006}, vol_bump={6: 5, 11: 5, 70: 5})
    bars["ts"] -= timedelta(minutes=35)                                  # bar 0 is 9:25 ET, bar 70 is 10:35
    bars = market.flag_abnormal(market.adjusted_returns(bars))
    ss = segs([(m, "CFO", "x") for m in (-31, -26, 34)])               # 9:29 and 9:34 touch the open, 10:34 not
    claims = [claim(i, s) for i, s in enumerate(ss)]
    assert rank_moments(claims[:2], ss, bars) == []
    assert [m.claim_id for m in rank_moments(claims, ss, bars)] == [2]


def test_claims_with_no_flag_ahead_keep_1_per_5_minute_window():
    bars = market.flag_abnormal(market.adjusted_returns(make_bars()))
    ss = segs([(m, "CFO", "x") for m in (20, 21, 23, 30)])
    moments = rank_moments([claim(i, s) for i, s in enumerate(ss)], ss, bars)
    assert all(m.nearest_flag_min == -1.0 for m in moments)
    assert len(moments) == 2 and abs(moments[0].t0_ms - moments[1].t0_ms) >= 5 * 60_000


def test_spillover_tags():
    from callmoves.score import spillover
    assert spillover(-0.01, -0.006) == "sector"
    assert spillover(-0.01, 0.0005) == "company"
    assert spillover(-0.01, 0.004) == "company"     # peers went the other way
    assert spillover(-0.01, -0.003) == "mixed"
    assert spillover(-0.01, None) is None
    assert spillover(0.0009, 0.0013) is None        # the stock barely moved, nothing to attribute


def test_placebo_counts_the_other_5_minute_windows_that_moved_as_much():
    bars = market.adjusted_returns(make_bars(stock_bump={41: 0.012, 71: 0.03, 91: -0.03, 101: 0.012}))
    bars["flagged"] = bars.index.isin([41])
    ss = segs([(40, "CFO", "x")])
    m = with_placebo(rank_moments([claim(0, ss[0])], ss, bars), bars, T0, T0 + timedelta(minutes=100))[0]
    # windows start at minute 40 + 5k: 0, 5 ... 100 minus the moment's own = 20, the ones from 70, 90 and 100 hold a
    # bump at least as big (91 is negative), none of them overlaps the moment's window
    assert m.adj_ret_5m > 0.01 and (m.placebo_k, m.placebo_n) == (3, 20)
    assert with_placebo([m], bars, T0 + timedelta(minutes=30), T0 + timedelta(minutes=50))[0].placebo_n is None
    gap = bars.drop(index=range(95, 100)).reset_index(drop=True)       # no trade 95 to 99: that window is left out
    assert with_placebo([m], gap, T0, T0 + timedelta(minutes=100))[0].placebo_n == 19
    neg = m.model_copy(update={"adj_ret_5m": -m.adj_ret_5m})            # the size of the move counts, not its sign
    assert with_placebo([neg], bars, T0, T0 + timedelta(minutes=100))[0].placebo_k == 3
    top = max(abs(market.forward_window(bars, T0 + timedelta(minutes=t))["adj_ret"]) for t in (70, 90))
    tie = m.model_copy(update={"adj_ret_5m": top})                       # an equal move counts as moving as much
    assert with_placebo([tie], bars, T0, T0 + timedelta(minutes=100))[0].placebo_k == 1
    early = make_bars(stock_bump={41: 0.012, 71: 0.03})
    early["ts"] -= timedelta(minutes=40)                                # bar 0 is 9:20 ET, the moment is at 10:00
    early = market.adjusted_returns(early)
    e = with_placebo([m.model_copy(update={"ts": m.ts - timedelta(minutes=40)})], early, T0 - timedelta(minutes=40),
                     T0 + timedelta(minutes=60))[0]
    # pre-market 9:20 and 9:25 and the 9:30 auction window are left out: 9:35 to 11:00 is 18 windows minus the moment
    assert (e.placebo_k, e.placebo_n) == (1, 17)