from datetime import timedelta
from pathlib import Path

from conftest import T0

from callmoves import market
from callmoves.eval import Label, cross_null, evaluate, placebo, placebo_inputs
from callmoves.models import Moment


def moment(rank, minute):
    return Moment(rank=rank, claim_id=rank, seg_id=rank, ts=T0 + timedelta(minutes=minute), t0_ms=minute * 60_000,
                  speaker="CFO", quote="x", metric="m", kind="guidance", novel=True, matched_doc=None,
                  matched_text=None,
                  adj_ret_5m=0.01, vol_mult=3.0, nearest_flag_min=1.0, score=1.0)


def test_precision_at_k_with_2_minute_tolerance_and_missed_labels():
    labels = [Label(ts=T0 + timedelta(minutes=12, seconds=10), note="raise"),
              Label(ts=T0 + timedelta(minutes=31), note="margin")]
    # ranks 1 and 2 land on the raise (within 2 min), rank 3 is 3 min off every label, rank 4 lands on the margin label
    ev = evaluate([moment(1, 12), moment(2, 13), moment(3, 40), moment(4, 29)], labels)
    assert ev.hits == [0, 0, None, 1]
    assert ev.precision == {3: 2 / 3, 5: 3 / 4}   # 4 moments, so top 5 is scored over 4
    assert ev.missed == []
    ev = evaluate([moment(1, 12), moment(2, 13), moment(3, 40)], labels)
    assert ev.precision[3] == 2 / 3 and ev.missed == [labels[1]]


ACME = Path(__file__).parent.parent / "fixtures" / "acme_q3_2026"


def test_placebo_planted_moves_beat_every_time_shift():
    real, fake = placebo(*placebo_inputs(ACME, market.BENCHMARK))
    assert fake and real > max(fake)


def test_placebo_skips_a_call_too_short_to_shift():
    segs, bars, claims = placebo_inputs(ACME, market.BENCHMARK)
    short = [s for s in segs if s.ts - segs[0].ts < timedelta(minutes=20)]
    assert placebo(short, bars, [c for c in claims if c.seg_id <= short[-1].seg_id])[1] == []


def test_cross_null_scores_each_call_against_the_other_calls_timings():
    loaded = {"a": placebo_inputs(ACME, market.BENCHMARK), "b": placebo_inputs(ACME, market.BENCHMARK)}
    res = cross_null(loaded)
    assert len(res["a"][1]) == 1 and res["a"][1][0] == res["a"][0]   # the same profile restores the call
