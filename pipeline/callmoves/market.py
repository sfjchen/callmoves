"""Step 2 and 4: 1-minute bars, sector-adjusted returns, abnormal-minute detection."""
from __future__ import annotations

import os
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


def load_bars_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["ts"])
    return df.sort_values("ts").reset_index(drop=True)


def fetch_polygon_bars(ticker: str, start: datetime, end: datetime, api_key: str | None = None) -> pd.DataFrame:
    """1-minute aggregates from Polygon. Needs POLYGON_API_KEY. Returns ts, px, vol."""
    key = api_key or os.environ["POLYGON_API_KEY"]
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute/"
           f"{int(start.timestamp() * 1000)}/{int(end.timestamp() * 1000)}")
    r = httpx.get(url, params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key}, timeout=30)
    r.raise_for_status()
    rows = r.json().get("results", [])
    return pd.DataFrame({
        "ts": pd.to_datetime([x["t"] for x in rows], unit="ms", utc=True),
        "px": [x["c"] for x in rows],
        "vol": [x["v"] for x in rows],
    })


def join_stock_etf(stock: pd.DataFrame, etf: pd.DataFrame) -> pd.DataFrame:
    df = stock.merge(etf[["ts", "px"]].rename(columns={"px": "etf_px"}), on="ts", how="inner")
    return df.sort_values("ts").reset_index(drop=True)


def adjusted_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Per minute: stock return minus ETF return, cumulative since the first bar, volume vs trailing median."""
    out = df.copy()
    stock_ret = out["px"].pct_change().fillna(0.0)
    etf_ret = out["etf_px"].pct_change().fillna(0.0)
    out["adj_ret"] = stock_ret - etf_ret
    out["cum_adj_ret"] = (1 + out["adj_ret"]).cumprod() - 1
    med = out["vol"].shift(1).rolling(BASELINE_MIN, min_periods=5).median()
    out["vol_mult"] = (out["vol"] / med).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    return out


def flag_abnormal(df: pd.DataFrame) -> pd.DataFrame:
    """Flag a minute when its adjusted return is far outside the trailing baseline, or volume spikes with a real move."""
    out = df.copy()
    prior = out["adj_ret"].shift(1)
    mu = prior.rolling(BASELINE_MIN, min_periods=5).mean()
    sd = prior.rolling(BASELINE_MIN, min_periods=5).std().replace(0, np.nan)
    z = ((out["adj_ret"] - mu) / sd).fillna(0.0)
    out["z"] = z
    big_move = out["adj_ret"].abs() >= MIN_ABS_RET
    out["flagged"] = ((z.abs() >= Z_THRESHOLD) | (out["vol_mult"] >= VOL_THRESHOLD)) & big_move
    return out


def to_bars(df: pd.DataFrame) -> list[Bar]:
    return [Bar(**{k: row[k] for k in ("ts", "px", "etf_px", "vol", "adj_ret", "cum_adj_ret", "vol_mult", "flagged")})
            for _, row in df.iterrows()]


def forward_return(df: pd.DataFrame, ts: datetime, minutes: int = 5) -> tuple[float, float]:
    """Adjusted return from the bar at ts to ts+minutes, and the max volume multiple in that window."""
    win = df[(df["ts"] >= ts) & (df["ts"] <= ts + timedelta(minutes=minutes))]
    if win.empty:
        return 0.0, 1.0
    ret = float((1 + win["adj_ret"]).prod() - 1)
    return ret, float(win["vol_mult"].max())


def minutes_to_nearest_flag(df: pd.DataFrame, ts: datetime, lookahead: int = 5) -> float:
    """Distance in minutes to the nearest flagged minute at or after ts (a statement precedes its reaction)."""
    flags = df.loc[df["flagged"] & (df["ts"] >= ts) & (df["ts"] <= ts + timedelta(minutes=lookahead)), "ts"]
    if flags.empty:
        return float("inf")
    return float((flags.iloc[0] - ts).total_seconds() / 60)
