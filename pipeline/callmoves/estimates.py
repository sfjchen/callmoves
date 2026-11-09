"""Step 6b: analyst consensus for the reported quarter, next quarter and fiscal year, and how far each EPS or revenue
claim lands from it. A number already in the release still moves the stock when it beats or misses what analysts
expected.

Alpha Vantage (EARNINGS_ESTIMATES + EARNINGS, 25 requests a day, 1 a second) when ALPHA_VANTAGE_KEY is set, else
yfinance. Every value is a consensus as some provider holds it on some day, so each row carries a basis:
- at_report: the EPS estimate the provider stores with the reported quarter (EARNINGS estimatedEPS, yfinance
  epsEstimate), the number the surprise is measured against. It does not drift the way EARNINGS_ESTIMATES rows for
  past quarters do (WFC Q3 2026 moved 1.8407 -> 1.8558 in the 4 weeks after the report), so it is taken as frozen.
- before_call: a 7/30/60/90-day-ago EPS snapshot dated before the call day, or a value fetched before the call starts.
- after_call: EARNINGS_ESTIMATES is the current consensus, not a frozen one. Fetched after the call, it already reflects
  the call and later revisions, which pull it toward the actual or the guidance, so a gap to it understates the
  surprise. Revenue has no snapshots, so every revenue consensus fetched after the call is after_call. It is shown
  for reference but never scores a claim (match_consensus), since that would use data from after the statement.
Snapshot ages count from the fetch day, Alpha Vantage does not say when it last refreshed them.
The reported quarter's revenue actual comes from yfinance's quarterly income statement, Alpha Vantage's EARNINGS has
only EPS. AV_RAW_DIR, when set, caches the raw responses there so a rerun spends no requests.

    python -m callmoves.estimates AAPL 2026-11-02T17:00-05:00 Q4-2026 --out data/aapl_q3_2026
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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


def _forward_ends(rep_end: date, fy_month: int) -> tuple[date, date]:
    """Returns the next quarter's end and the end of the fiscal year it falls in."""
    nq = (pd.Timestamp(rep_end) + pd.offsets.MonthEnd(3)).date()
    fy = nq.year if nq.month <= fy_month else nq.year + 1
    return nq, (pd.Timestamp(fy, fy_month, 1) + pd.offsets.MonthEnd(0)).date()


def normalize_yf(hist: pd.DataFrame, eps_est: pd.DataFrame, rev_est: pd.DataFrame, trend: pd.DataFrame,
                 call_start: datetime, fetched: datetime, fy_month: int = 12) -> list[Estimate]:
    """Turns yfinance's earnings_history, earnings_estimate, revenue_estimate and eps_trend frames into Estimate rows.
    0q is the quarter after the last one in the history, so it is the reported quarter before the call and the
    next quarter after it. yfinance keeps no past-quarter revenue consensus."""
    call_d = call_start.date()
    if hist is None or hist.empty or eps_est is None or eps_est.empty:
        return []
    ends = sorted(pd.Timestamp(i).date() for i in hist.index)
    q0 = (pd.Timestamp(ends[-1]) + pd.offsets.MonthEnd(3)).date()
    past = [e for e in ends + [q0] if e < call_d and (call_d - e).days <= REPORT_LAG_DAYS]
    if not past:
        return []
    rep_end = past[-1]
    nq, fy_end = _forward_ends(rep_end, fy_month)
    out: list[Estimate] = []
    if rep_end in ends:
        r = hist.loc[[pd.Timestamp(i).date() == rep_end for i in hist.index]].iloc[0]
        if pd.notna(r["epsActual"]) and pd.notna(r["epsEstimate"]):
            out.append(Estimate(period=_label(rep_end, fy_month), period_end=rep_end, role="reported", metric="eps",
                                consensus=float(r["epsEstimate"]), actual=float(r["epsActual"]),
                                surprise_pct=pct_vs(float(r["epsActual"]), float(r["epsEstimate"])),
                                as_of=call_d, basis="at_report", source="yfinance"))
    roles = {rep_end: "reported", nq: "next_quarter"}
    for key, end in (("0q", q0), ("+1q", (pd.Timestamp(q0) + pd.offsets.MonthEnd(3)).date()), ("0y", fy_end)):
        role = "full_year" if key == "0y" else roles.get(end)
        if role is None:
            continue
        label = _label(end, fy_month, year=key == "0y")
        if key in trend.index and pd.notna(trend.loc[key, "current"]):
            t = trend.loc[key]
            v, as_of, basis = _snapshot({0: t["current"], **{n: t[f"{n}daysAgo"] for n in DAYS_AGO}},
                                        call_start, fetched)
            out.append(Estimate(period=label, period_end=end, role=role, metric="eps", consensus=v, as_of=as_of,
                                basis=basis, source="yfinance"))
        if rev_est is not None and key in rev_est.index and pd.notna(rev_est.loc[key, "avg"]):
            v, as_of, basis = _snapshot({0: rev_est.loc[key, "avg"]}, call_start, fetched)
            out.append(Estimate(period=label, period_end=end, role=role, metric="revenue", consensus=v, as_of=as_of,
                                basis=basis, source="yfinance"))
    return out


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
    nq, fy_end = _forward_ends(rep_end, fy_month)
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


def fetch_estimates(ticker: str, call_start: datetime, *, av_key: str | None = None) -> list[Estimate]:
    """Fetches and normalizes consensus, Alpha Vantage first when a key is set. Any failure or an unknown ticker
    returns an empty list so the pipeline runs without estimates. Error text never carries the key."""
    fetched = datetime.now(timezone.utc)
    raw = Path(os.environ["AV_RAW_DIR"]) if os.environ.get("AV_RAW_DIR") else None
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
    except Exception as e:  # noqa: BLE001
        print(f"yfinance unavailable: {str(e)[:160]}")
        t = None
    if av_key:
        try:
            import httpx

            def get(fn: str, want: str) -> dict:
                """Returns 1 Alpha Vantage response from the raw cache, else fetches it and caches it if it holds
                data. A rate-limit notice comes back as 200 with an 'Information' field instead of data."""
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

            rev = {}
            try:
                inc = t.quarterly_income_stmt
                rev = {(c.year, c.month): float(v) for c, v in inc.loc["Total Revenue"].items() if pd.notna(v)}
            except Exception as e:  # noqa: BLE001
                print(f"yfinance revenue actuals failed: {str(e)[:160]}")
            est = get("EARNINGS_ESTIMATES", "estimates")["estimates"]
            q = get("EARNINGS", "quarterlyEarnings")["quarterlyEarnings"]
            # snapshot ages count from when the raw response was fetched, not from this run
            if raw and (raw / f"av_{ticker}_EARNINGS_ESTIMATES.json").exists():
                mtime = (raw / f"av_{ticker}_EARNINGS_ESTIMATES.json").stat().st_mtime
                fetched = datetime.fromtimestamp(mtime, tz=timezone.utc)
            rows = normalize_av(est, q, call_start, fetched, rev)
            if rows:
                return rows
        except Exception as e:  # noqa: BLE001
            print(f"alpha vantage estimates failed, trying yfinance: {str(e).replace(av_key, '***')[:200]}")
    try:
        hist = t.earnings_history
        if hist is None or hist.empty:
            return []
        fy_ts = t.info.get("lastFiscalYearEnd")
        fy_month = datetime.fromtimestamp(fy_ts, tz=timezone.utc).month if fy_ts else 12
        return normalize_yf(hist, t.earnings_estimate, t.revenue_estimate, t.eps_trend, call_start, fetched, fy_month)
    except Exception as e:  # noqa: BLE001
        print(f"yfinance estimates failed, running without: {str(e)[:160]}")
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
_METRIC = {"eps": re.compile(r"\b(eps|earnings per share)\b", re.I),
           "revenue": re.compile(rf"^(?:{_QUAL}\s+)*revenues?(?:\s+(?:guidance|outlook|range|forecast))?$", re.I)}


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


def main(argv=None):
    """Builds data/<call>/estimates.json ahead of call.json, with the same cache key run.py reads, replacing any
    existing cache. Prints the rows."""
    import argparse

    from dotenv import load_dotenv

    from .models import CallMeta
    from .run import _estimates

    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("call_start", type=datetime.fromisoformat, help="tz-aware, e.g. 2026-10-13T10:00-04:00")
    ap.add_argument("quarter", help="the call's fiscal quarter, e.g. Q4-2026, checked against the reported row")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.call_start.tzinfo is None:
        ap.error("call_start needs a UTC offset")
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "estimates.json").unlink(missing_ok=True)
    rows = _estimates(a.out, CallMeta(ticker=a.ticker, quarter=a.quarter, call_start=a.call_start))
    want = "Q{} FY{}".format(*a.quarter.upper().lstrip("Q").split("-"))
    if not any(e.role == "reported" and e.period == want for e in rows):
        print(f"warning: no reported row for {want}")
    for e in rows:
        print(f"  {e.period:10} {e.role:12} {e.metric:7} cons={e.consensus:<16g} act={e.actual} "
              f"surp={e.surprise_pct if e.surprise_pct is None else f'{e.surprise_pct:+.2%}'} {e.basis} {e.as_of}")


if __name__ == "__main__":
    main()
