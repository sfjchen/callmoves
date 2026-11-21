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
from datetime import UTC, datetime, timedelta
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
BOOT = 2000
PROFILE_STEP = 10
PROFILE = range(-30, 90, PROFILE_STEP)  # 10-minute bins from 30 minutes before the call to 90 after its start
KEYWORD = re.compile(r"conference call|webcast|earnings call|teleconference|call to discuss|investor call|"
                     r"call with (?:analysts|investors)|host a call|live call|video call", re.IGNORECASE)
CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?", re.IGNORECASE)
ZONES = {"eastern": 0, "et": 0, "est": 0, "edt": 0, "central": 1, "ct": 1, "cst": 1, "cdt": 1, "mountain": 2,
         "mt": 2, "mst": 2, "mdt": 2, "pacific": 3, "pt": 3, "pst": 3, "pdt": 3}   # hours behind US Eastern
ZONE = re.compile(r"\b(" + "|".join(ZONES) + r")\b", re.IGNORECASE)
KEY_REACH = 250      # characters between the call keyword and its time
ZONE_REACH = 40      # characters between a time and its zone
REPLAY_REACH = 80    # "replay" this close before the keyword or the time marks a replay's time, not the call's
LATE_TOL_MIN = 15    # a release accepted this long after the stated call time still belongs to that call


def _get(url: str, cache: Path, wait: float = SEC_WAIT_S) -> bytes | None:
    """Returns the body of an SEC URL, from the cache when present, None on a 404 or an oversized file. SEC refuses
    requests without a contact User-Agent, so SEC_USER_AGENT must be set."""
    if cache.exists():
        return cache.read_bytes()
    for attempt in range(NET_RETRIES + 1):
        time.sleep(wait if not attempt else NET_WAIT_S)
        try:
            r = httpx.get(url, headers={"User-Agent": os.environ["SEC_USER_AGENT"]}, timeout=60,
                          follow_redirects=True)
        except httpx.TransportError:    # a dropped connection or failed lookup, common on a long pull
            if attempt == NET_RETRIES:
                raise
            continue
        if r.status_code not in (429, 500, 502, 503) or attempt == NET_RETRIES:
            break
    if r.status_code == 404 or len(r.content) > MAX_DOC_BYTES:
        return None
    r.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(r.content)
    return r.content


def call_time(text: str) -> tuple[int, int] | None:
    """Returns the call's start as (hour, minute) US Eastern from a release's text, None when it states none. A time
    counts when a zone follows it within ZONE_REACH characters and a call keyword sits within KEY_REACH, and no
    "replay" stands between or just before them. The time nearest its keyword wins, and both halves of a pair such
    as "2:00 p.m. PT (5:00 p.m. ET)" read the same Eastern time."""
    keys = [m.start() for m in KEYWORD.finditer(text)]
    best = None
    for m in CLOCK.finditer(text):
        zone = ZONE.search(text, m.end(), m.end() + ZONE_REACH)
        hour = int(m.group(1))
        if not zone or not keys or not 1 <= hour <= 12 or int(m.group(2) or 0) > 59:
            continue
        key = min(keys, key=lambda k: abs(k - m.start()))
        dist = abs(key - m.start())
        if dist > KEY_REACH or "replay" in text[min(key, m.start()) - REPLAY_REACH:m.start()].lower():
            continue
        h24 = hour % 12 + (12 if m.group(3).lower() == "p" else 0) + ZONES[zone.group(1).lower()]
        if best is None or dist < best[0]:
            best = (dist, h24, int(m.group(2) or 0))
    return (best[1], best[2]) if best and best[1] < 24 else None


def call_start(accepted: datetime, clock: tuple[int, int]) -> tuple[datetime, bool]:
    """Returns the call's start and whether the filing reached EDGAR after it. The call is on the release's own day
    unless the release came after 15:30 Eastern and the call is before noon, which is the next weekday's morning. A
    same-day time more than LATE_TOL_MIN before the acceptance means the 8-K was filed after the call began."""
    acc = accepted.astimezone(ET)
    start = acc.replace(hour=clock[0], minute=clock[1], second=0, microsecond=0)
    if acc.hour * 60 + acc.minute >= 930 and clock[0] < 12:
        start += timedelta(days=1)
        while start.weekday() >= 5:
            start += timedelta(days=1)
    return start, start < acc - timedelta(minutes=LATE_TOL_MIN)


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


def release_clock(cik: int, accession: str) -> tuple[int, int] | None:
    """Returns the call time stated in any HTML document of 1 filing, the smallest documents first since the
    release is shorter than the financial supplement."""
    acc = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
    index = _get(f"{base}/index.json", RAW / "doc" / acc / "index.json")
    if index is None:
        return None
    items = [i for i in json.loads(index)["directory"]["item"] if i["name"].lower().endswith((".htm", ".html"))]
    for item in sorted(items, key=lambda i: int(i.get("size") or 0)):
        raw = _get(f"{base}/{item['name']}", RAW / "doc" / acc / item["name"])
        clock = call_time(html_to_text(raw.decode("utf-8", "replace"))) if raw else None
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
            clock = release_clock(ciks[tk], f.accession)
            if clock is None:
                no_time += 1
                continue
            start, late = call_start(f.accepted.to_pydatetime(), clock)
            day = pd.Timestamp(start.date())
            near = sur[(sur["day"] - day).abs() <= pd.Timedelta(days=1)]
            s = near.iloc[0] if len(near) else {}
            rows.append({"ticker": tk, "accession": f.accession, "accepted": f.accepted.isoformat(),
                         "call_start": start.isoformat(), "late_8k": late, "eps_est": s.get("eps_est"),
                         "eps": s.get("eps"), "surprise_pct": s.get("surprise_pct")})
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


def window(b: pd.DataFrame, t0: datetime, minutes: int = CALL_MIN) -> dict:
    """Returns what the stock did in the `minutes` after t0: the adjusted return (the first bar carries the move
    since the last trade before t0), the summed |1-minute adjusted return|, volume, bars and flagged minutes."""
    w = b[(b["ts"] > t0) & (b["ts"] <= t0 + timedelta(minutes=minutes))]
    return {"ret": float((1 + w["adj_ret"]).prod() - 1), "rv": float(w["adj_ret"].abs().sum()),
            "vol": float(w["vol"].sum()), "n": len(w), "flags": int(w["flagged"].sum())}


def features(b: pd.DataFrame, start: datetime) -> dict | None:
    """Returns 1 event's features from its bars (ts, px, vol, etf_px with SPY as the benchmark), None when the
    stock has no regular close before the call or nothing trades in the call hour.

    - ann: adjusted return from the last regular close before the call to the call's start, the release's move.
    - call: the CALL_MIN minutes from the start (`window`), and thin when under MIN_COVER of them traded.
    - after: from the end of that hour to the next regular close, next: from that close to the one after.
    - ctl_*: the same clock hour on each of the CONTROLS sessions before the call day, the event's own baseline.
    - ev_k and ct_k: summed |return| in the 10 minutes from minute k of the call, and the controls' median."""
    b = market.flag_abnormal(market.adjusted_returns(b))
    et = b["ts"].dt.tz_convert(ET)
    close = b[market.session_key(b["ts"]).str.endswith("-1")].groupby(et.dt.date).tail(1).set_index("ts")
    end = start + timedelta(minutes=CALL_MIN)

    def span(t0: datetime, t1: datetime) -> float:
        """Returns the adjusted return between the last trades at or before t0 and t1, NaN when either is missing."""
        a, z = b[b["ts"] <= t0].tail(1), b[b["ts"] <= t1].tail(1)
        if a.empty or z.empty:
            return float("nan")
        return float(z["px"].iloc[0] / a["px"].iloc[0] - z["etf_px"].iloc[0] / a["etf_px"].iloc[0])

    before, later = close[close.index < start], close[close.index >= end]
    call = window(b, start)
    if before.empty or not call["n"]:
        return None
    days = [d for d in sorted(set(et.dt.date)) if d < start.astimezone(ET).date()][-CONTROLS:]
    at = start.astimezone(ET)
    ctl_t = [at.replace(year=d.year, month=d.month, day=d.day) for d in days]   # same wall clock, DST-safe
    ctl = pd.DataFrame([window(b, t) for t in ctl_t])
    out = {"ann": span(before.index[-1], start), "call": call["ret"], "rv": call["rv"], "vol": call["vol"],
           "n": call["n"], "flags": call["flags"], "thin": call["n"] < MIN_COVER * CALL_MIN,
           "after": span(end, later.index[0]) if len(later) else float("nan"),
           "next": span(later.index[0], later.index[1]) if len(later) > 1 else float("nan"),
           "ctl_days": len(ctl), "ctl_absret": float(ctl["ret"].abs().mean()) if len(ctl) else float("nan"),
           "ctl_rv": float(ctl["rv"].median()) if len(ctl) else float("nan"),
           "ctl_vol": float(ctl["vol"].median()) if len(ctl) else float("nan"),
           "ctl_n": int(ctl["n"].sum()) if len(ctl) else 0, "ctl_flags": int(ctl["flags"].sum()) if len(ctl) else 0}
    for k in PROFILE:
        out[f"ev_{k}"] = window(b, start + timedelta(minutes=k), PROFILE_STEP)["rv"]
        out[f"ct_{k}"] = float(pd.Series([window(b, t + timedelta(minutes=k), PROFILE_STEP)["rv"]
                                          for t in ctl_t]).median()) if ctl_t else float("nan")
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
            rows.append({"ticker": e.ticker, "call_start": e.call_start, "late_8k": e.late_8k,
                         "surprise_pct": e.surprise_pct, "in_library": (e.ticker, e.call_start[:10]) in lib, **f})
    out = pd.DataFrame(rows)
    out.to_csv(FEATURES, index=False)
    print(f"{len(out)} of {len(df)} events have features -> {FEATURES}")
    return out


def describe(f: pd.DataFrame, rng: np.random.Generator, draws: int = BOOT) -> dict[str, tuple[float, float, float]]:
    """Returns each panel statistic for 1 group of events as (value, low, high), the interval from a bootstrap over
    call days, since calls on 1 day share the market's news.

    - rv x, volume x: the median event's call-hour summed |return| and volume over its own control hours' median.
    - flag x: flagged minutes per traded minute in call hours over the same in control hours.
    - |ann| %, |call| %: median absolute adjusted return of the release window and of the call hour.
    - call share: median of |call| / (|ann| + |call|).
    - the rank correlations (Spearman): surprise with ann and with call, ann with call, and call and ann each with
      fwd, the return from the end of the call hour over the next 2 regular closes.
    - call | ann: the call's coefficient when fwd's rank is regressed on the ranks of call and ann together, so a
      call move that only continues the release's move scores 0."""
    def rho(d: pd.DataFrame, x: str, y: str) -> float:
        """Returns the rank correlation of 2 columns over the rows that have both."""
        return d[[x, y]].dropna().corr(method="spearman").iloc[0, 1]

    def one(d: pd.DataFrame) -> dict[str, float]:
        """Returns every statistic for 1 sample."""
        ok = d[(d["ctl_rv"] > 0) & (d["ctl_vol"] > 0)]
        r = d[["fwd", "call", "ann"]].dropna().rank()
        r = (r - r.mean()) / r.std()
        beta = np.linalg.lstsq(r[["call", "ann"]].to_numpy(), r["fwd"].to_numpy(), rcond=None)[0][0] if len(r) > 3 \
            else np.nan
        return {"rv x": (ok["rv"] / ok["ctl_rv"]).median(), "volume x": (ok["vol"] / ok["ctl_vol"]).median(),
                "flag x": (d["flags"].sum() / d["n"].sum()) / (d["ctl_flags"].sum() / d["ctl_n"].sum())
                if d["ctl_flags"].sum() else np.nan,
                "|ann| %": d["ann"].abs().median() * 100, "|call| %": d["call"].abs().median() * 100,
                "call share": (d["call"].abs() / (d["call"].abs() + d["ann"].abs())).median(),
                "surprise~ann": rho(d, "surprise_pct", "ann"), "surprise~call": rho(d, "surprise_pct", "call"),
                "ann~call": rho(d, "ann", "call"), "call~fwd": rho(d, "call", "fwd"), "ann~fwd": rho(d, "ann", "fwd"),
                "call|ann": beta}

    days = [g for _, g in f.groupby("day")]
    sims = pd.DataFrame([one(pd.concat([days[i] for i in rng.integers(0, len(days), len(days))]))
                         for _ in range(draws)])
    return {k: (v, float(np.nanpercentile(sims[k], 2.5)), float(np.nanpercentile(sims[k], 97.5)))
            for k, v in one(f).items()}


def stats(f: pd.DataFrame, seed: int = 0) -> None:
    """Prints the panel's tables: every statistic for all events, for each half of the tickers (a claim counts only
    when both halves agree, see labels/PANEL.md), by session and by other cuts, then the 10-minute activity profile
    around the call start."""
    start = pd.to_datetime(f["call_start"], utc=True).dt.tz_convert(ET)
    mins = start.dt.hour * 60 + start.dt.minute
    f = f.assign(day=start.dt.date, fwd=f["after"] + f["next"],
                 session=np.select([mins < 570, mins < 960], ["pre-market", "regular"], "after hours"),
                 half=[int(hashlib.sha1(t.encode()).hexdigest(), 16) % 2 for t in f["ticker"]])
    print(f"{len(f)} events, {f['ticker'].nunique()} tickers, {start.min():%Y-%m-%d} to {start.max():%Y-%m-%d}")
    print(f"  dropped from every table: {int(f['thin'].sum())} with a trade in under {MIN_COVER:.0%} of the call "
          f"hour, {int((f['ctl_days'] < MIN_CTL_DAYS).sum())} with under {MIN_CTL_DAYS} control sessions")
    f = f[~f["thin"] & (f["ctl_days"] >= MIN_CTL_DAYS)]
    big = f["surprise_pct"].abs() >= f["surprise_pct"].abs().median()
    groups = {"all": f, "half A": f[f["half"] == 0], "half B": f[f["half"] == 1],
              **{s: f[f["session"] == s] for s in ("pre-market", "regular", "after hours")},
              "8-K before call": f[~f["late_8k"]], "not in library": f[~f["in_library"]],
              "big surprise": f[big], "small surprise": f[~big & f["surprise_pct"].notna()],
              **{str(y): f[start.loc[f.index].dt.year == y] for y in sorted(set(start.loc[f.index].dt.year))}}
    rng = np.random.default_rng(seed)
    table = {name: describe(g, rng) for name, g in groups.items() if len(g) >= MIN_GROUP}
    print(f"\n{'':16}" + "".join(f"{name[:17]:>20}" for name in table))
    print(f"{'events':16}" + "".join(f"{len(groups[name]):>20}" for name in table))
    for k in next(iter(table.values())):
        print(f"{k:16}" + "".join(f"{f'{v:.2f} ({lo:.2f},{hi:.2f})':>20}" for v, lo, hi in
                                  (table[name][k] for name in table)))
    print("\nactivity by 10 minutes from the call start: median event's summed |return| over its controls' median")
    for name in ("all", "half A", "half B", "pre-market", "regular", "after hours"):
        g = groups[name]
        cells = []
        for k in PROFILE:
            r = (g[f"ev_{k}"] / g[f"ct_{k}"].where(g[f"ct_{k}"] > 0)).dropna()
            cells.append(f"{r.median():>6.2f}")
        print(f"  {name:12}" + "".join(cells))
    print(f"  {'minute':12}" + "".join(f"{k:>6}" for k in PROFILE))


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
