"""Tests for the price-only panel on synthetic text and bars: the call time read from a release, the call's day,
and 1 event's features."""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from callmoves import panel
from callmoves.market import ET


@pytest.mark.parametrize("text,want", [
    ("The company will host a conference call beginning at 2:00 p.m. PT today.", (17, 0)),
    ("conference call today, October 13, 2026, at 8:30 a.m. (ET)", (8, 30)),
    ("webcast at 2:30 p.m. Pacific time (5:30 p.m. Eastern time)", (17, 30)),
    ("webcast at 8:30 a.m. Central Time", (9, 30)),
    ("conference call at 5:00 p.m. ET. A replay will be available at 8:00 p.m. ET", (17, 0)),
    ("A replay of the conference call will be available at 8:00 p.m. ET", None),
    ("Revenue rose 12% and the dividend is payable at 9:00 a.m. ET", None),
    ("conference call at 13:00 a.m. ET", None),
])
def test_call_time(text, want):
    """Reads the Eastern call time next to a call keyword and skips replays, other times and bad clocks."""
    assert panel.call_time(text) == want


def test_call_start_day():
    """Puts a morning call after an evening release on the next weekday and flags an 8-K filed after its call."""
    fri_eve = datetime(2026, 10, 23, 16, 5, tzinfo=ET)
    start, late = panel.call_start(fri_eve, (8, 30))
    assert (start.date().isoformat(), start.hour, late) == ("2026-10-26", 8, False)
    start, late = panel.call_start(fri_eve, (17, 0))
    assert (start.date().isoformat(), late) == ("2026-10-23", False)
    start, late = panel.call_start(datetime(2026, 10, 23, 11, 40, tzinfo=ET), (10, 0))
    assert (start.date().isoformat(), late) == ("2026-10-23", True)


def bars(jump_at: datetime, days: int = 17) -> pd.DataFrame:
    """Builds flat 1-minute bars over `days` weekdays, 9:30 to 17:59 Eastern, with a 1% jump in the minute after
    `jump_at` and a 2% jump at that day's 15:00."""
    ts = [datetime(2026, 10, 1, 9, 30, tzinfo=ET) + timedelta(days=d, minutes=m)
          for d in range(days * 2) for m in range(510)]
    ts = [t for t in ts if t.weekday() < 5][:days * 510]
    px = np.full(len(ts), 100.0)
    for i, t in enumerate(ts):
        if t > jump_at:
            px[i] *= 1.01
        if t >= jump_at.replace(hour=15, minute=0):
            px[i] *= 1.02
    return pd.DataFrame({"ts": pd.to_datetime(ts, utc=True), "px": px, "vol": 100.0, "etf_px": 50.0})


def test_features_windows():
    """Splits a call day's moves into the call hour and the hours after, and reads quiet control hours as 0."""
    start = datetime(2026, 10, 20, 10, 0, tzinfo=ET)
    f = panel.features(bars(start + timedelta(minutes=5)), start)
    assert f["call"] == pytest.approx(0.01)
    assert f["ann"] == pytest.approx(0.0)
    assert f["after"] == pytest.approx(0.02)
    assert f["next"] == pytest.approx(0.0)
    assert (f["n"], f["thin"], f["ctl_days"], f["ctl_rv"]) == (60, False, panel.CONTROLS, 0.0)
    assert f["ev_0"] == pytest.approx(0.01)
    assert f["ev_10"] == 0.0


def test_features_after_hours_call():
    """Reads an after-hours call against the same day's close and the next 2 closes."""
    start = datetime(2026, 10, 20, 16, 30, tzinfo=ET)
    b = bars(start + timedelta(minutes=5))
    f = panel.features(b, start)
    assert f["ann"] == pytest.approx(0.0)        # 16:00 close to 16:30: flat, the 15:00 jump is before the close
    assert f["call"] == pytest.approx(0.01)
    assert f["after"] == pytest.approx(0.0)
    assert panel.features(b[b["ts"] < pd.Timestamp(start)], start) is None   # nothing trades in the call hour
