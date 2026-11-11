"""Analyst consensus for the reported quarter, next quarter and fiscal year, and how far each EPS or revenue
claim lands from it. Source: Alpha Vantage (25 requests a day), no key or a failed fetch means no estimates. Each row
carries a basis: at_report (the EPS estimate stored with the reported quarter, taken as frozen), before_call (a dated
snapshot or a fetch before the call) or after_call (the current value, which already reflects the call, so it is
shown but never scores a claim). The reported quarter's revenue actual comes from yfinance, AV has only EPS.
AV_RAW_DIR, when set, caches the raw responses so a rerun spends no requests.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from .models import Claim, Estimate
from .novelty import _num_values

AV_URL = "https://www.alphavantage.co/query"
REPORT_LAG_DAYS = 75     # a call reports a quarter that ended at most this long before it
MAX_GAP = 0.5            # a spoken figure more than 50% off consensus is some other line item, not the headline
# a reported-quarter revenue consensus this far off the actual belongs to another quarter: AV's AAPL row dated
# 2026-09-30 holds the holiday quarter (EPS 2.90 vs 1.98 at report, revenue 155.6B vs 114.5B actual)
REV_SANITY = 0.15
EPS_BASIS_GAP = 0.25     # at_report vs EARNINGS_ESTIMATES EPS for the same quarter: drift stays within 3%
DAYS_AGO = (7, 30, 60, 90)


def pct_vs(value: float, consensus: float) -> float:
    """Returns the signed distance from consensus as a fraction of it, 0.032 = 3.2% above."""
    return (value - consensus) / abs(consensus)


def _label(end: date, fy_month: int, year: bool = False) -> str:
    """Names a period by its fiscal year, which ends in fy_month: Q1 is the first quarter after that month."""
    fy = end.year if end.month <= fy_month else end.year + 1
    return f"FY{fy}" if year else f"Q{(end.month - fy_month - 1) % 12 // 3 + 1} FY{fy}"


def _snapshot(snaps: dict[int, float | None], call_start: datetime, fetched: datetime) -> tuple[float, date, str]:
    """Picks the newest value taken before the call ({days ago: value}). A dated snapshot has no time of day, so it
    counts only when its day precedes the call day. The current value (0 days ago) counts when fetched before the
    call starts, else it comes back as after_call. Days count in the call's time zone."""
    fetched = fetched.astimezone(call_start.tzinfo)
    for n in sorted(snaps):
        if snaps[n] is None or pd.isna(snaps[n]):
            continue
        if fetched < call_start if n == 0 else (fetched - timedelta(n)).date() < call_start.date():
            return float(snaps[n]), (fetched - timedelta(n)).date(), "before_call"
    return float(snaps[0]), fetched.date(), "after_call"


def _f(x) -> float | None:
    """Parses an Alpha Vantage field, which is a string, 'None' or missing."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def normalize_av(est: list[dict], quarterly: list[dict], call_start: datetime, fetched: datetime,
                 rev_actual: dict[tuple[int, int], float] | None = None) -> list[Estimate]:
    """Turns Alpha Vantage EARNINGS_ESTIMATES rows and EARNINGS quarterlyEarnings rows into Estimate rows.
    The reported quarter is the EARNINGS row reported within 3 days of the call, its EPS consensus and actual come
    from there. Periods match by year and month, since a 52-week fiscal year (Apple) ends on varying days.
    rev_actual maps (year, month) of a quarter end to its reported revenue, a reported-quarter revenue row more
    than REV_SANITY off it is dropped as mislabeled."""
    call_d = call_start.date()
    fy_rows = sorted(date.fromisoformat(r["date"]) for r in est if "year" in r.get("horizon", ""))
    fy_month = fy_rows[0].month if fy_rows else 12
    by_end = {(d.year, d.month, "year" in r.get("horizon", "")): (d, r)
              for r in est for d in [date.fromisoformat(r["date"])]}
    rep = next((q for q in quarterly if q.get("reportedDate")
                and abs((date.fromisoformat(q["reportedDate"]) - call_d).days) <= 3), None)
    if rep:
        rep_end = date.fromisoformat(rep["fiscalDateEnding"])
    else:
        past = [d for d, r in by_end.values() if "year" not in r.get("horizon", "")
                and d < call_d and (call_d - d).days <= REPORT_LAG_DAYS]
        if not past:
            return []
        rep_end = max(past)
    nq = (pd.Timestamp(rep_end) + pd.offsets.MonthEnd(3)).date()   # the next quarter's end
    nq_fy = nq.year if nq.month <= fy_month else nq.year + 1
    fy_end = (pd.Timestamp(nq_fy, fy_month, 1) + pd.offsets.MonthEnd(0)).date()   # end of the fiscal year it is in
    out: list[Estimate] = []
    cons = _f(rep.get("estimatedEPS")) if rep else None
    # EARNINGS and EARNINGS_ESTIMATES can define EPS differently (W Q3 2026: 0.31 vs 0.81, DUOL 0.55 vs 1.59). When
    # the estimates row is this quarter's by its revenue, an EPS gap over EPS_BASIS_GAP means another definition, and
    # the at_report row is dropped so every EPS row shares the definition the forward rows use.
    _, est_rep = by_end.get((rep_end.year, rep_end.month, False), (None, None))
    rev_act = (rev_actual or {}).get((rep_end.year, rep_end.month))
    if cons is not None and est_rep and rev_act is not None:
        est_eps, est_rev = _f(est_rep.get("eps_estimate_average")), _f(est_rep.get("revenue_estimate_average"))
        if (est_eps is not None and est_rev is not None and abs(pct_vs(rev_act, est_rev)) <= REV_SANITY
                and abs(pct_vs(est_eps, cons)) > EPS_BASIS_GAP):
            cons = None
    if cons is not None:
        act = _f(rep.get("reportedEPS"))
        out.append(Estimate(period=_label(rep_end, fy_month), period_end=rep_end, role="reported", metric="eps",
                            consensus=cons, actual=act, surprise_pct=pct_vs(act, cons) if act is not None else None,
                            as_of=date.fromisoformat(rep["reportedDate"]), basis="at_report", source="alphavantage"))
    for role, end, yr in (("reported", rep_end, False), ("next_quarter", nq, False), ("full_year", fy_end, True)):
        d, r = by_end.get((end.year, end.month, yr), (None, None))
        if r is None:
            continue
        label = _label(end, fy_month, year=yr)
        if not (role == "reported" and out) and _f(r.get("eps_estimate_average")) is not None:
            snaps = {0: _f(r["eps_estimate_average"]),
                     **{n: _f(r.get(f"eps_estimate_average_{n}_days_ago")) for n in DAYS_AGO}}
            v, as_of, basis = _snapshot(snaps, call_start, fetched)
            out.append(Estimate(period=label, period_end=d, role=role, metric="eps", consensus=v, as_of=as_of,
                                basis=basis, source="alphavantage"))
        if _f(r.get("revenue_estimate_average")) is not None:
            v, as_of, basis = _snapshot({0: _f(r["revenue_estimate_average"])}, call_start, fetched)
            act = (rev_actual or {}).get((end.year, end.month)) if role == "reported" else None
            if act is not None and abs(pct_vs(act, v)) > REV_SANITY:
                continue
            out.append(Estimate(period=label, period_end=d, role=role, metric="revenue", consensus=v, actual=act,
                                surprise_pct=pct_vs(act, v) if act is not None else None, as_of=as_of, basis=basis,
                                source="alphavantage"))
    return out


def fetch_estimates(ticker: str, call_start: datetime, av_key: str | None) -> list[Estimate]:
    """Fetches and normalizes Alpha Vantage consensus. No key, a failure or an unknown ticker returns an empty list
    so the pipeline runs without estimates. Error text never carries the key."""
    if not av_key:
        return []
    raw = Path(os.environ["AV_RAW_DIR"]) if os.environ.get("AV_RAW_DIR") else None

    def get(fn: str, want: str) -> dict:
        """Returns 1 Alpha Vantage response from the raw cache, else fetches it and caches it if it holds data.
        A rate-limit notice comes back as 200 with an 'Information' field instead of data."""
        path = raw / f"av_{ticker}_{fn}.json" if raw else None
        if path and path.exists():
            return json.loads(path.read_text())
        time.sleep(1.2)
        d = httpx.get(AV_URL, params={"function": fn, "symbol": ticker, "apikey": av_key}, timeout=20).json()
        if want not in d:
            raise RuntimeError(f"{fn}: {str(d)[:200]}")
        if path:
            path.write_text(json.dumps(d, indent=1))
        return d

    try:
        est = get("EARNINGS_ESTIMATES", "estimates")["estimates"]
        q = get("EARNINGS", "quarterlyEarnings")["quarterlyEarnings"]
        # snapshot ages count from when the raw response was fetched, not from this run
        cached = raw / f"av_{ticker}_EARNINGS_ESTIMATES.json" if raw else None
        fetched = datetime.fromtimestamp(cached.stat().st_mtime, tz=UTC) if cached else datetime.now(UTC)
        # reported revenue by (year, month) of the quarter end from yfinance, empty on failure
        try:
            import yfinance as yf

            inc = yf.Ticker(ticker).quarterly_income_stmt
            rev = {(c.year, c.month): float(v) for c, v in inc.loc["Total Revenue"].items() if pd.notna(v)}
        except Exception as e:  # noqa: BLE001
            print(f"yfinance revenue actuals failed: {str(e)[:160]}")
            rev = {}
        return normalize_av(est, q, call_start, fetched, rev)
    except Exception as e:  # noqa: BLE001
        print(f"alpha vantage estimates failed, running without: {str(e).replace(av_key, '***')[:200]}")
        return []


_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def _role(claim: Claim, quarter_roles: dict[int, str]) -> str | None:
    """Resolves a claim's period ('Q3', 'fourth quarter', 'FY', 'next quarter', none for a result) to an Estimate
    role."""
    p = (claim.period or "").lower()
    if re.search(r"\bnext (year|fiscal year)\b", p):
        return None
    if re.search(r"\bnext quarter\b", p):
        return "next_quarter"
    if re.search(r"\b(fy|full[- ]year|fiscal year|fy\d{4}|full year \d{4})\b", p):
        return "full_year"
    q = re.search(r"\bq([1-4])\b|\b(first|second|third|fourth) quarter\b", p)
    if q:
        return quarter_roles.get(int(q.group(1)) if q.group(1) else _ORDINAL[q.group(2)])
    if re.search(r"\b(this|current|the) quarter\b", p) or (not p and claim.kind == "result"):
        return "reported"
    return None


# total company revenue, optionally qualified by a period or 'guidance', never a segment or a growth rate
_QUAL = r"(?:q[1-4]|fy\s?\d{0,4}|\d{4}|full year|fiscal year|quarterly|(?:first|second|third|fourth) quarter|total|net"\
    r"|consolidated|reported|company)"
_METRIC = {"eps": re.compile(r"\b(eps|earnings per share)\b", re.IGNORECASE),
           "revenue": re.compile(rf"^(?:{_QUAL}\s+)*revenues?(?:\s+(?:guidance|outlook|range|forecast))?$",
                                re.IGNORECASE)}


def match_consensus(claims: list[Claim], estimates: list[Estimate]) -> list[Claim]:
    """Sets vs_consensus_pct on each EPS or revenue claim whose period resolves to an estimate, using the spoken
    figure closest to consensus. EPS needs a dollar sign. Revenue takes any figure of 100,000 or more, so
    '$21.4 billion', '21.4 billion' and a release-style '$ 21,870' (millions) all compare with a consensus in dollars.
    An after_call consensus already reflects the call, so it never scores a claim. Other claims, and every claim when
    estimates are empty, come back unchanged."""
    if not estimates:
        return claims
    quarter_roles = {int(e.period[1]): e.role for e in estimates if e.role != "full_year"}
    by_key = {(e.role, e.metric): e for e in estimates if e.basis != "after_call"}
    out = []
    for c in claims:
        name = re.sub(r"[^a-z0-9]+", " ", c.metric.lower()).strip()
        metric = next((m for m, rx in _METRIC.items() if rx.search(name)), None)
        role = _role(c, quarter_roles) if metric else None
        e = by_key.get((role, metric))
        text = c.value or c.quote
        vals = ({v for v in _num_values(text) if v >= 1e5} if metric == "revenue"
                else _num_values(text, dollars_only=True)) if e else set()
        if vals:
            v = min(vals, key=lambda x: abs(x - e.consensus))
            gap = pct_vs(v, e.consensus)
            if abs(gap) <= MAX_GAP:
                c = c.model_copy(update={"vs_consensus_pct": gap, "consensus_role": role})
        out.append(c)
    return out
