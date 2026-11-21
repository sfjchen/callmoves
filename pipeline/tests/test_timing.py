"""Tests the timing study on synthetic call grids: a planted signal is found, shuffled events are not, and every
null keeps its events inside the call."""
import numpy as np

from callmoves.timing import LOCAL_SHIFTS, Grid, placements, planted, sections, study


def grids(n_calls=30, n=70, qi=30, seed=0, signal=False):
    """Returns calls whose outcome is a random rank per minute, with 20 events each: at the top minutes when
    `signal`, anywhere otherwise."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n_calls):
        x = rng.permutation(n) / (n - 1)
        ev = np.argsort(x)[-20:] if signal else rng.choice(n, 20, replace=False)
        out.append(Grid(f"c{i}", f"2026-10-{1 + i % 15:02d}", n, qi, x, {"claims": np.sort(ev), "few": ev[:3]}))
    return out


def test_events_on_the_top_minutes_beat_every_null():
    for null in ("local", "cross", "rotate"):
        r = study(grids(signal=True), null)["claims"]
        assert r["lo"] > 0 and r["p"] < 0.01 and r["p_max"] < 0.01 and r["p_boot"] < 0.01, null


def test_random_events_show_no_timing_and_a_family_with_too_few_events_is_left_out():
    for null in ("local", "cross", "rotate"):
        res = study(grids(), null)
        assert "few" not in res
        assert res["claims"]["lo"] < 0 < res["claims"]["hi"] and res["claims"]["p_boot"] > 0.05, null


def test_planting_a_fifth_of_the_claims_is_detected():
    rng = np.random.default_rng(1)
    r = study([planted(g, 0.2, rng) for g in grids(n_calls=60)], "local")["claims"]
    assert r["lo"] > 0 and r["p"] < 0.01


def test_placements_keep_events_in_their_section_or_shift_them_all_alike():
    a, b = grids(n_calls=2, seed=3)
    idx = a.ev["claims"]
    assert [list(p - idx) for p in placements(a, idx, "local", [a, b], "claims")] == [[s] * 20 for s in LOCAL_SHIFTS]
    for p in placements(a, idx, "rotate", [a, b], "claims"):
        assert ((p < a.qi) == (idx < a.qi)).all() and not (p == idx).any()
    assert sections(Grid("x", "d", 40, 40, np.zeros(40), {})) == [(0, 40)]


def test_cross_null_keeps_the_calls_own_event_count_at_the_other_calls_positions():
    a, b = grids(n_calls=2, seed=3)
    early = Grid("e", "d", 70, 30, b.x, {"claims": np.arange(40)})   # 30 prepared-remarks minutes, 10 of the Q&A
    (laid,) = placements(a, a.ev["claims"], "cross", [a, early], "claims")
    assert len(laid) == 20 and (laid < 40).all() and 13 <= (laid < a.qi).sum() <= 17   # 3 in 4 before the Q&A
    assert placements(a, a.ev["claims"], "cross", [a, Grid("n", "d", 70, 70, b.x, {"claims": np.arange(9)})],
                      "claims") == []   # a call without Q&A cannot lend its shape to one with


def test_a_call_too_short_for_any_placement_is_skipped_not_fatal():
    short = [Grid(f"s{i}", "d", 18, 18, np.linspace(0, 1, 18), {"claims": np.arange(6, 12)}) for i in range(3)]
    assert study(short, "rotate") == {}   # no section is 20 minutes long, so nothing can rotate
