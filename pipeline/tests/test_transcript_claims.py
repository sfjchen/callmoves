from datetime import timedelta

from callmoves.claims import extract_claims_rules
from callmoves.models import CallMeta, Segment
from callmoves.novelty import judge_novelty_rules
from callmoves.transcript import align
from conftest import T0


def seg(i, t0, speaker, text):
    return Segment(seg_id=i, t0_ms=t0, t1_ms=t0 + 1000, speaker=speaker, text=text)


def test_align_adds_call_start_to_offsets():
    meta = CallMeta(ticker="X", quarter="Q1", call_start=T0)
    out = align(meta, [seg(0, 0, "CEO", "hi"), seg(1, 90_000, "CFO", "bye")])
    assert out[0].ts == T0 and out[1].ts == T0 + timedelta(seconds=90)


def test_rules_extract_guidance_from_management_only():
    segs = align(CallMeta(ticker="X", quarter="Q1", call_start=T0), [
        seg(0, 0, "Marcus Lee, CFO", "We are raising our full year revenue guidance to $1.64 billion. Thank you."),
        seg(1, 5000, "Priya Nair, Analyst", "Is the $1.64 billion number conservative?"),
        seg(2, 9000, "Operator", "Next question."),
    ])
    claims = extract_claims_rules(segs)
    assert [c.seg_id for c in claims] == [0]
    assert claims[0].kind == "guidance" and claims[0].value.startswith("$1.64") and claims[0].ts == T0


def test_novelty_by_number_and_by_overlap():
    release = "Acme reiterates revenue guidance of $1.61 billion to $1.62 billion. Deal cycles were steady."
    segs = align(CallMeta(ticker="X", quarter="Q1", call_start=T0), [
        seg(0, 0, "CFO", "We had guided to $1.61 billion to $1.62 billion."),
        seg(1, 1000, "CFO", "We are raising guidance to $1.64 billion to $1.65 billion."),
    ])
    old, new = [judge_novelty_rules(c, release, "") for c in extract_claims_rules(segs)]
    assert old.novel is False and old.matched_doc == "release" and "1.61" in old.matched_text
    assert new.novel is True and new.matched_text is None


def test_same_wording_with_new_numbers_is_novel_and_transitions_are_not_claims():
    prior = "For the full fiscal year 2026 we are raising our revenue guidance to $1.61 billion to $1.62 billion."
    segs = align(CallMeta(ticker="X", quarter="Q1", call_start=T0), [
        seg(0, 0, "CFO", "Now to guidance. We are raising our full year fiscal 2026 revenue guidance to $1.64 billion to $1.65 billion."),
    ])
    claims = extract_claims_rules(segs)
    assert [c.quote[:6] for c in claims] == ["We are"]
    assert judge_novelty_rules(claims[0], "", prior).novel is True


def test_speaker_heuristic_follows_call_shape():
    from callmoves.transcript import label_speakers, merge_turns
    raw = [
        {"t0_ms": 0, "t1_ms": 5000, "speaker": "", "text": "Good morning. I would now like to turn the call over to John."},
        {"t0_ms": 5000, "t1_ms": 9000, "speaker": "", "text": "Thanks. Revenue was $20 billion this quarter."},
        {"t0_ms": 9000, "t1_ms": 12000, "speaker": "", "text": "Net interest income was $12 billion."},
        {"t0_ms": 12000, "t1_ms": 15000, "speaker": "", "text": "Our first question comes from Jane at Firm. Please go ahead."},
        {"t0_ms": 15000, "t1_ms": 18000, "speaker": "", "text": "Thanks. Can you talk about deposit costs?"},
        {"t0_ms": 18000, "t1_ms": 22000, "speaker": "", "text": "Sure. We expect deposit costs to fall 10 basis points."},
        {"t0_ms": 22000, "t1_ms": 25000, "speaker": "", "text": "Our next question comes from Bob at Other."},
        {"t0_ms": 25000, "t1_ms": 28000, "speaker": "", "text": "Hi, what about expenses?"},
    ]
    labeled = label_speakers(raw)
    assert [s["speaker"] for s in labeled] == ["Operator", "Management", "Management", "Operator", "Analyst", "Management", "Operator", "Analyst"]
    turns = merge_turns(labeled)
    assert len(turns) == 7 and turns[1]["text"].startswith("Thanks. Revenue") and turns[1]["t1_ms"] == 12000
    claims = extract_claims_rules(align(CallMeta(ticker="X", quarter="Q1", call_start=T0), [Segment(seg_id=i, **t) for i, t in enumerate(turns)]))
    assert {c.quote[:10] for c in claims} == {"Revenue wa", "Net intere", "We expect "}


def test_release_table_millions_match_spoken_billions():
    from callmoves.novelty import _num_values, _nums_match
    assert _nums_match(_num_values("Revenue was $21.9 billion, up 2%."), _num_values("Total revenue $ 21,870 21,436"))
    assert not _nums_match(_num_values("Net interest income of about $13 billion"), _num_values("Total revenue $ 21,870"))
    assert _nums_match(_num_values("ROTCE was 16.3%"), _num_values("return on tangible common equity 16.3%"))


def test_percentages_match_exactly_not_approximately():
    from callmoves.novelty import _num_values, _nums_match
    assert not _nums_match(_num_values("roughly 77.5%"), _num_values("in the 78% to 80% range"))
