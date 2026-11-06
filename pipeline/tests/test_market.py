from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings, strategies as st

from callmoves import market
from conftest import ET, T0, make_bars


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


def _session(first: datetime, n: int, px: float, etf_px: float) -> pd.DataFrame:
    return pd.DataFrame({"ts": [first + timedelta(minutes=i) for i in range(n)],
                         "px": px, "etf_px": etf_px, "vol": 10000.0})


# an OTC ADR that only trades 9:30-16:00 ET, with a call held overnight from Hong Kong
PRIOR_CLOSE = datetime(2026, 11, 11, 15, 0, tzinfo=ET)
NEXT_OPEN = datetime(2026, 11, 12, 9, 30, tzinfo=ET)
CALL_START = datetime(2026, 11, 12, 7, 0, tzinfo=ET)
CALL_END = CALL_START + timedelta(minutes=60)


def _adr_bars(gap=0.03, etf_gap=0.005):
    return pd.concat([_session(PRIOR_CLOSE, 60, 100.0, 50.0),
                      _session(NEXT_OPEN, 60, 100.0 * (1 + gap), 50.0 * (1 + etf_gap))], ignore_index=True)


def test_session_edges_add_prior_close_and_next_open_for_a_call_with_no_trades():
    wide = _adr_bars()
    window = wide[(wide["ts"] >= CALL_START - timedelta(minutes=180)) & (wide["ts"] <= CALL_START + timedelta(minutes=60))]
    assert window.empty
    df = market.add_session_edges(window, wide, CALL_START, CALL_END)
    assert len(df) == market.SESSION_TAIL_MIN + market.OPEN_WINDOW_MIN + 1
    assert df["ts"].iloc[market.SESSION_TAIL_MIN - 1] == PRIOR_CLOSE + timedelta(minutes=59)
    assert df["ts"].iloc[market.SESSION_TAIL_MIN] == NEXT_OPEN
    with pytest.raises(RuntimeError, match="next US session"):
        market.add_session_edges(window, wide[wide["ts"] < NEXT_OPEN], CALL_START, CALL_END)


def test_session_edges_keep_a_window_that_starts_at_the_open_and_prepend_the_prior_close():
    wide = _adr_bars()
    start, end = datetime(2026, 11, 12, 9, 0, tzinfo=ET), datetime(2026, 11, 12, 10, 0, tzinfo=ET)
    window = wide[(wide["ts"] >= NEXT_OPEN) & (wide["ts"] <= end)]
    df = market.add_session_edges(window, wide, start, end)
    assert len(df) == market.SESSION_TAIL_MIN + len(window)
    gap_bar = market.adjusted_returns(df).iloc[market.SESSION_TAIL_MIN]
    assert gap_bar["ts"] == NEXT_OPEN and abs(gap_bar["adj_ret"] - 0.025) < 1e-9


def test_open_reaction_is_the_sector_adjusted_move_to_the_next_open():
    df = market.adjusted_returns(market.add_session_edges(pd.DataFrame(columns=["ts"]), _adr_bars(), CALL_START, CALL_END))
    r = market.open_reaction(df, CALL_START, CALL_END)
    assert r["from_ts"] == PRIOR_CLOSE + timedelta(minutes=59)
    assert r["to_ts"] == NEXT_OPEN + timedelta(minutes=market.OPEN_WINDOW_MIN)
    assert abs(r["ret"] - 0.03) < 1e-9 and abs(r["etf_ret"] - 0.005) < 1e-9 and abs(r["adj_ret"] - 0.025) < 1e-9


def test_open_reaction_is_none_when_the_stock_traded_during_the_call():
    df = market.adjusted_returns(make_bars())
    assert market.open_reaction(df, T0 + timedelta(minutes=10), T0 + timedelta(minutes=70)) is None
