from datetime import datetime, timedelta

import httpx
import numpy as np
import pandas as pd
import pytest
from conftest import ET, T0, make_bars

from callmoves import market


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


def test_forward_window_and_nearest_flag():
    df = market.flag_abnormal(market.adjusted_returns(make_bars(stock_bump={42: 0.01}, vol_bump={42: 5})))
    ts = T0 + timedelta(minutes=40)
    w = market.forward_window(df, ts, minutes=5)
    assert 0.008 < w["adj_ret"] < 0.013 and w["vol_mult"] > 3
    assert market.minutes_to_nearest_flag(df, ts) == 2.0
    assert market.minutes_to_nearest_flag(df, T0 + timedelta(minutes=80)) == float("inf")


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
    window = wide[(wide["ts"] >= CALL_START - timedelta(minutes=180)) &
                  (wide["ts"] <= CALL_START + timedelta(minutes=60))]
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
    df = market.adjusted_returns(
        market.add_session_edges(pd.DataFrame(columns=["ts"]), _adr_bars(), CALL_START, CALL_END))
    r = market.open_reaction(df, CALL_START, CALL_END)
    assert r["from_ts"] == PRIOR_CLOSE + timedelta(minutes=59)
    assert r["to_ts"] == NEXT_OPEN + timedelta(minutes=market.OPEN_WINDOW_MIN)
    assert abs(r["ret"] - 0.03) < 1e-9 and abs(r["etf_ret"] - 0.005) < 1e-9 and abs(r["adj_ret"] - 0.025) < 1e-9


def test_open_reaction_is_none_when_the_stock_traded_during_the_call():
    df = market.adjusted_returns(make_bars())
    assert market.open_reaction(df, T0 + timedelta(minutes=10), T0 + timedelta(minutes=70)) is None


def test_zero_volume_baseline_gives_neutral_vol_mult_and_flags_on_z_alone():
    df = make_bars(stock_bump={40: 0.012})
    df["vol"] = 0.0
    out = market.flag_abnormal(market.adjusted_returns(df))
    assert (out["vol_mult"] == 1.0).all()
    assert bool(out.loc[40, "flagged"]) and out["flagged"].sum() == 1
    assert abs(out.loc[40, "z"]) >= market.Z_THRESHOLD and out.loc[40, "vol_mult"] < market.VOL_THRESHOLD
    regular = market.flag_abnormal(market.adjusted_returns(make_bars(stock_bump={40: 0.012}, vol_bump={40: 5})))
    assert abs(regular.loc[40, "vol_mult"] - 5.0) < 1e-9 and regular.loc[40, "vol_mult"] >= market.VOL_THRESHOLD


def test_fetch_bars_without_a_massive_key_raises(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="POLYGON_API_KEY"):
        market.fetch_bars("WFC", T0, T0 + timedelta(minutes=5))


def test_massive_bars_wait_out_a_429_follow_next_url_and_keep_the_key_out_of_urls(monkeypatch):
    t0 = int(T0.timestamp() * 1000)
    bar = lambda i, c, v: {"t": t0 + i * 60000, "o": c, "h": c, "l": c, "c": c, "v": v, "n": 1}
    pages = [httpx.Response(429, headers={"Retry-After": "2"}),
             httpx.Response(200, json={"status": "OK", "results": [bar(0, 80.5, 100), bar(1, 80.6, 200)],
                                       "next_url": "https://api.massive.com/v2/aggs/next?cursor=abc"}),
             httpx.Response(200, json={"status": "OK", "results": [bar(2, 80.7, 300)]})]
    calls, sleeps = [], []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params, headers))
        r = pages[len(calls) - 1]
        r.request = httpx.Request("GET", url, params=params)
        return r

    monkeypatch.setattr(market.httpx, "get", fake_get)
    monkeypatch.setattr(market.time, "sleep", sleeps.append)
    df = market.fetch_polygon_bars("WFC", T0, T0 + timedelta(minutes=5), api_key="k")
    assert sleeps == [2.0] and len(calls) == 3
    assert calls[0][0].startswith(f"{market.MASSIVE_URL}/v2/aggs/ticker/WFC/range/1/minute/{t0}/")
    assert calls[0][1] == calls[1][1] and calls[1][1]["limit"] == 50000
    assert calls[2] == ("https://api.massive.com/v2/aggs/next?cursor=abc", None, {"Authorization": "Bearer k"})
    assert all("apiKey" not in u and "apiKey" not in (q or {}) for u, q, _ in calls)
    assert list(df.columns) == ["ts", "px", "vol"] and str(df["ts"].dt.tz) == "UTC"
    assert df["ts"].iloc[0] == T0 and list(df["px"]) == [80.5, 80.6, 80.7] and list(df["vol"]) == [100, 200, 300]


def test_join_keeps_stock_minutes_the_etf_skipped_with_its_last_price():
    ts = pd.date_range("2026-11-03 22:00", periods=4, freq="1min", tz="UTC")
    stock = pd.DataFrame({"ts": ts, "px": [10.0, 11.0, 12.0, 13.0], "vol": [5.0] * 4})
    etf = pd.DataFrame({"ts": ts[[1, 3]], "px": [100.0, 101.0], "vol": [1.0, 1.0]})
    out = market.join_stock_etf(stock, etf)
    assert list(out["ts"]) == list(ts[1:])                 # minute 0 precedes the first ETF print
    assert list(out["etf_px"]) == [100.0, 100.0, 101.0]    # minute 2 carries the ETF's last price


def test_open_volume_is_not_flagged_against_the_pre_market_baseline_but_a_later_spike_is():
    ts = pd.date_range("2026-07-14 13:00", periods=60, freq="1min", tz="UTC")   # 9:00 to 9:59 ET
    df = make_bars(n=60, stock_bump={30: 0.004, 50: 0.01}, vol_bump={50: 8})
    df["ts"] = ts
    df.loc[:29, "vol"] = 200.0                                                   # thin pre-market
    out = market.flag_abnormal(market.adjusted_returns(df))
    assert out.loc[30, "vol_mult"] == 1.0 and not out.loc[30, "flagged"]          # 9:30 open, no session baseline yet
    assert out.loc[50, "flagged"]
