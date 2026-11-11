"""Market side of the pipeline: 1-minute bars from Massive, the sector-adjusted return of each minute, abnormal-
minute detection and the price window around a statement."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")   # US Eastern, the zone of session boundaries and the submit form
WINDOW_MIN = 5          # a statement's reaction window, and how far before a flag a turn still counts
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
FIT_DAYS = 3            # prior regular sessions the market model's betas are fitted on
FIT_LOOKUP_DAYS = 7     # calendar days searched for those sessions
# same-industry names per ticker, 3 to 5 liquid US listings. A peer that reports the same day moves on its own news,
# which is what the spillover tag reads, so peers enter the benchmark only in the "peers" model
PEERS = {
    "WFC": ["JPM", "BAC", "C", "USB", "PNC"], "JPM": ["BAC", "C", "WFC", "GS"], "BAC": ["JPM", "C", "WFC", "USB"],
    "C": ["JPM", "BAC", "WFC"], "GS": ["MS", "JPM", "C"], "PNC": ["USB", "TFC", "WFC", "FITB"],
    "USB": ["PNC", "TFC", "WFC", "FITB"], "TRV": ["ALL", "CB", "PGR"],
    "ABBV": ["BMY", "MRK", "PFE", "LLY"], "ABT": ["MDT", "BSX", "SYK"], "BMY": ["PFE", "MRK", "ABBV"],
    "DHR": ["TMO", "A", "RVTY"], "JNJ": ["PFE", "MRK", "ABBV"], "LLY": ["NVO", "MRK", "ABBV"],
    "MRK": ["PFE", "BMY", "ABBV"], "PFE": ["MRK", "BMY", "ABBV"],
    "BA": ["RTX", "LMT", "GE"], "CAT": ["DE", "CMI", "PCAR"], "GD": ["LMT", "NOC", "RTX"], "HON": ["MMM", "EMR", "ETN"],
    "LMT": ["NOC", "GD", "RTX"], "MMM": ["HON", "EMR", "ITW"], "NOC": ["LMT", "GD", "RTX"], "RTX": ["GE", "LMT", "NOC"],
    "UPS": ["FDX", "EXPD"],
    "AAPL": ["MSFT", "GOOGL"], "AMZN": ["MSFT", "GOOGL", "WMT"], "GOOGL": ["META", "MSFT"],
    "META": ["GOOGL", "SNAP", "PINS"], "MSFT": ["GOOGL", "AMZN", "ORCL"], "CMCSA": ["CHTR", "DIS"], "T": ["VZ", "TMUS"],
    "MCD": ["YUM", "SBUX", "CMG"], "W": ["CHWY", "ETSY", "RH"], "KO": ["PEP", "KDP"], "PG": ["CL", "KMB", "CHD"],
    "MO": ["PM", "BTI"], "PM": ["MO", "BTI"], "CVX": ["XOM", "COP"], "XOM": ["CVX", "COP"],
    "DUK": ["SO", "D", "AEP"], "NEE": ["DUK", "SO", "AEP"],
}
# liquid ETFs read as cross-asset context: the market, rates, the dollar, credit, gold and oil
CROSS_ASSETS = {"SPY": "market", "TLT": "rates", "UUP": "dollar", "HYG": "credit", "GLD": "gold", "USO": "oil"}
BENCHMARKS = ("etf", "market", "peers")   # stock minus ETF, or minus betas x (SPY, ETF[, peer basket])
BENCHMARK = "etf"       # the one the pipeline ships, chosen on the out-of-sample placebo (README, Benchmarks)


def load_bars_csv(path: Path) -> pd.DataFrame:
    """Reads a call folder's bars.csv sorted by time."""
    df = pd.read_csv(path, parse_dates=["ts"])
    return df.sort_values("ts").reset_index(drop=True)


def load_call_bars(folder: Path, ticker: str, etf: str, call_start: datetime,
                   benchmark: str = BENCHMARK) -> tuple[pd.DataFrame, pd.DataFrame | None, dict]:
    """Reads a call folder's bars.csv and context.csv.gz (when present) and returns the bars with benchmark,
    adjusted returns and flags, the context table and the benchmark fit."""
    ctx_path = folder / "context.csv.gz"
    ctx = pd.read_csv(ctx_path, parse_dates=["ts"]) if ctx_path.exists() else None
    bars, fit = with_context(load_bars_csv(folder / "bars.csv"), ctx, ticker, etf, call_start, benchmark)
    return flag_abnormal(adjusted_returns(bars)), ctx, fit


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
    et = pd.to_datetime(ts, utc=True).dt.tz_convert(ET)
    mins = et.dt.hour * 60 + et.dt.minute
    return et.dt.strftime("%Y%m%d") + "-" + ((mins >= 570).astype(int) + (mins >= 960).astype(int)).astype(str)


def day_start(ts: datetime) -> pd.Timestamp:
    """Returns US Eastern midnight of ts's trading day, in UTC like the bars."""
    return pd.Timestamp(ts).tz_convert(ET).normalize().tz_convert("UTC")


def regular_wide(ctx: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Context bars as 1 column of prices per symbol on the regular-session minutes, each symbol's last price carried
    within its day. Returns that wide table and a day label per row."""
    reg = ctx[session_key(ctx["ts"]).str.endswith("-1")]
    wide = reg.pivot_table(index="ts", columns="sym", values="px", aggfunc="last").sort_index()
    day = pd.Series(session_key(pd.Series(wide.index)).values, index=wide.index)
    return wide.groupby(day).ffill(), day


def fit_betas(ctx: pd.DataFrame, ticker: str, factors: dict[str, list[str]], before: datetime) -> dict:
    """Least-squares betas of the stock's 1-minute return on each factor's return (a factor with several symbols is
    their equal-weight mean), over the regular-session minutes of the FIT_DAYS sessions before `before`'s day. The
    first minute of each day is dropped, since it carries the overnight gap. Returns {betas, r2, n}, with no
    intercept used later since a per-minute drift is noise at this scale."""
    wide, day = regular_wide(ctx[ctx["ts"] < day_start(before)])
    days = sorted(day.unique())[-FIT_DAYS:]
    wide, day = wide[day.isin(days)], day[day.isin(days)]
    ret = wide.groupby(day).pct_change()
    cols = {"stock": ret[ticker]} | {f: ret[[x for x in syms if x in ret]].mean(axis=1) for f, syms in factors.items()}
    xy = pd.DataFrame(cols).dropna()
    if len(xy) < 60:
        return {"betas": {}, "r2": None, "n": len(xy)}
    x = np.column_stack([np.ones(len(xy))] + [xy[f] for f in factors])
    coef, *_ = np.linalg.lstsq(x, xy["stock"].to_numpy(), rcond=None)
    resid = xy["stock"].to_numpy() - x @ coef
    return {"betas": {f: float(b) for f, b in zip(factors, coef[1:])},
            "r2": float(1 - resid.var() / xy["stock"].var()), "n": len(xy)}


def with_context(bars: pd.DataFrame, ctx: pd.DataFrame | None, ticker: str, etf: str, call_start: datetime,
                 benchmark: str = "etf") -> tuple[pd.DataFrame, dict]:
    """Adds `bench_px`, the price index the stock is measured against, plus `spy_px` and `peer_px` (the peers'
    equal-weight index) as each minute's last print. "etf" uses the sector ETF as is. "market" and "peers" chain
    the betas-weighted 1-minute returns of SPY and the ETF (and the peers), betas fitted on the days before the call,
    so the benchmark never sees the call day. Without context or a usable fit it falls back to the ETF. Returns the
    bars and the fit."""
    out = bars.copy()
    out["bench_px"] = out["etf_px"]
    if ctx is None or ctx.empty:
        return out, {"benchmark": "etf", "betas": {}, "r2": None, "n": 0}
    day = ctx[ctx["ts"] >= day_start(call_start)]
    wide = day.pivot_table(index="ts", columns="sym", values="px", aggfunc="last").sort_index().ffill()
    peers = [p for p in PEERS.get(ticker, []) if p in wide]
    at = pd.merge_asof(out[["ts"]], wide.reset_index(), on="ts", direction="backward")
    at = at.drop(columns="ts").set_index(out.index)
    ret = at.pct_change().fillna(0.0)
    out["spy_px"] = at.get("SPY")
    if peers:
        out["peer_px"] = (1 + ret[peers].mean(axis=1)).cumprod()
    factors = {"SPY": ["SPY"], etf: [etf]} | ({"peers": peers} if benchmark == "peers" and peers else {})
    fit = {"benchmark": "etf", "betas": {}, "r2": None, "n": 0}
    if benchmark != "etf":
        fit = fit_betas(ctx, ticker, factors, call_start) | {"benchmark": benchmark}
    if fit["betas"]:
        r = sum(b * (ret[factors[f]].mean(axis=1) if len(factors[f]) > 1 else ret[factors[f][0]])
                for f, b in fit["betas"].items())
        out["bench_px"] = (1 + r).cumprod()
    else:
        fit["benchmark"] = "etf"
    return out, fit


def adjusted_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Per minute: stock return minus the benchmark's (`bench_px`, the sector ETF when absent), cumulative since the
    first bar, volume vs the trailing median of the same session, and `gap_min`, the minutes since the previous bar.
    Massive omits minutes with no trade, so a bar after a gap carries the return of the whole gap."""
    out = df.copy()
    stock_ret = out["px"].pct_change().fillna(0.0)
    bench_ret = out.get("bench_px", out["etf_px"]).pct_change().fillna(0.0)
    out["adj_ret"] = stock_ret - bench_ret
    out["gap_min"] = (pd.to_datetime(out["ts"]).diff().dt.total_seconds() / 60).fillna(1.0).clip(lower=1.0)
    out["cum_adj_ret"] = (1 + out["adj_ret"]).cumprod() - 1
    med = out.groupby(session_key(out["ts"]))["vol"].transform(
        lambda v: v.shift(1).rolling(BASELINE_MIN, min_periods=5).median())
    out["vol_mult"] = (out["vol"] / med.where(med > 0)).fillna(1.0)   # zero or NaN baseline: neutral 1.0
    return out


def flag_abnormal(df: pd.DataFrame) -> pd.DataFrame:
    """Flag a minute when its adjusted return is far outside the trailing baseline, or volume spikes with a real
    move. Returns are scaled to 1 minute by the square root of the gap before the z-score, since a return's variance
    grows with its time span, so a bar after 10 quiet minutes is not judged as a 1-minute jump."""
    out = df.copy()
    per_min = out["adj_ret"] / np.sqrt(out["gap_min"])
    roll = per_min.groupby(session_key(out["ts"])).transform
    mu = roll(lambda r: r.shift(1).rolling(BASELINE_MIN, min_periods=5).mean())
    sd = roll(lambda r: r.shift(1).rolling(BASELINE_MIN, min_periods=5).std()).replace(0, np.nan)
    z = ((per_min - mu) / sd).fillna(0.0)
    out["z"] = z
    big_move = out["adj_ret"].abs() >= MIN_ABS_RET
    out["flagged"] = ((z.abs() >= Z_THRESHOLD) | (out["vol_mult"] >= VOL_THRESHOLD)) & big_move
    return out


def minute_of(ts: datetime) -> datetime:
    """The start of the minute holding ts. A bar's ts is its minute's start and its px that minute's close, so the
    bar for a statement at 10:00:30 is the 10:00 bar."""
    return ts.replace(second=0, microsecond=0)


def forward_window(df: pd.DataFrame, ts: datetime, minutes: int = WINDOW_MIN) -> dict:
    """What happened in the statement's own minute and the minutes after it, `minutes` bars in all: adjusted, raw
    stock, ETF, benchmark, SPY and peer-index returns (None without context), and the peak volume multiple."""
    t0 = minute_of(ts)
    win = df[(df["ts"] >= t0) & (df["ts"] < t0 + timedelta(minutes=minutes))]
    if win.empty:
        return {"adj_ret": 0.0, "ret": 0.0, "etf_ret": 0.0, "bench_ret": 0.0, "spy_ret": None, "peer_ret": None,
                "vol_mult": 1.0}
    # measured from the last close before the statement's minute, and adjusted = stock minus benchmark over those
    # same bars so the numbers on a card reconcile
    before = df[df["ts"] < t0]
    base = before.iloc[-1] if not before.empty else win.iloc[0]

    def move(col: str) -> float | None:
        """Returns col's return from the base bar to the window's last bar, None when the column is missing."""
        if col not in df or pd.isna(base[col]) or pd.isna(win[col].iloc[-1]):
            return None
        return float(win[col].iloc[-1] / base[col] - 1)

    stock, bench = move("px"), move("bench_px") if "bench_px" in df else move("etf_px")
    return {"adj_ret": stock - bench, "ret": stock, "etf_ret": move("etf_px"), "bench_ret": bench,
            "spy_ret": move("spy_px"), "peer_ret": move("peer_px"), "vol_mult": float(win["vol_mult"].max())}


def segments_before_flags(segments, df: pd.DataFrame, lookback_min: int = WINDOW_MIN):
    """Work backward from the price: keep only the transcript turns spoken in the minutes before an abnormal minute.
    Summarizing the whole call would surface what sounds important, not what the market reacted to."""
    flags = list(df.loc[df["flagged"], "ts"])
    if not flags:
        return []
    keep = []
    for s in segments:
        if s.ts is None:
            continue
        if any(f - timedelta(minutes=lookback_min) <= minute_of(s.ts) <= f for f in flags):
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


def minutes_to_nearest_flag(df: pd.DataFrame, ts: datetime, lookahead: int = WINDOW_MIN) -> float:
    """Whole minutes from the statement's minute to the nearest flagged minute at or after it (a statement precedes
    its reaction, and a flag in its own minute is 0)."""
    t0 = minute_of(ts)
    flags = df.loc[df["flagged"] & (df["ts"] >= t0) & (df["ts"] <= t0 + timedelta(minutes=lookahead)), "ts"]
    if flags.empty:
        return float("inf")
    return float((flags.iloc[0] - t0).total_seconds() / 60)


def fetch_bars(ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Massive bars between start and end as ts (UTC), px, vol. Raises when POLYGON_API_KEY is unset."""
    if not os.environ.get("POLYGON_API_KEY"):
        raise RuntimeError("POLYGON_API_KEY is not set: Massive (formerly Polygon) is the only 1-minute bars source")
    df = fetch_polygon_bars(ticker, start, end)
    return df[(df["ts"] >= start) & (df["ts"] <= end)].reset_index(drop=True)


def fetch_context(ticker: str, etf: str, call_start: datetime, call_end: datetime, before_min: int = 180,
                  after_min: int = 150) -> pd.DataFrame:
    """Bars of the stock, its ETF, its peers and the cross-asset ETFs as a long table ts, sym, px, vol: the regular
    sessions of the FIT_LOOKUP_DAYS before the call day (for the market model's fit) and the call window of
    `bars_for_call`. A symbol Massive does not return is left out."""
    end = max(call_start + timedelta(minutes=after_min), call_end + timedelta(minutes=30))
    win_start = call_start - timedelta(minutes=before_min)
    day0 = day_start(call_start)
    parts = []
    for sym in dict.fromkeys([ticker, etf, *CROSS_ASSETS, *PEERS.get(ticker, [])]):
        df = fetch_bars(sym, call_start - timedelta(days=FIT_LOOKUP_DAYS), end)
        if df.empty:
            continue
        fit = df[(df["ts"] < day0) & session_key(df["ts"]).str.endswith("-1")]
        parts.append(pd.concat([fit, df[df["ts"] >= win_start]]).assign(sym=sym))
    out = pd.concat(parts, ignore_index=True)
    return out.assign(px=out["px"].round(4), vol=out["vol"].round())[["ts", "sym", "px", "vol"]]


def _traded_bars(ticker: str, etf: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Returns the stock and ETF bars between start and end joined on the minute."""
    return join_stock_etf(fetch_bars(ticker, start, end), fetch_bars(etf, start, end))


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


def open_reaction(
        df: pd.DataFrame, call_start: datetime, call_end: datetime, minutes: int = OPEN_WINDOW_MIN
) -> dict | None:
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


def main(argv=None):
    """Writes context.csv.gz (peer and cross-asset bars) into each call folder that lacks it, or all with --force."""
    import argparse
    import json

    from dotenv import load_dotenv

    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("calls", type=Path, nargs="+")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    for call in args.calls:
        path = call / "context.csv.gz"
        if path.exists() and not args.force:
            continue
        meta = json.loads((call / "call.json").read_text())
        start = datetime.fromisoformat(meta["call_start"])
        end = start + timedelta(milliseconds=max(x["t1_ms"] for x in meta["segments"]) - meta.get("audio_offset_ms", 0))
        ctx = fetch_context(meta["ticker"], meta["etf"], start, end)
        ctx.to_csv(path, index=False)
        print(f"{call.name}: {ctx['sym'].nunique()} symbols, {len(ctx)} bars", flush=True)


if __name__ == "__main__":
    main()
