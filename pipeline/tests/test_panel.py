"""Tests for the price-only panel on synthetic text and bars: the call time read from a release, the call's day,
and 1 event's features."""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from callmoves import panel
from callmoves.market import ET


@pytest.mark.parametrize("text,want", [
    ("The company will host a conference call beginning at 2:00 p.m. PT today.", (17, 0, "today")),
    ("conference call on October 13, 2026, at 8:30 a.m. (ET)", (8, 30, (10, 13))),
    ("webcast tomorrow at 2:30 p.m. Pacific time (5:30 p.m. Eastern time)", (17, 30, "tomorrow")),
    ("webcast at 8:30 a.m. Central Time", (9, 30, None)),
    ("conference call at 5:00 p.m. ET. A replay will be available at 8:00 p.m. ET", (17, 0, None)),
    ("A replay of the conference call will be available at 8:00 p.m. ET", None),
    ("Revenue rose 12% and the dividend is payable at 9:00 a.m. ET", None),
    ("conference call at 13:00 a.m. ET", None),
    ("webcast at 11:40 a.m. PT on December 9. A conference call begins at 2 p.m. Pacific Time on October 13, 2026.",
     (17, 0, (10, 13))),
])
def test_call_time(text, want):
    """Reads the Eastern call time and day hint next to a call keyword, skipping replays and other times."""
    assert panel.call_time(text, date(2026, 10, 13)) == want


def test_call_start_day():
    """Takes the call's day from the text when it gives one, else from the rule, and flags an 8-K filed late."""
    fri_eve = datetime(2026, 10, 23, 16, 5, tzinfo=ET)

    def day(accepted, clock):
        """Returns the call's date, lateness and day source."""
        start, late, src = panel.call_start(accepted, clock)
        return start.date().isoformat(), late, src

    assert day(fri_eve, (8, 30, None)) == ("2026-10-26", False, "rule")       # evening release, morning call
    assert day(fri_eve, (17, 0, None)) == ("2026-10-23", False, "rule")
    assert day(fri_eve, (8, 30, "today")) == ("2026-10-23", True, "today")    # 8-K filed the evening after its call
    assert day(fri_eve, (8, 30, (10, 23))) == ("2026-10-23", True, "date")
    assert day(fri_eve, (8, 30, (10, 27))) == ("2026-10-27", False, "date")
    assert day(fri_eve, (8, 30, "tomorrow")) == ("2026-10-26", False, "tomorrow")
    assert day(datetime(2026, 10, 23, 11, 40, tzinfo=ET), (10, 0, None)) == ("2026-10-23", True, "rule")
    assert day(datetime(2026, 12, 30, 16, 5, tzinfo=ET), (8, 30, (1, 2)))[0] == "2027-01-02"
    filed_before = panel.call_start(fri_eve, (8, 30, "today"), frozenset({date(2026, 10, 26)}))
    assert (filed_before[0].date().isoformat(), filed_before[1:]) == ("2026-10-26", (False, "calendar"))
    assert panel.dated((9, 30), date(2026, 10, 23)) is None                   # the quarter's end, not the call


def bars(jump_at: datetime, days: int = 17) -> pd.DataFrame:
    """Builds flat 1-minute bars over `days` weekdays, 9:30 to 17:59 Eastern, stamped at the minute's start. The
    minute starting at `jump_at` closes 1% higher, and that day's 15:00 minute 2% higher again."""
    ts = [datetime(2026, 10, 1, 9, 30, tzinfo=ET) + timedelta(days=d, minutes=m)
          for d in range(days * 2) for m in range(510)]
    ts = [t for t in ts if t.weekday() < 5][:days * 510]
    px = np.full(len(ts), 100.0)
    for i, t in enumerate(ts):
        if t >= jump_at:
            px[i] *= 1.01
        if t >= jump_at.replace(hour=15, minute=0):
            px[i] *= 1.02
    return pd.DataFrame({"ts": pd.to_datetime(ts, utc=True), "px": px, "vol": 100.0, "etf_px": 50.0})


def test_features_windows():
    """Puts the call's first minute in the call hour, not before it, and reads quiet control hours as 0."""
    start = datetime(2026, 10, 20, 10, 0, tzinfo=ET)
    f = panel.features(bars(start), start)
    assert f["call"] == pytest.approx(0.01)
    assert f["pre"] == pytest.approx(0.0)
    assert f["fwd"] == pytest.approx(0.02)       # 11:05 to the next day's close holds the 15:00 jump
    assert (f["n"], f["thin"], f["ctl_days"], f["ctl_abs"], f["ctl_rank"]) == (60, False, panel.CONTROLS, 0.0, 1.0)
    assert f["ev_0"] == pytest.approx(0.01)
    assert (f["ev_-10"], f["ev_10"], f["ct_0"]) == (0.0, 0.0, 0.0)
    early = panel.features(bars(start - timedelta(minutes=1)), start)
    assert (early["pre"], early["call"]) == (pytest.approx(0.01), pytest.approx(0.0))


def test_features_after_hours_and_stale():
    """Reads an after-hours call against the same day's close, and gives no return to a stale start."""
    start = datetime(2026, 10, 20, 16, 30, tzinfo=ET)
    b = bars(start + timedelta(minutes=5))
    f = panel.features(b, start)
    assert f["pre"] == pytest.approx(0.0)        # 16:00 close to 16:30: flat, the 15:00 jump is before the close
    assert f["call"] == pytest.approx(0.01)
    assert f["fwd"] == pytest.approx(0.0)        # next day's open to the close after it
    gap = (b["ts"] >= pd.Timestamp(start - timedelta(minutes=15))) & (b["ts"] < pd.Timestamp(start))
    stale = panel.features(b[~gap], start)
    assert np.isnan(stale["call"]) and stale["thin"]
    assert panel.features(b[b["ts"] < pd.Timestamp(start)], start) is None   # nothing trades in the call hour


@pytest.mark.parametrize("boost,low,high", [(1.0, -0.1, 0.1), (3.0, 0.3, 1.0)])
def test_in_call_lift(boost, low, high, capsys):
    """Finds no lift when in-call bins move like waiting bins of the same release age, and a lift when they move
    `boost` times as much."""
    rng = np.random.default_rng(0)
    rows = []
    for i in range(160):
        gap = 30 if i % 2 else 60                       # release to call: half the events start 30 minutes sooner
        start = datetime(2026, 1, 5, 17, 0, tzinfo=ET) + timedelta(days=i // 4)
        row = {"ticker": f"T{i % 20}", "call_start": start.isoformat(), "late_8k": False, "day_src": "today",
               "accepted": (start - timedelta(minutes=gap)).isoformat(), "surprise_pct": rng.normal(),
               "in_library": False, "pre": rng.normal(0, 0.03), "call": rng.normal(0, 0.01),
               "fwd": rng.normal(0, 0.02), "n": 60, "vol": 1e5, "rv": 0.02, "flags": 2, "thin": False,
               "ctl_days": 10, "ctl_abs": 0.008, "ctl_rank": 0.5, "ctl_share": 0.3, "ctl_vol": 1e5, "ctl_n": 600,
               "ctl_flags": 20}
        for k in panel.BINS:
            decay = 3.0 * np.exp(-max(gap + k, 0) / 40) + 1    # activity fades with the release's age
            row[f"ev_{k}"] = 0.001 * decay * rng.exponential() * (boost if k >= 0 else 1.0)
            row[f"ct_{k}"] = 0.001
        rows.append(row)
    panel.stats(pd.DataFrame(rows), draws=20)
    line = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("in-call lift"))
    assert low < float(line.split()[2]) < high
