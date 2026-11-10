"""Step 2 and 4: 1-minute bars, sector-adjusted returns, abnormal-minute detection."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from .models import Bar

BASELINE_MIN = 20       # trailing window for the volume baseline and return z-score
Z_THRESHOLD = 2.5       # |z| of the 1-minute adjusted return
VOL_THRESHOLD = 3.0     # volume / trailing median
MIN_ABS_RET = 0.002     # 0.2% in a minute, so a volume spike alone is not a flag
SESSION_TAIL_MIN = 30   # minutes of the prior session kept for a stock that is closed when the call starts
OPEN_WINDOW_MIN = 30    # minutes after the next open read as the reaction to a call held while it was closed
MASSIVE_URL = "https://api.massive.com"   # api.polygon.io still serves the same data
RATE_WAIT_S = 13.0      # free plan: about 5 requests a minute
RATE_RETRIES = 5
EVENT_GAP_MIN = 2       # flagged minutes this close together are 1 price event
EDGE_LOOKUP_DAYS = 5    # how far to look for the prior close and next open: a weekend plus a holiday


def load_bars_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["ts"])
    return df.sort_values("ts").reset_index(drop=True)


def fetch_polygon_bars(ticker: str, start: datetime, end: datetime, api_key: str | None = None) -> pd.DataFrame:
    """1-minute aggregates from Massive (formerly Polygon.io), pre and post market included. Needs POLYGON_API_KEY.
    Follows next_url when a range spans more than 1 page, and waits out a 429 since the free plan allows about
    5 requests a minute. The key goes in a header so it never appears in a URL or an error message.
    Returns ts (UTC), px, vol."""
    key = api_key or os.environ["POLYGON_API_KEY"]
    url = (f"{MASSIVE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/"
           f"{int(start.timestamp() * 1000)}/{int(end.timestamp() * 1000)}")
    params = {"adjusted": "true", "sort": "asc", "limit": 50000}
    rows, waits = [], 0
    while url:
        r = httpx.get(url, params=params, headers={"Authorization": f"Bearer {key}"}, timeout=30)
        if r.status_code == 429 and waits < RATE_RETRIES:
            waits += 1
            time.sleep(float(r.headers.get("Retry-After", RATE_WAIT_S)))
            continue
        r.raise_for_status()
        page = r.json()
        rows += page.get("results", [])
        url, params = page.get("next_url"), None   # next_url carries the cursor and the original query
    return pd.DataFrame({
        "ts": pd.to_datetime([x["t"] for x in rows], unit="ms", utc=True),
        "px": [float(x["c"]) for x in rows],
        "vol": [float(x["v"]) for x in rows],
    })


def join_stock_etf(stock: pd.DataFrame, etf: pd.DataFrame) -> pd.DataFrame:
    """Every stock minute with the ETF's last price at or before it. After the close a sector ETF prints far less
    often than a stock that just reported, so an exact-minute join drops most of the reaction. A minute with no ETF
    trade carries its last price, a sector return of 0. Stock minutes before the first ETF print are dropped."""
    etf_px = etf[["ts", "px"]].rename(columns={"px": "etf_px"}).sort_values("ts")
    df = pd.merge_asof(stock.sort_values("ts"), etf_px, on="ts", direction="backward")
    return df.dropna(subset=["etf_px"]).reset_index(drop=True)


def session_key(ts: pd.Series) -> pd.Series:
    """Labels each bar with its trading day and session (0 pre-market, 1 regular, 2 after hours, US Eastern).
    Baselines restart at each boundary: the 9:30 open prints many times the pre-market volume on any day, so a
    pre-market baseline would flag the open itself as the reaction to whatever was said just before it."""
    et = pd.to_datetime(ts, utc=True).dt.tz_convert("America/New_York")
    mins = et.dt.hour * 60 + et.dt.minute
    return et.dt.strftime("%Y%m%d") + "-" + ((mins >= 570).astype(int) + (mins >= 960).astype(int)).astype(str)


def adjusted_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Per minute: stock return minus ETF return, cumulative since the first bar, volume vs the trailing median of
    the same session."""
    out = df.copy()
    stock_ret = out["px"].pct_change().fillna(0.0)
    etf_ret = out["etf_px"].pct_change().fillna(0.0)
    out["adj_ret"] = stock_ret - etf_ret
    out["cum_adj_ret"] = (1 + out["adj_ret"]).cumprod() - 1
    med = out.groupby(session_key(out["ts"]))["vol"].transform(
        lambda v: v.shift(1).rolling(BASELINE_MIN, min_periods=5).median())
    out["vol_mult"] = (out["vol"] / med.where(med > 0)).fillna(1.0)   # zero or NaN baseline: neutral 1.0
    return out


def flag_abnormal(df: pd.DataFrame) -> pd.DataFrame:
    """Flag a minute when its adjusted return is far outside the trailing baseline, or volume spikes with a real move."""
    out = df.copy()
    roll = out.groupby(session_key(out["ts"]))["adj_ret"].transform
    mu = roll(lambda r: r.shift(1).rolling(BASELINE_MIN, min_periods=5).mean())
    sd = roll(lambda r: r.shift(1).rolling(BASELINE_MIN, min_periods=5).std()).replace(0, np.nan)
    z = ((out["adj_ret"] - mu) / sd).fillna(0.0)
    out["z"] = z
    big_move = out["adj_ret"].abs() >= MIN_ABS_RET
    out["flagged"] = ((z.abs() >= Z_THRESHOLD) | (out["vol_mult"] >= VOL_THRESHOLD)) & big_move
    return out


def to_bars(df: pd.DataFrame) -> list[Bar]:
    return [Bar(**{k: row[k] for k in ("ts", "px", "etf_px", "vol", "adj_ret", "cum_adj_ret", "vol_mult", "flagged")})
            for _, row in df.iterrows()]


def forward_window(df: pd.DataFrame, ts: datetime, minutes: int = 5) -> dict:
    """What happened in the minutes after ts: adjusted, raw stock and ETF returns, and the peak volume multiple."""
    win = df[(df["ts"] >= ts) & (df["ts"] <= ts + timedelta(minutes=minutes))]
    if win.empty:
        return {"adj_ret": 0.0, "ret": 0.0, "etf_ret": 0.0, "vol_mult": 1.0}
    # measured from the last close before the statement, so the move inside the statement's own minute counts,
    # and adjusted = stock minus ETF over those same bars so the 3 numbers on a card reconcile
    before = df[df["ts"] < ts]
    base = before.iloc[-1] if not before.empty else win.iloc[0]
    stock = float(win["px"].iloc[-1] / base["px"] - 1)
    etf = float(win["etf_px"].iloc[-1] / base["etf_px"] - 1)
    return {"adj_ret": stock - etf, "ret": stock, "etf_ret": etf, "vol_mult": float(win["vol_mult"].max())}


def forward_return(df: pd.DataFrame, ts: datetime, minutes: int = 5) -> tuple[float, float]:
    w = forward_window(df, ts, minutes)
    return w["adj_ret"], w["vol_mult"]


def segments_before_flags(segments, df: pd.DataFrame, lookback_min: int = 5):
    """Work backward from the price: keep only the transcript turns spoken in the minutes before an abnormal minute.
    Summarizing the whole call would surface what sounds important, not what the market reacted to."""
    flags = list(df.loc[df["flagged"], "ts"])
    if not flags:
        return []
    keep = []
    for s in segments:
        if s.ts is None:
            continue
        if any(f - timedelta(minutes=lookback_min) <= s.ts <= f for f in flags):
            keep.append(s)
    return keep


def flag_events(df: pd.DataFrame, gap_min: int = EVENT_GAP_MIN) -> pd.DataFrame:
    """Groups flagged minutes into price events: a flag more than `gap_min` minutes after the previous one starts a
    new event. One drop flagged over 4 straight minutes is 1 event, so it yields 1 moment, not 4. Columns: ts,
    event (id), start (the event's first flagged minute)."""
    ev = df.loc[df["flagged"], ["ts"]].reset_index(drop=True)
    ev["event"] = (ev["ts"].diff() > timedelta(minutes=gap_min)).cumsum()
    ev["start"] = ev.groupby("event")["ts"].transform("min")
    return ev


def minutes_to_nearest_flag(df: pd.DataFrame, ts: datetime, lookahead: int = 5) -> float:
    """Distance in minutes to the nearest flagged minute at or after ts (a statement precedes its reaction)."""
    flags = df.loc[df["flagged"] & (df["ts"] >= ts) & (df["ts"] <= ts + timedelta(minutes=lookahead)), "ts"]
    if flags.empty:
        return float("inf")
    return float((flags.iloc[0] - ts).total_seconds() / 60)


def fetch_yfinance_bars(ticker: str, day: datetime) -> pd.DataFrame:
    """1-minute bars for one day including pre and post market. No key, but only the last 30 days."""
    import yfinance as yf

    start = day.date().isoformat()
    end = (day + timedelta(days=1)).date().isoformat()
    df = yf.download(ticker, start=start, end=end, interval="1m", prepost=True, progress=False, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"yfinance returned no 1-minute bars for {ticker} on {start}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    out = pd.DataFrame({"ts": pd.to_datetime(df.index), "px": df["Close"].to_numpy(), "vol": df["Volume"].to_numpy()})
    out = out.dropna(subset=["px"]).reset_index(drop=True)
    out["ts"] = out["ts"].dt.tz_convert("UTC")
    return out


def fetch_bars(ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Massive when POLYGON_API_KEY is set, otherwise yfinance one day at a time. Both return ts (UTC), px, vol."""
    if os.environ.get("POLYGON_API_KEY"):
        df = fetch_polygon_bars(ticker, start, end)
    else:
        days = []
        for i in range((end.date() - start.date()).days + 1):
            try:
                days.append(fetch_yfinance_bars(ticker, start + timedelta(days=i)))
            except RuntimeError:   # weekend, holiday or a day that has not traded yet
                continue
        if not days:
            raise RuntimeError(f"yfinance returned no 1-minute bars for {ticker} between {start} and {end}")
        df = pd.concat(days, ignore_index=True)
    return df[(df["ts"] >= start) & (df["ts"] <= end)].reset_index(drop=True)


def traded_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keeps the bars that carry information: any volume, or a price change. yfinance reports extended-hours
    minutes with moving prices but volume 0, so a flat zero-volume minute drops and a moving one stays."""
    keep = (df["vol"] > 0) | df["px"].ne(df["px"].shift())
    return df[keep].reset_index(drop=True)


def _traded_bars(ticker: str, etf: str, start: datetime, end: datetime) -> pd.DataFrame:
    return traded_only(join_stock_etf(fetch_bars(ticker, start, end), fetch_bars(etf, start, end)))


def add_session_edges(window: pd.DataFrame, wide: pd.DataFrame, call_start: datetime, call_end: datetime,
                      tail_min: int = SESSION_TAIL_MIN, open_min: int = OPEN_WINDOW_MIN) -> pd.DataFrame:
    """For a stock that does not trade when the call starts (an OTC ADR during a Hong Kong call), add the last
    `tail_min` traded minutes before the call so the opening gap shows up as a return, and, if nothing traded
    during the call at all, the first `open_min` minutes of the next session, where the reaction happens."""
    parts = [window]
    if window.empty or window["ts"].iloc[0] > call_start:
        parts.insert(0, wide[wide["ts"] < call_start].tail(tail_min))
    if window[(window["ts"] >= call_start) & (window["ts"] <= call_end)].empty:
        after = wide[wide["ts"] > call_end]
        if after.empty:
            raise RuntimeError(f"no trades after the call ended at {call_end}; rerun once the next US session opens")
        parts.append(after[after["ts"] <= after["ts"].iloc[0] + timedelta(minutes=open_min)])
    df = pd.concat(parts, ignore_index=True).drop_duplicates("ts")
    return df.sort_values("ts").reset_index(drop=True)


def bars_for_call(ticker: str, etf: str, call_start: datetime, call_end: datetime | None = None,
                  before_min: int = 180, after_min: int = 150) -> pd.DataFrame:
    """Stock and ETF bars around the call, joined on the minute. Window covers a pre-market release and the call.
    A stock with no bars at the call start also gets its prior close and, if needed, its next open."""
    call_end = call_end or call_start + timedelta(minutes=60)
    start = call_start - timedelta(minutes=before_min)
    end = max(call_start + timedelta(minutes=after_min), call_end + timedelta(minutes=30))
    window = _traded_bars(ticker, etf, start, end)
    if not window.empty and window["ts"].iloc[0] <= call_start and \
            not window[(window["ts"] >= call_start) & (window["ts"] <= call_end)].empty:
        return window
    now = datetime.now(call_start.tzinfo)
    wide = _traded_bars(ticker, etf, call_start - timedelta(days=EDGE_LOOKUP_DAYS),
                        min(now, call_end + timedelta(days=EDGE_LOOKUP_DAYS)))
    return add_session_edges(window, wide, call_start, call_end)


def open_reaction(df: pd.DataFrame, call_start: datetime, call_end: datetime, minutes: int = OPEN_WINDOW_MIN) -> dict | None:
    """When the stock did not trade during the call, the market's only read is the next open: the sector-adjusted
    move from the last trade before the call to `minutes` after the first trade after it. None if it did trade."""
    if not df[(df["ts"] >= call_start) & (df["ts"] <= call_end)].empty:
        return None
    before, after = df[df["ts"] < call_start], df[df["ts"] > call_end]
    if before.empty or after.empty:
        return None
    win = after[after["ts"] <= after["ts"].iloc[0] + timedelta(minutes=minutes)]
    p0, p1 = before.iloc[-1], win.iloc[-1]
    moves = df.loc[before.index[-1] + 1: win.index[-1], "adj_ret"]   # includes the opening gap bar
    return {"from_ts": p0["ts"], "to_ts": p1["ts"], "adj_ret": float((1 + moves).prod() - 1),
            "ret": float(p1["px"] / p0["px"] - 1), "etf_ret": float(p1["etf_px"] / p0["etf_px"] - 1),
            "vol_mult": float(win["vol_mult"].max())}
