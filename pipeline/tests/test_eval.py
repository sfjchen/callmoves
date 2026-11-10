from datetime import timedelta
from pathlib import Path

from conftest import T0

from callmoves.eval import Label, evaluate, placebo
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


def test_placebo_planted_moves_beat_every_time_shift():
    real, fake = placebo(Path(__file__).parent.parent / "fixtures" / "acme_q3_2026")
    assert real > max(fake)
