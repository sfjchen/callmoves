"""Price-only panel: the earnings calls of the last 2 years for every ticker the library knows, without audio.

    python -m callmoves.panel events     # SEC filings and yfinance -> panel/events.csv
    python -m callmoves.panel bars       # Massive minute bars per event -> out/panel/bars/ (hours, 5 requests/min)
    python -m callmoves.panel stats      # panel/features.csv and the tables in the README

The library has 88 calls with audio, too few to say when in a call prices move or how the call's move relates to
the release, the surprise and the next day. Those questions need only a call's start time and minute bars, so this
module builds a wider sample from free sources:

- the event: every 8-K with Item 2.02 (results of operations) a company filed, from EDGAR's submissions API. Its
  acceptance time is when the release reached EDGAR, to the second.
- the call start: read from the release text ("conference call at 5:00 p.m. ET") by `call_time`. A release that
  states no time is dropped, not guessed. `check` scores this against the library's calls, whose start times were
  set by hand.
- the surprise: reported EPS against the estimate, from yfinance, matched to the release by date.
- the bars: `market.fetch_bars` for the stock, SPY and the sector ETF over the event day, the sessions before it
  (the controls) and the session after it.

Universe: the library's tickers and the peers named in `market.PEERS`. Foreign filers have no 8-K and drop out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from dotenv import load_dotenv

from . import market
from .docs import html_to_text
from .market import ET

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "out" / "panel"            # gitignored cache of everything fetched
BARS = RAW / "bars"
EVENTS = ROOT / "panel" / "events.csv"
FEATURES = ROOT / "panel" / "features.csv"
FIRST_DAY = datetime(2024, 12, 2, tzinfo=UTC)   # Massive's free plan keeps 2 years of minute bars
SEC_WAIT_S = 0.15                       # EDGAR allows 10 requests a second
NET_RETRIES, NET_WAIT_S = 6, 20         # a failed request is retried after this wait
MAX_DOC_BYTES = 3_000_000               # a release is well under this, a full 10-Q exhibit is not
PRE_DAYS, POST_DAYS = 21, 5              # calendar days of bars around a call: 10+ control sessions, 2 closes after
CHUNK_DAYS = 45                         # SPY is fetched once for the whole span, in pieces a rerun can resume
PACE_S = 12.5                           # Massive's free plan: 5 requests a minute
CALL_MIN = 60                           # the call hour (Brochet, Kolev and Lerman read the first 60 minutes too)
CONTROLS = 10                           # prior sessions whose same clock hour is the event's baseline
MIN_COVER = 0.25                        # share of the call hour's minutes with a trade, as in timing.py
MIN_CTL_DAYS = 5                        # control sessions an event needs to have a baseline
MIN_GROUP = 40                          # events a cut needs to get a column
BOOT = 1000
BIN_MIN = 10
BINS = range(-180, 90, BIN_MIN)         # 10-minute bins from 3 hours before the call's start to 90 minutes after
FRESH_MIN = 10                          # a price older than this is stale: its window gets no return
SKIP_MIN = 5                            # gap between the call hour's last price and the forward return's first
MAX_AGE_MIN = 240                       # minutes since the release that the in-call test looks at
KEYWORD = re.compile(r"conference call|webcast|earnings call|teleconference|call to discuss|investor call|"
                     r"call with (?:analysts|investors)|host a call|live call|video call", re.IGNORECASE)
CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?", re.IGNORECASE)
ZONES = {"eastern": 0, "et": 0, "est": 0, "edt": 0, "central": 1, "ct": 1, "cst": 1, "cdt": 1, "mountain": 2,
         "mt": 2, "mst": 2, "mdt": 2, "pacific": 3, "pt": 3, "pst": 3, "pdt": 3}   # hours behind US Eastern
ZONE = re.compile(r"\b(" + "|".join(ZONES) + r")\b", re.IGNORECASE)
EXHIBIT = re.compile(r"99|press|release|earn", re.IGNORECASE)    # file names of the release exhibit
SKIP_DOC = re.compile(r"^R\d+\.htm|-index", re.IGNORECASE)          # EDGAR's own rendered pages
KEY_REACH = 250      # characters between the call keyword and its time
ZONE_REACH = 40      # characters between a time and its zone
REPLAY_REACH = 80    # "replay" this close before the keyword or the time marks a replay's time, not the call's
LATE_TOL_MIN = 15    # a release accepted this long after the stated call time still belongs to that call
DAY_REACH = 100      # characters around the keyword and time searched for the call's day
OTHER_EVENT_DAYS = 120   # a time dated this far after the release is an investor day or a conference
MAX_AHEAD_DAYS = 5   # a dated call further from the release than this is some other date in the text
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MONTH_DAY = re.compile(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2})\b", re.IGNORECASE)


def _get(url: str, cache: Path, cap: int | None = None) -> bytes | None:
    """Returns the body of an SEC URL, from the cache when present, None on a 404 or a body over `cap` bytes. SEC
    refuses requests without a contact User-Agent, so SEC_USER_AGENT must be set."""
    if cache.exists():
        return cache.read_bytes()
    for attempt in range(NET_RETRIES + 1):
        time.sleep(NET_WAIT_S if attempt else SEC_WAIT_S)
        try:
            r = httpx.get(url, headers={"User-Agent": os.environ["SEC_USER_AGENT"]}, timeout=60,
                          follow_redirects=True)
        except httpx.TransportError:    # a dropped connection or failed lookup, common on a long pull
            if attempt == NET_RETRIES:
                raise
            continue
        if r.status_code not in (429, 500, 502, 503) or attempt == NET_RETRIES:
            break
    if r.status_code == 404 or (cap and len(r.content) > cap):
        return None
    r.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(r.content)
    return r.content


def dated(hint: tuple[int, int], near: date, lo: int = -1, hi: int = MAX_AHEAD_DAYS) -> date | None:
    """Returns the (month, day) hint as a date `lo` to `hi` days from `near`, None when it is no real date or lies
    outside that range, as the quarter's end or a later investor event does."""
    for year in (near.year, near.year + 1):
        try:
            day = date(year, *hint)
        except ValueError:      # "February 30" from a table
            return None
        if lo <= (day - near).days <= hi:
            return day
    return None


def call_time(text: str, near: date) -> tuple[int, int, object] | None:
    """Returns the call's start as (hour, minute, day hint) US Eastern from a release's text, None when it states
    none. A time counts when a zone follows it within ZONE_REACH characters and a call keyword sits within
    KEY_REACH, and no "replay" stands between or just before them. The time nearest its keyword wins, and both
    halves of a pair such as "2:00 p.m. PT (5:00 p.m. ET)" read the same Eastern time. The day hint is what the
    text within DAY_REACH says about the day: "tomorrow", the nearest (month, day) when it is near the release's
    day (`dated`), "today", or None. A time dated up to OTHER_EVENT_DAYS later belongs to some other event and is
    skipped, and an earlier date (the quarter's end) is ignored."""
    keys = [m.start() for m in KEYWORD.finditer(text)]
    best = None
    for m in CLOCK.finditer(text):
        zone = ZONE.search(text, m.end(), m.end() + ZONE_REACH)
        hour = int(m.group(1))
        if not zone or not keys or not 1 <= hour <= 12 or int(m.group(2) or 0) > 59:
            continue
        key = min(keys, key=lambda k: abs(k - m.start()))
        dist = abs(key - m.start())
        if dist > KEY_REACH or "replay" in text[max(0, min(key, m.start()) - REPLAY_REACH):m.start()].lower():
            continue
        h24 = hour % 12 + (12 if m.group(3).lower() == "p" else 0) + ZONES[zone.group(1).lower()]
        lo = max(0, min(key, m.start()) - DAY_REACH)
        around = text[lo:m.end() + DAY_REACH]
        day = min(MONTH_DAY.finditer(around), key=lambda d: abs(d.start() + lo - m.start()), default=None)
        md = (MONTHS.index(day.group(1)[:3].lower()) + 1, int(day.group(2))) if day else None
        if md and dated(md, near, MAX_AHEAD_DAYS + 1, OTHER_EVENT_DAYS):
            continue
        hint = "tomorrow" if re.search(r"\btomorrow\b", around, re.IGNORECASE) else \
            md if md and dated(md, near) else "today" if re.search(r"\btoday\b", around, re.IGNORECASE) else None
        if h24 < 24 and (best is None or dist < best[0]):
            best = (dist, h24, int(m.group(2) or 0), hint)
    return best[1:] if best else None


def call_start(accepted: datetime, clock: tuple[int, int, object],
               earn_days: frozenset[date] = frozenset()) -> tuple[datetime, bool, str]:
    """Returns the call's start, whether the filing reached EDGAR after it, and where the day came from: a date in
    the text, "tomorrow" (the next weekday), "today", the earnings calendar, or a rule.

    A morning call whose 8-K arrived after 15:30 Eastern is ambiguous even with "today" in the text: the release
    was filed either the evening before its date (Danaher, Duke) or the evening after the call. `earn_days`, the
    company's earnings days from yfinance, settles it when one of the 2 days is in it. Otherwise "today" is the
    filing's day and no hint means the next weekday, and those rule days are reported apart. A start more than
    LATE_TOL_MIN before the acceptance means the 8-K was filed after the call began."""
    acc = accepted.astimezone(ET)
    start = acc.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
    nxt = start + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    hint, src = clock[2], clock[2] or "rule"
    if isinstance(hint, tuple):
        day = dated(hint, acc.date())
        start, src = start.replace(year=day.year, month=day.month, day=day.day), "date"
    elif hint == "tomorrow":
        start = nxt
    elif acc.hour * 60 + acc.minute >= 930 and clock[0] < 12:
        if nxt.date() in earn_days or start.date() in earn_days:
            start, src = nxt if nxt.date() in earn_days else start, "calendar"
        elif hint is None:
            start = nxt
    return start, start < acc - timedelta(minutes=LATE_TOL_MIN), src


def filings(cik: int) -> pd.DataFrame:
    """Returns a company's Item 2.02 8-Ks since FIRST_DAY as accession, accepted (UTC). EDGAR pages older filings
    into extra files, read only when they reach past FIRST_DAY (JPM files thousands of note supplements)."""
    sub = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", RAW / "sub" / f"{cik}.json"))
    pages = [sub["filings"]["recent"]]
    for f in sub["filings"].get("files", []):
        if f["filingTo"] >= FIRST_DAY.strftime("%Y-%m-%d"):
            pages.append(json.loads(_get(f"https://data.sec.gov/submissions/{f['name']}", RAW / "sub" / f["name"])))
    df = pd.concat([pd.DataFrame({k: p[k] for k in ("accessionNumber", "form", "items", "acceptanceDateTime")})
                    for p in pages], ignore_index=True)
    df = df[(df["form"] == "8-K") & df["items"].str.contains("2.02", regex=False)]
    df = df.assign(accepted=pd.to_datetime(df["acceptanceDateTime"], utc=True)).rename(
        columns={"accessionNumber": "accession"})
    return df[df["accepted"] >= FIRST_DAY].drop_duplicates("accession")[["accession", "accepted"]]


def release_clock(cik: int, accession: str, near: date) -> tuple[int, int, object] | None:
    """Returns the call time stated in any HTML document of 1 filing: files named like an exhibit 99 first, then
    the smallest, since the release is shorter than the financial supplement."""
    acc = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
    index = _get(f"{base}/index.json", RAW / "doc" / acc / "index.json")
    if index is None:
        return None
    items = [i for i in json.loads(index)["directory"]["item"]
             if i["name"].lower().endswith((".htm", ".html")) and not SKIP_DOC.search(i["name"])]
    for item in sorted(items, key=lambda i: (not EXHIBIT.search(i["name"]), int(i.get("size") or 0))):
        raw = _get(f"{base}/{item['name']}", RAW / "doc" / acc / item["name"], MAX_DOC_BYTES)
        clock = call_time(html_to_text(raw.decode("utf-8", "replace")), near) if raw else None
        if clock:
            return clock
    return None


def surprises(ticker: str) -> pd.DataFrame:
    """Returns yfinance's earnings rows for a ticker as day, eps_est, eps, surprise_pct, cached, empty on failure.
    Its timestamps are placeholders (16:00 or 06:00), so only the day is kept."""
    cache = RAW / "yf" / f"{ticker}.csv"
    if cache.exists():
        return pd.read_csv(cache, parse_dates=["day"])
    import yfinance as yf

    try:
        df = yf.Ticker(ticker).get_earnings_dates(limit=16)
        df = pd.DataFrame({"day": df.index.tz_convert(ET).tz_localize(None).normalize(), "eps_est": df.iloc[:, 0],
                           "eps": df.iloc[:, 1], "surprise_pct": df.iloc[:, 2]}).dropna(subset=["eps"])
    except Exception as e:   # noqa: BLE001 yfinance raises many types, a missing surprise only drops a column
        print(f"  yfinance {ticker}: {str(e)[:120]}")
        df = pd.DataFrame(columns=["day", "eps_est", "eps", "surprise_pct"])
    time.sleep(1.0)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    return df


def bars_path(e) -> Path:
    """Returns the cache path of 1 event's stock bars."""
    return BARS / f"{e.ticker}_{e.call_start[:10]}.csv.gz"


def universe() -> list[str]:
    """Returns the library's tickers and their peers, sorted."""
    lib = {json.loads(p.read_text())["ticker"] for p in (ROOT / "data").glob("*/call.json")}
    return sorted(lib | set(market.PEERS) | {p for peers in market.PEERS.values() for p in peers})


def build_events() -> pd.DataFrame:
    """Writes panel/events.csv: 1 row per earnings release with a stated call time, and prints what was dropped."""
    ciks = {v["ticker"]: int(v["cik_str"]) for v in json.loads(_get(
        "https://www.sec.gov/files/company_tickers.json", RAW / "company_tickers.json")).values()}
    rows, no_cik, no_time = [], [], 0
    for tk in universe():
        if tk not in ciks:
            no_cik.append(tk)
            continue
        fl, sur = filings(ciks[tk]), surprises(tk)
        for f in fl.itertuples():
            clock = release_clock(ciks[tk], f.accession, f.accepted.tz_convert(ET).date())
            if clock is None:
                no_time += 1
                continue
            start, late, src = call_start(f.accepted.to_pydatetime(), clock, frozenset(sur["day"].dt.date))
            day = pd.Timestamp(start.date())
            near = sur[(sur["day"] - day).abs() <= pd.Timedelta(days=1)]
            s = near.iloc[0] if len(near) else {}
            rows.append({"ticker": tk, "accession": f.accession, "accepted": f.accepted.isoformat(),
                         "call_start": start.isoformat(), "late_8k": late, "day_src": src,
                         "eps_est": s.get("eps_est"), "eps": s.get("eps"), "surprise_pct": s.get("surprise_pct")})
        print(f"{tk}: {len(fl)} releases, {sum(r['ticker'] == tk for r in rows)} with a call time", flush=True)
    df = pd.DataFrame(rows).sort_values(["ticker", "call_start"]).drop_duplicates(["ticker", "call_start"])
    EVENTS.parent.mkdir(exist_ok=True)
    df.to_csv(EVENTS, index=False)
    print(f"{len(df)} events, {df['ticker'].nunique()} tickers -> {EVENTS}. No CIK: {' '.join(no_cik) or 'none'}. "
          f"Releases with no stated call time: {no_time}")
    return df


def check(df: pd.DataFrame) -> None:
    """Prints how the parsed call starts compare with the library's hand-set ones, per matched call."""
    ev = df.assign(start=pd.to_datetime(df["call_start"], utc=True))
    diffs, missing = [], []
    for p in sorted((ROOT / "data").glob("*/call.json")):
        c = json.loads(p.read_text())
        t = pd.Timestamp(c["call_start"]).tz_convert("UTC")
        near = ev[(ev["ticker"] == c["ticker"]) & ((ev["start"] - t).abs() <= pd.Timedelta(days=2))]
        if near.empty:
            missing.append(p.parent.name)
            continue
        diffs.append((p.parent.name, min(((s - t).total_seconds() / 60 for s in near["start"]), key=abs)))
    d = pd.Series(dict(diffs))
    print(f"library check: {len(d)} matched, {len(missing)} not in the panel ({' '.join(missing)})")
    print(f"  parsed start within 5 minutes of the library's: {(d.abs() <= 5).sum()} of {len(d)}, "
          f"within 15: {(d.abs() <= 15).sum()}")
    print("  off by more than 5 minutes:", d[d.abs() > 5].round().astype(int).to_dict())
    print("  day taken from:", df["day_src"].value_counts().to_dict(), "| 8-K filed after the call began:",
          int(df["late_8k"].sum()))


def fetch_bars(df: pd.DataFrame) -> None:
    """Caches each event's stock bars (PRE_DAYS before the call to POST_DAYS after) and SPY's in CHUNK_DAYS pieces
    under out/panel/bars/, pacing requests to Massive's 5 a minute. A rerun fetches only what is missing."""
    jobs = [("SPY", t, t + timedelta(days=CHUNK_DAYS), BARS / f"SPY_{t:%Y%m%d}.csv.gz") for t in pd.date_range(
        FIRST_DAY - timedelta(days=PRE_DAYS), datetime.now(UTC), freq=f"{CHUNK_DAYS}D").to_pydatetime()]
    for e in df.itertuples():
        t = datetime.fromisoformat(e.call_start)
        jobs.append((e.ticker, t - timedelta(days=PRE_DAYS), t + timedelta(days=POST_DAYS), bars_path(e)))
    todo = [j for j in jobs if not j[3].exists()]
    print(f"{len(todo)} of {len(jobs)} requests to make, about {len(todo) * PACE_S / 3600:.1f} h", flush=True)
    BARS.mkdir(parents=True, exist_ok=True)
    for i, (sym, t0, t1, path) in enumerate(todo):
        try:
            market.fetch_bars(sym, t0, min(t1, datetime.now(UTC))).to_csv(path, index=False)
        except httpx.HTTPError as e:
            print(f"  {path.name}: {str(e)[:120]}", flush=True)
        if i % 50 == 0:
            print(f"  {i} done, at {path.name}", flush=True)
        time.sleep(PACE_S)


def features(b: pd.DataFrame, start: datetime) -> dict | None:
    """Returns 1 event's features from its bars (ts, px, vol, etf_px with SPY as the benchmark), None when the
    stock has no regular close before the call or nothing trades in the call hour.

    A bar is stamped with the start of its minute and holds that minute's last trade, so the price known at time t
    is the bar that started before t, and it is stale, giving no return, when that bar is over FRESH_MIN old. The
    same rule runs on the call day and on every control day, so thin trading cannot favour either.

    - pre: adjusted return from the last regular close before the call to the call's start (release and all).
    - call: adjusted return over the CALL_MIN minutes from the start. thin marks an hour with a stale end or a trade
      in under MIN_COVER of its minutes. vol, rv (summed |1-minute return|) and flags describe the same hour.
    - fwd: from the first regular-session bar SKIP_MIN or more after the call hour to the regular close of the
      session after that bar's, so it shares no price with call (a shared print fakes a reversal).
    - ctl_*: the same windows on the CONTROLS sessions before the call day, hours that fail `thin` left out:
      ctl_abs the mean |hour return|, ctl_rank the share of them the call hour's |return| beats (ties half),
      ctl_share the mean of |call| / (|pre| + |call|) on those days.
    - ev_k, ct_k: |return| in the 10 minutes from minute k of the call, and the controls' mean."""
    b = market.flag_abnormal(market.adjusted_returns(b)).reset_index(drop=True)
    ns = b["ts"].to_numpy().astype("datetime64[ns]").astype("int64")
    reg = b[market.session_key(b["ts"]).str.endswith("-1")]
    close = reg.groupby(reg["ts"].dt.tz_convert(ET).dt.date).tail(1)

    def at(t: datetime, side: str = "left") -> int:
        """Returns the index of the first bar starting at or after t."""
        return int(np.searchsorted(ns, pd.Timestamp(t).value, side=side))

    def span(t0: datetime, t1: datetime, close0: bool = False) -> float:
        """Returns the adjusted return between the prices known at t0 and t1, NaN when either is stale. With
        close0 the first price is the last regular close before t0, however old."""
        i = (close.index[close["ts"] < t0][-1:].tolist() or [-1])[0] if close0 else at(t0) - 1
        j = at(t1) - 1
        stale = FRESH_MIN * 60 * 10**9
        if i < 0 or j < i or pd.Timestamp(t1).value - ns[j] > stale + 60 * 10**9 or \
                (not close0 and pd.Timestamp(t0).value - ns[i] > stale + 60 * 10**9):
            return float("nan")
        return float(b["px"].iat[j] / b["px"].iat[i] - b["etf_px"].iat[j] / b["etf_px"].iat[i])

    def hour(t: datetime) -> dict:
        """Returns the call-hour measures for the hour from t, on the call day or a control day."""
        w = b.iloc[at(t):at(t + timedelta(minutes=CALL_MIN))]
        ret = span(t, t + timedelta(minutes=CALL_MIN))
        return {"ret": ret, "pre": span(t, t, close0=True), "n": len(w), "vol": float(w["vol"].sum()),
                "rv": float(w["adj_ret"].abs().sum()), "flags": int(w["flagged"].sum()),
                "thin": not np.isfinite(ret) or len(w) < MIN_COVER * CALL_MIN,
                **{k: abs(span(t + timedelta(minutes=k), t + timedelta(minutes=k + BIN_MIN))) for k in BINS}}

    call = hour(start)
    if (close["ts"] < start).sum() == 0 or not call["n"]:
        return None
    local = start.astimezone(ET)
    days = [d for d in sorted(set(b["ts"].dt.tz_convert(ET).dt.date)) if d < local.date()][-CONTROLS:]
    ctl = pd.DataFrame([hour(local.replace(year=d.year, month=d.month, day=d.day)) for d in days])   # same clock
    ctl = ctl[~ctl["thin"]] if len(ctl) else ctl
    first = reg[reg["ts"] >= start + timedelta(minutes=CALL_MIN + SKIP_MIN)].head(1)
    ends = close[close["ts"] > first["ts"].iloc[0]] if len(first) else close.iloc[:0]
    j = ends.index[1] if len(ends) > 1 and ends["ts"].iloc[0].date() == first["ts"].iloc[0].date() else \
        ends.index[0] if len(ends) and ends["ts"].iloc[0].date() != first["ts"].iloc[0].date() else None
    i = first.index[0] if len(first) else None
    some = len(ctl) > 0
    out = {"pre": call["pre"], "call": call["ret"], "n": call["n"], "vol": call["vol"], "rv": call["rv"],
           "flags": call["flags"], "thin": call["thin"],
           "fwd": float(b["px"].iat[j] / b["px"].iat[i] - b["etf_px"].iat[j] / b["etf_px"].iat[i])
           if j is not None else float("nan"),
           "ctl_days": len(ctl), "ctl_abs": float(ctl["ret"].abs().mean()) if some else float("nan"),
           "ctl_rank": float(((ctl["ret"].abs() < abs(call["ret"])) + 0.5 * (ctl["ret"].abs() == abs(call["ret"])))
                             .mean()) if some else float("nan"),
           "ctl_share": float((ctl["ret"].abs() / (ctl["ret"].abs() + ctl["pre"].abs())).mean()) if some
           else float("nan"),
           "ctl_vol": float(ctl["vol"].median()) if some else float("nan"),
           "ctl_n": int(ctl["n"].sum()) if some else 0, "ctl_flags": int(ctl["flags"].sum()) if some else 0}
    for k in BINS:
        out[f"ev_{k}"] = call[k]
        out[f"ct_{k}"] = float(ctl[k].mean()) if some and ctl[k].notna().sum() >= MIN_CTL_DAYS else float("nan")
    return out


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Writes panel/features.csv, 1 row per event with cached bars, and returns it."""
    spy = pd.concat([market.load_bars_csv(p) for p in sorted(BARS.glob("SPY_*.csv.gz"))]).drop_duplicates("ts")
    lib = {(json.loads(p.read_text())["ticker"], json.loads(p.read_text())["call_start"][:10])
           for p in (ROOT / "data").glob("*/call.json")}
    rows = []
    for e in df.itertuples():
        if not bars_path(e).exists():
            continue
        stock = market.load_bars_csv(bars_path(e))
        f = features(market.join_stock_etf(stock, spy), datetime.fromisoformat(e.call_start)) if len(stock) else None
        if f:
            rows.append({"ticker": e.ticker, "call_start": e.call_start, "accepted": e.accepted,
                         "late_8k": e.late_8k, "day_src": e.day_src, "surprise_pct": e.surprise_pct,
                         "in_library": (e.ticker, e.call_start[:10]) in lib, **f})
    out = pd.DataFrame(rows)
    out.to_csv(FEATURES, index=False)
    print(f"{len(out)} of {len(df)} events have features -> {FEATURES}")
    return out


def describe(f: pd.DataFrame, rng: np.random.Generator, draws: int = BOOT) -> dict[str, tuple[float, float, float]]:
    """Returns each panel statistic for 1 group of events as (value, low, high). The interval is the wider of 2
    bootstraps, over call days (calls on 1 day share the market's news) and over tickers (a ticker repeats about
    8 times).

    - abs x: mean |call-hour return| over the mean of the controls' (1 = an ordinary hour).
    - rank: mean share of an event's control hours that its call hour beats (0.5 = an ordinary hour).
    - in-call lift: the test that separates the call from the release. Each 10-minute bin after the release and up
      to the call hour's end is scored log(1 + |return| / the controls' mean for that clock bin). Bins are compared
      inside cells of equal release age (10-minute steps) and session, where some events are already in their call
      and others are still waiting for it, and the lift is the in-call bins' mean score minus the waiting bins'
      (0 = the call adds nothing to a release still being digested). Columns y_k and c_k hold the scores and cells.
    - volume x: the median event's call-hour volume over its controls' median. flag x: flagged minutes per traded
      minute in call hours over the same in control hours.
    - |pre| %, |call| %: median absolute returns. share and ctl share: median |call| / (|pre| + |call|) on call
      days and on control days.
    - rank correlations (Spearman): surprise with pre and with call, and their difference, pre with call, call and
      pre each with fwd. call|pre: the call's coefficient when fwd's rank is regressed on the ranks of call and
      pre together."""
    ys, cs = [f"y_{k}" for k in BINS if k < CALL_MIN], [f"c_{k}" for k in BINS if k < CALL_MIN]
    in_call = np.array([k >= 0 for k in BINS if k < CALL_MIN], dtype=float)

    def rho(d: pd.DataFrame, x: str, y: str) -> float:
        """Returns the rank correlation of 2 columns over the rows that have both."""
        return d[[x, y]].dropna().corr(method="spearman").iloc[0, 1]

    def one(d: pd.DataFrame) -> dict[str, float]:
        """Returns every statistic for 1 sample."""
        y, c = d[ys].to_numpy().ravel(), d[cs].to_numpy().ravel().astype(int)
        x = np.tile(in_call, len(d))
        keep = c >= 0
        y, c, x = y[keep], c[keep], x[keep]
        cnt = np.bincount(c)
        x = x - (np.bincount(c, x) / np.maximum(cnt, 1))[c]      # each cell's own share of in-call bins removed
        y = y - (np.bincount(c, y) / np.maximum(cnt, 1))[c]
        ok = d[d["ctl_vol"] > 0]
        r = d[["fwd", "call", "pre"]].dropna().rank()
        r = (r - r.mean()) / r.std()
        s_pre, s_call = rho(d, "surprise_pct", "pre"), rho(d, "surprise_pct", "call")
        return {"abs x": d["call"].abs().mean() / d["ctl_abs"].mean(), "rank": d["ctl_rank"].mean(),
                "in-call lift": float(x @ y / (x @ x)) if (x @ x) > 0 else np.nan,
                "volume x": (ok["vol"] / ok["ctl_vol"]).median(),
                "flag x": (d["flags"].sum() / d["n"].sum()) / (d["ctl_flags"].sum() / d["ctl_n"].sum())
                if d["ctl_flags"].sum() else np.nan,
                "|pre| %": d["pre"].abs().median() * 100, "|call| %": d["call"].abs().median() * 100,
                "share": (d["call"].abs() / (d["call"].abs() + d["pre"].abs())).median(),
                "ctl share": d["ctl_share"].median(),
                "surprise~pre": s_pre, "surprise~call": s_call, "pre minus call": s_pre - s_call,
                "pre~call": rho(d, "pre", "call"), "call~fwd": rho(d, "call", "fwd"), "pre~fwd": rho(d, "pre", "fwd"),
                "call|pre": np.linalg.lstsq(r[["call", "pre"]].to_numpy(), r["fwd"].to_numpy(), rcond=None)[0][0]
                if len(r) > 3 else np.nan}

    est, lo, hi = one(f), {}, {}
    for by in ("day", "ticker"):
        parts = [g for _, g in f.groupby(by)]
        sims = pd.DataFrame([one(pd.concat([parts[i] for i in rng.integers(0, len(parts), len(parts))]))
                             for _ in range(draws)])
        for k in est:
            a, z = np.nanpercentile(sims[k], [2.5, 97.5]) if sims[k].notna().any() else (np.nan, np.nan)
            lo[k], hi[k] = min(a, lo.get(k, a)), max(z, hi.get(k, z))
    return {k: (v, float(lo[k]), float(hi[k])) for k, v in est.items()}


def stats(f: pd.DataFrame, seed: int = 0, draws: int = BOOT) -> None:
    """Prints the panel's tables: what was dropped, every statistic for all events, for each half of the tickers
    (labels/PANEL.md says what counts as a finding), by session and by other cuts, then the 10-minute profile of
    |return| over the controls' around the call start."""
    start = pd.to_datetime(f["call_start"], utc=True).dt.tz_convert(ET)
    mins = start.dt.hour * 60 + start.dt.minute
    gap = (start - pd.to_datetime(f["accepted"], utc=True)).dt.total_seconds() / 60    # release to call, minutes
    f = f.assign(day=start.dt.date, gap=gap, year=start.dt.year,
                 session=np.select([mins < 570, mins < 960], ["pre-market", "regular"], "after hours"),
                 half=[int(hashlib.sha1(t.encode()).hexdigest(), 16) % 2 for t in f["ticker"]])
    for k in BINS:      # the in-call test's score and cell per bin, cell -1 = not used
        age, clock = gap + k, mins + k
        use = (age >= 0) & (age < MAX_AGE_MIN) & ~f["late_8k"] & f[f"ev_{k}"].notna() & (f[f"ct_{k}"] > 0)
        f[f"y_{k}"] = np.log1p(f[f"ev_{k}"] / f[f"ct_{k}"].where(f[f"ct_{k}"] > 0)).where(use, 0.0)
        f[f"c_{k}"] = ((age // BIN_MIN) * 3 + (clock >= 570) + (clock >= 960)).where(use, -1).astype(int)
    print(f"{len(f)} events, {f['ticker'].nunique()} tickers, {start.min():%Y-%m-%d} to {start.max():%Y-%m-%d}")
    bad = f["thin"] | (f["ctl_days"] < MIN_CTL_DAYS)
    print("  dropped (stale or thin call hour, or under", MIN_CTL_DAYS, "usable control hours), by session:",
          {s: f"{int(bad[f['session'] == s].sum())} of {int((f['session'] == s).sum())}"
           for s in ("pre-market", "regular", "after hours")})
    f = f[~bad].copy()
    used = np.array([f[f"c_{k}"].to_numpy() for k in BINS if k < CALL_MIN])
    print(f"  in-call test: {int((used >= 0).any(axis=0).sum())} events, {int((used[-6:] >= 0).sum())} in-call bins "
          f"and {int((used[:-6] >= 0).sum())} waiting bins")
    big = f["surprise_pct"].abs() >= f["surprise_pct"].abs().median()
    groups = {"all": f, "half A": f[f["half"] == 0], "half B": f[f["half"] == 1],
              **{s: f[f["session"] == s] for s in ("pre-market", "regular", "after hours")},
              "8-K before call": f[~f["late_8k"]], "day stated": f[f["day_src"] != "rule"],
              "not in library": f[~f["in_library"]], "big surprise": f[big],
              "small surprise": f[~big & f["surprise_pct"].notna()],
              "release 4h+ before": f[f["gap"] >= MAX_AGE_MIN],
              **{str(y): f[f["year"] == y] for y in sorted(set(f["year"]))}}
    groups = {name: g for name, g in groups.items() if len(g) >= MIN_GROUP}
    rng = np.random.default_rng(seed)
    table = {name: describe(g, rng, draws) for name, g in groups.items()}
    for cols in (list(table)[:6], list(table)[6:]):
        print(f"\n{'':15}" + "".join(f"{name:>21}" for name in cols))
        print(f"{'events':15}" + "".join(f"{len(groups[name]):>21}" for name in cols))
        for k in table["all"]:
            print(f"{k:15}" + "".join(f"{f'{v:.2f} ({lo:.2f},{hi:.2f})':>21}" for v, lo, hi in
                                      (table[name][k] for name in cols)))
    shown = [k for k in BINS if k >= -60]
    print("\n|return| per 10 minutes from the call start, events' mean over their controls' mean")
    print(f"  {'minute':19}" + "".join(f"{k:>6}" for k in shown))
    for name, g in groups.items():
        cells = [g[[f"ev_{k}", f"ct_{k}"]].dropna() for k in shown]
        print(f"  {name:19}" + "".join(f"{c.iloc[:, 0].mean() / c.iloc[:, 1].mean():>6.2f}" for c in cells))


def main(argv=None):
    """Runs 1 panel step."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=["events", "check", "bars", "stats"])
    args = ap.parse_args(argv)
    load_dotenv(ROOT / ".env")
    if args.step == "events":
        check(build_events())
    elif args.step == "check":
        check(pd.read_csv(EVENTS))
    elif args.step == "bars":
        fetch_bars(pd.read_csv(EVENTS))
    else:
        stats(build_features(pd.read_csv(EVENTS)) if next(BARS.glob("SPY_*"), None) else pd.read_csv(FEATURES))


if __name__ == "__main__":
    main()
