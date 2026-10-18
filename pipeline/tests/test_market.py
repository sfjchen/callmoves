from datetime import timedelta

import numpy as np
from hypothesis import given, settings, strategies as st

from callmoves import market
from conftest import T0, make_bars


def test_identical_stock_and_etf_has_zero_adjusted_return():
    df = make_bars()
    df["etf_px"] = df["px"] / 2
    out = market.adjusted_returns(df)
    assert np.allclose(out["adj_ret"], 0) and np.allclose(out["cum_adj_ret"], 0)


def test_cum_adj_ret_is_compounded_product():
    out = market.adjusted_returns(make_bars(stock_bump={30: 0.01, 31: -0.004}))
    expected = (1 + out["adj_ret"]).cumprod() - 1
    assert np.allclose(out["cum_adj_ret"], expected)


def test_planted_jump_with_volume_is_flagged_and_quiet_minutes_are_not():
    df = market.flag_abnormal(market.adjusted_returns(make_bars(stock_bump={40: 0.012}, vol_bump={40: 5})))
    assert bool(df.loc[40, "flagged"])
    assert df["flagged"].sum() == 1


def test_sector_wide_move_is_not_flagged():
    df = make_bars(stock_bump={40: 0.01}, etf_bump={40: 0.01}, vol_bump={40: 2})
    out = market.flag_abnormal(market.adjusted_returns(df))
    assert not bool(out.loc[40, "flagged"])


def test_volume_spike_without_a_move_is_not_flagged():
    out = market.flag_abnormal(market.adjusted_returns(make_bars(vol_bump={40: 8})))
    assert not bool(out.loc[40, "flagged"])


def test_forward_return_and_nearest_flag():
    df = market.flag_abnormal(market.adjusted_returns(make_bars(stock_bump={42: 0.01}, vol_bump={42: 5})))
    ts = T0 + timedelta(minutes=40)
    ret, vol_mult = market.forward_return(df, ts, minutes=5)
    assert 0.008 < ret < 0.013 and vol_mult > 3
    assert market.minutes_to_nearest_flag(df, ts) == 2.0
    assert market.minutes_to_nearest_flag(df, T0 + timedelta(minutes=80)) == float("inf")


@settings(max_examples=50, deadline=None)
@given(st.floats(min_value=-0.05, max_value=0.05), st.integers(min_value=25, max_value=100))
def test_adj_ret_matches_difference_of_simple_returns(bump, i):
    out = market.adjusted_returns(make_bars(stock_bump={i: bump}))
    s = out["px"].pct_change().iloc[i]; e = out["etf_px"].pct_change().iloc[i]
    assert abs(out["adj_ret"].iloc[i] - (s - e)) < 1e-12
