import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from callmoves import market
from callmoves.estimates import match_consensus, normalize_av, normalize_yf
from callmoves.models import Claim, Estimate, Segment
from callmoves.score import rank_moments
from conftest import T0, make_bars

CALL, FETCHED = T0, T0 + timedelta(days=26)   # 26 days later: the 30-day snapshot predates the call
ET5 = timezone(timedelta(hours=-5))


def _yf_frames():
    hist = pd.DataFrame({"epsActual": [2.0, 1.87], "epsEstimate": [1.72, 1.80]},
                        index=pd.to_datetime(["2026-06-30", "2026-09-30"]))
    eps = pd.DataFrame({"avg": [1.79, 1.8, 7.34, 8.0]}, index=["0q", "+1q", "0y", "+1y"])
    rev = pd.DataFrame({"avg": [22.4e9, 22.8e9, 88.2e9, 92.9e9]}, index=["0q", "+1q", "0y", "+1y"])
    trend = pd.DataFrame({"current": [1.79, 1.8, 7.34, 8.0], "7daysAgo": [1.79, 1.8, 7.34, 8.0],
                          "30daysAgo": [1.75, 1.8, 7.30, 8.0], "60daysAgo": [1.7] * 4, "90daysAgo": [1.7] * 4},
                         index=["0q", "+1q", "0y", "+1y"])
    return hist, eps, rev, trend


def test_yfinance_rows_after_the_call_use_the_pre_call_eps_snapshot_and_mark_revenue_post_call():
    rows = {(e.role, e.metric): e for e in normalize_yf(*_yf_frames(), CALL, FETCHED)}
    rep = rows[("reported", "eps")]
    assert (rep.period, rep.consensus, rep.actual, rep.basis) == ("Q3 FY2026", 1.80, 1.87, "at_report")
    assert abs(rep.surprise_pct - 0.07 / 1.80) < 1e-9
    nq = rows[("next_quarter", "eps")]
    assert (nq.period, nq.consensus, nq.as_of, nq.basis) == ("Q4 FY2026", 1.75, date(2026, 10, 9), "before_call")
    assert rows[("full_year", "eps")].period == "FY2026"
    assert rows[("next_quarter", "revenue")].basis == "after_call" and ("reported", "revenue") not in rows


def test_snapshot_on_the_call_day_is_not_before_the_call_but_a_fetch_an_hour_before_it_is():
    hist, eps, rev, trend = _yf_frames()
    trend = trend.assign(**{"7daysAgo": [1.78] * 4})
    on_day = {(e.role, e.metric): e for e in normalize_yf(hist, eps, rev, trend, CALL, CALL + timedelta(days=7))}
    assert on_day[("next_quarter", "eps")].consensus == 1.75   # the 7-day snapshot is dated the call day
    early = {(e.role, e.metric): e for e in normalize_yf(hist.iloc[:1], eps, rev, trend, CALL,
                                                         CALL - timedelta(hours=1))}
    assert early[("reported", "eps")].basis == "before_call" and early[("reported", "eps")].consensus == 1.79


def test_alpha_vantage_reported_quarter_comes_from_the_earnings_row_reported_on_the_call_day():
    est = [{"date": d, "horizon": h, "eps_estimate_average": eps, "eps_estimate_average_30_days_ago": "1.7",
            "revenue_estimate_average": rev} for d, h, eps, rev in (
        ("2026-09-30", "fiscal quarter", "1.85", "21.9e9"), ("2026-12-31", "fiscal quarter", "1.79", "22.4e9"),
        ("2026-12-31", "fiscal year", "7.34", "88.2e9"))]
    q = [{"fiscalDateEnding": "2026-09-30", "reportedDate": "2026-10-13", "reportedEPS": "1.87",
          "estimatedEPS": "1.80"}]
    rows = {(e.role, e.metric): e for e in normalize_av(est, q, CALL, FETCHED, {(2026, 9): 21.87e9})}
    assert rows[("reported", "eps")].consensus == 1.80 and rows[("reported", "eps")].actual == 1.87
    assert rows[("reported", "eps")].basis == "at_report"
    r = rows[("reported", "revenue")]
    assert (r.consensus, r.actual, r.basis) == (21.9e9, 21.87e9, "after_call")
    assert abs(r.surprise_pct + 0.03 / 21.9) < 1e-9
    assert rows[("next_quarter", "eps")].consensus == 1.7 and rows[("full_year", "eps")].period == "FY2026"


def test_alpha_vantage_apple_maps_the_september_quarter_to_q4_and_drops_its_mislabeled_revenue_row():
    av = json.loads((Path(__file__).parent.parent / "fixtures" / "av_aapl_q4_2026.json").read_text())
    call, fetched = datetime(2026, 11, 2, 17, tzinfo=ET5), datetime(2026, 11, 9, 2, tzinfo=ET5)
    rows = {(e.role, e.metric): e for e in normalize_av(av["estimates"], av["quarterlyEarnings"], call, fetched,
                                                        {(2026, 9): 114.477e9})}
    rep = rows[("reported", "eps")]
    assert (rep.period, rep.consensus, rep.actual) == ("Q4 FY2026", 1.98, 2.05)
    assert ("reported", "revenue") not in rows   # AV's 2026-09-30 row is the holiday quarter, 26% off the actual
    assert rows[("next_quarter", "eps")].period == "Q1 FY2027" and rows[("full_year", "eps")].period == "FY2027"
    # the 7-day snapshot falls on the call day, so the 30-day one is used
    assert rows[("next_quarter", "eps")].as_of == date(2026, 10, 10)



def test_alpha_vantage_drops_an_at_report_eps_on_another_definition_than_the_estimates_row():
    est = [{"date": "2026-09-30", "horizon": "fiscal quarter", "eps_estimate_average": "0.8133",
            "eps_estimate_average_7_days_ago": "0.8084", "revenue_estimate_average": "3379421330"}]
    q = [{"fiscalDateEnding": "2026-09-30", "reportedDate": "2026-11-04", "reportedEPS": "0.49",
          "estimatedEPS": "0.31"}]
    call, fetched = datetime(2026, 11, 4, 8, tzinfo=ET5), datetime(2026, 11, 9, 9, tzinfo=ET5)
    rep = next(e for e in normalize_av(est, q, call, fetched, {(2026, 9): 3.42e9}) if e.metric == "eps")
    assert (rep.consensus, rep.actual, rep.surprise_pct, rep.basis) == (0.8084, None, None, "before_call")
    # without a revenue actual the estimates row is not shown to be this quarter, so the at_report row stays
    rep = next(e for e in normalize_av(est, q, call, fetched) if e.metric == "eps")
    assert (rep.consensus, rep.basis) == (0.31, "at_report")

def _est(role, metric, period, consensus):
    return Estimate(period=period, period_end=CALL.date(), role=role, metric=metric, consensus=consensus,
                    as_of=CALL.date(), basis="at_report", source="yfinance")


def _claim(i, metric, value, period, kind="result", novel=False):
    return Claim(claim_id=i, seg_id=i, speaker="CFO", quote=f"q{i}", metric=metric, value=value, period=period,
                 kind=kind, ts=T0 + timedelta(minutes=10 + 30 * i), novel=novel)


ESTS = [_est("reported", "eps", "Q3 FY2026", 1.80), _est("reported", "revenue", "Q3 FY2026", 21.0e9),
        _est("next_quarter", "revenue", "Q4 FY2026", 22.0e9)]


def test_matcher_compares_spoken_eps_and_revenue_with_the_consensus_for_their_period():
    claims = match_consensus([
        _claim(0, "Earnings per share", "$1.87, up 13% YoY", "Q3"),
        _claim(1, "Revenue", "$21.9 billion", "third quarter"),
        _claim(2, "Revenue", "about $22.5 billion", "Q4", kind="guidance"),
        _claim(3, "Revenue growth", "2% YoY", "Q3"),                      # growth, not a level
        _claim(4, "Commercial banking revenue", "$4.1 billion", "Q3"),   # a segment, not the total
        _claim(5, "Revenue", "$21.9 billion", "next year", kind="guidance"),
    ], ESTS)
    gaps = [c.vs_consensus_pct for c in claims]
    assert abs(gaps[0] - 0.07 / 1.80) < 1e-9 and claims[0].consensus_role == "reported"
    assert abs(gaps[1] - 0.9 / 21.0) < 1e-9
    assert abs(gaps[2] - 0.5 / 22.0) < 1e-9 and claims[2].consensus_role == "next_quarter"
    assert gaps[3:] == [None, None, None]


def test_revenue_matches_in_billions_millions_without_a_dollar_sign_and_in_release_millions():
    claims = match_consensus([
        _claim(0, "Q3 revenue", None, "Q3").model_copy(update={"quote": "We reported revenue of $21.4 billion."}),
        _claim(1, "Total net revenue", "21,400 million", "third quarter"),
        _claim(2, "Revenue guidance", "$21.8 to $22.2 billion", "Q4", kind="guidance"),
        _claim(3, "Total revenue", "$ 21,870", None),
        _claim(4, "Revenue", "up 2%", "Q3"),
        _claim(5, "Markets revenue", "$4.1 billion", "Q3"),
    ], ESTS)
    gaps = [c.vs_consensus_pct for c in claims]
    assert abs(gaps[0] - 0.4 / 21.0) < 1e-9 and abs(gaps[1] - 0.4 / 21.0) < 1e-9
    assert abs(gaps[2] - 0.2 / 22.0) < 1e-9 and claims[2].consensus_role == "next_quarter"
    assert abs(gaps[3] - 0.87 / 21.0) < 1e-9
    assert gaps[4:] == [None, None]


def test_empty_estimates_leave_claims_and_moments_unchanged_and_a_beat_scores_like_novel():
    bars = market.flag_abnormal(market.adjusted_returns(
        make_bars(stock_bump={11: 0.01, 41: 0.01}, vol_bump={11: 4, 41: 4})))
    segs = [Segment(seg_id=i, t0_ms=(10 + 30 * i) * 60_000, t1_ms=0, speaker="CFO", text=f"q{i}",
                    ts=T0 + timedelta(minutes=10 + 30 * i)) for i in range(2)]
    claims = [_claim(0, "Earnings per share", "$1.87", "Q3"), _claim(1, "Net interest margin", None, "Q4", novel=True)]
    assert match_consensus(claims, []) == claims
    stale = rank_moments(claims, segs, bars)
    beat = rank_moments(match_consensus(claims, ESTS), segs, bars)
    # the 3.9% EPS beat gets the novel weight (4x the stale one) and overtakes the new NIM remark
    assert [m.claim_id for m in stale] == [1, 0] and [m.claim_id for m in beat] == [0, 1]
    assert abs(beat[0].score / stale[1].score - 4.0) < 1e-9 and beat[0].vs_consensus_pct > 0.01
    assert beat[1].score == stale[0].score and beat[1].vs_consensus_pct is None


def test_an_after_call_consensus_never_scores_a_claim():
    late = [e.model_copy(update={"basis": "after_call"}) for e in ESTS]
    assert all(c.vs_consensus_pct is None for c in match_consensus([_claim(1, "Revenue", "$21.9 billion", "Q3")], late))
