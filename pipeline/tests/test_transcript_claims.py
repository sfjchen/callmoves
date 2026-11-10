from datetime import timedelta

from conftest import T0, meta

from callmoves.claims import extract_claims_rules
from callmoves.models import Claim, Part, Segment
from callmoves.novelty import _num_values, _nums_match, judge_novelty_rules
from callmoves.transcript import align, call_start_ms, label_speakers, merge_turns


def seg(i, t0, speaker, text):
    return Segment(seg_id=i, t0_ms=t0, t1_ms=t0 + 1000, speaker=speaker, text=text)


def test_align_adds_call_start_to_offsets():
    m = meta()
    out = align(m, [seg(0, 0, "CEO", "hi"), seg(1, 90_000, "CFO", "bye")])
    assert out[0].ts == T0 and out[1].ts == T0 + timedelta(seconds=90)


def test_align_subtracts_audio_offset_and_defaults_to_zero():
    assert meta().audio_offset_ms == 0
    m = meta(audio_offset_ms=30_000)   # 30 s of lead-in music
    out = align(m, [seg(0, 30_000, "Operator", "welcome"), seg(1, 90_000, "CFO", "revenue")])
    assert out[0].ts == T0 and out[1].ts == T0 + timedelta(seconds=60)


def test_rules_extract_guidance_from_management_only():
    segs = align(meta(), [
        seg(0, 0, "Marcus Lee, CFO", "We are raising our full year revenue guidance to $1.64 billion. Thank you."),
        seg(1, 5000, "Priya Nair, Analyst", "Is the $1.64 billion number conservative?"),
        seg(2, 9000, "Operator", "Next question."),
    ])
    claims = extract_claims_rules(segs)
    assert [c.seg_id for c in claims] == [0]
    assert claims[0].kind == "guidance" and claims[0].value.startswith("$1.64") and claims[0].ts == T0


def test_novelty_by_number_and_by_overlap():
    release = "Acme reiterates revenue guidance of $1.61 billion to $1.62 billion. Deal cycles were steady."
    segs = align(meta(), [
        seg(0, 0, "CFO", "We had guided to $1.61 billion to $1.62 billion."),
        seg(1, 1000, "CFO", "We are raising guidance to $1.64 billion to $1.65 billion."),
    ])
    old, new = [judge_novelty_rules(c, release, "") for c in extract_claims_rules(segs)]
    assert old.novel is False and old.matched_doc == "release" and "1.61" in old.matched_text
    assert new.novel is True and new.matched_text is None


def test_same_wording_with_new_numbers_is_novel_and_transitions_are_not_claims():
    prior = "For the full fiscal year 2026 we are raising our revenue guidance to $1.61 billion to $1.62 billion."
    segs = align(meta(), [
        seg(0, 0, "CFO", "Now to guidance. We are raising our full year fiscal 2026 revenue guidance to $1.64 billion "
            "to $1.65 billion."),
    ])
    claims = extract_claims_rules(segs)
    assert [c.quote[:6] for c in claims] == ["We are"]
    assert judge_novelty_rules(claims[0], "", prior).novel is True


def test_speaker_heuristic_follows_call_shape():
    raw = [
        {"t0_ms": 0, "t1_ms": 5000, "speaker": "",
         "text": "Good morning. I would now like to turn the call over to John."},
        {"t0_ms": 5000, "t1_ms": 9000, "speaker": "", "text": "Thanks. Revenue was $20 billion this quarter."},
        {"t0_ms": 9000, "t1_ms": 12000, "speaker": "", "text": "Net interest income was $12 billion."},
        {"t0_ms": 12000, "t1_ms": 15000, "speaker": "", "text": "Our first question will come from Jane of Firm."},
        {"t0_ms": 15000, "t1_ms": 16000, "speaker": "", "text": "Your line is open."},
        {"t0_ms": 16000, "t1_ms": 17000, "speaker": "", "text": "Hey, thanks for the question."},
        {"t0_ms": 17000, "t1_ms": 18000, "speaker": "", "text": "Can you talk about deposit costs?"},
        {"t0_ms": 18000, "t1_ms": 22000, "speaker": "",
         "text": "Sure. We expect deposit costs to fall 10 basis points."},
        {"t0_ms": 22000, "t1_ms": 25000, "speaker": "",
         "text": "The next question will come from Bob of Other. Please go ahead."},
        {"t0_ms": 25000, "t1_ms": 28000, "speaker": "", "text": "Hi, what about expenses?"},
    ]
    labeled = label_speakers(raw)
    assert [s["speaker"] for s in labeled] == [
        "Operator", "Management", "Management", "Operator", "Operator", "Analyst", "Analyst", "Management",
        "Operator", "Analyst"]
    turns = merge_turns(labeled)
    assert len(turns) == 7 and turns[1]["text"].startswith("Thanks. Revenue") and turns[1]["t1_ms"] == 12000
    claims = extract_claims_rules(align(meta(), [Segment(seg_id=i, **t) for i, t in enumerate(turns)]))
    assert {c.quote[:10] for c in claims} == {"Revenue wa", "Net intere", "We expect "}


def test_voices_fix_follow_ups_and_handoffs_the_call_shape_mislabels():
    raw = [
        {"t0_ms": 0, "t1_ms": 5000, "speaker": "", "text": "Welcome. I would now like to turn the call over to John."},
        {"t0_ms": 5000, "t1_ms": 40000, "speaker": "", "text": "Thanks. Revenue was $20 billion this quarter."},
        {"t0_ms": 40000, "t1_ms": 42000, "speaker": "", "text": "Let me turn the call over to Mary."},
        {"t0_ms": 42000, "t1_ms": 80000, "speaker": "", "text": "Net interest income was $12 billion."},
        {"t0_ms": 80000, "t1_ms": 84000, "speaker": "", "text": "Our first question will come from Jane of Firm."},
        {"t0_ms": 84000, "t1_ms": 88000, "speaker": "", "text": "Can you talk about deposit costs?"},
        {"t0_ms": 88000, "t1_ms": 95000, "speaker": "", "text": "Sure. We expect deposit costs to fall."},
        {"t0_ms": 95000, "t1_ms": 99000, "speaker": "", "text": "And then a follow-up on expenses."},
        {"t0_ms": 99000, "t1_ms": 104000, "speaker": "", "text": "Expenses will be flat."},
    ]
    voices = [{"t0_ms": a, "t1_ms": b, "cluster": c} for a, b, c in [
        (0, 5000, "op"), (5000, 42000, "ceo"), (42000, 80000, "cfo"), (80000, 84000, "op"), (84000, 88000, "jane"),
        (88000, 95000, "cfo"), (95000, 99000, "jane"), (99000, 104000, "ceo")]]
    assert [s["speaker"] for s in label_speakers(raw)][2] == "Operator"       # the call-shape rules miss both
    assert [s["speaker"] for s in label_speakers(raw)][7] == "Management"
    assert [s["speaker"] for s in label_speakers(raw, voices)] == [
        "Operator", "Management", "Management", "Management", "Operator", "Analyst", "Management", "Analyst",
        "Management"]


def test_an_analyst_split_into_2_voices_stays_an_analyst():
    raw = [
        {"t0_ms": 0, "t1_ms": 40000, "speaker": "", "text": "Revenue was $20 billion this quarter."},
        {"t0_ms": 40000, "t1_ms": 44000, "speaker": "", "text": "We will take our next question from Jane of Firm."},
        {"t0_ms": 44000, "t1_ms": 45000, "speaker": "", "text": "Hi, John."},
        {"t0_ms": 45000, "t1_ms": 52000, "speaker": "", "text": "Thanks for taking the questions. On deposits,"},
        {"t0_ms": 52000, "t1_ms": 56000, "speaker": "", "text": "where do costs go next year?"},
        {"t0_ms": 56000, "t1_ms": 70000, "speaker": "", "text": "Thanks. We think deposit costs fall a little."},
    ]
    voices = [{"t0_ms": a, "t1_ms": b, "cluster": c} for a, b, c in [
        (0, 40000, "ceo"), (40000, 44000, "op"), (44000, 45000, "jane_a"), (45000, 56000, "jane_b"),
        (56000, 70000, "cfo")]]
    # "next question" starts Q&A here (no "first question" on this call), and the 2nd analyst voice keeps the floor
    assert [s["speaker"] for s in label_speakers(raw, voices)] == [
        "Management", "Operator", "Analyst", "Analyst", "Analyst", "Management"]


def test_release_table_millions_match_spoken_billions():
    assert _nums_match(_num_values("Revenue was $21.9 billion, up 2%."), _num_values("Total revenue $ 21,870 21,436"))
    assert not _nums_match(
        _num_values("Net interest income of about $13 billion"), _num_values("Total revenue $ 21,870"))
    assert _nums_match(_num_values("ROTCE was 16.3%"), _num_values("return on tangible common equity 16.3%"))


def test_percentages_match_exactly_not_approximately():
    assert not _nums_match(_num_values("roughly 77.5%"), _num_values("in the 78% to 80% range"))


def test_claim_is_timed_to_its_sentence_not_the_turn():
    turn = Segment(seg_id=0, t0_ms=60_000, t1_ms=100_000, speaker="Management",
                   text="Thanks for the question. Net interest income was $12 billion in the quarter.",
                   parts=[Part(t0_ms=60_000, text="Thanks for the question."),
                          Part(t0_ms=85_000, text="Net interest income was $12 billion in the quarter.")])
    [c] = extract_claims_rules(align(meta(), [turn]))
    assert c.ts == T0 + timedelta(seconds=85)


def test_llm_not_novel_verdict_needs_its_evidence_in_the_named_document(monkeypatch):
    from callmoves import novelty

    release = "Total revenue was $20.1 billion.\nNet interest income rose 3%."
    claims = [Claim(claim_id=i, seg_id=0, speaker="CFO", quote=q, metric="m", kind="guidance") for i, q in
              enumerate(["We expect expenses of about $55 billion next year.", "Revenue was $20.1 billion."])]
    verdicts = novelty._VerdictsLLM(verdicts=[
        novelty._VerdictLLM(claim_id=0, novel=False, matched_doc="release", matched_text="Expenses of $55 billion."),
        novelty._VerdictLLM(claim_id=1, novel=False, matched_doc="release", matched_text="Total revenue was  $20.1 billion."),
    ])
    monkeypatch.setattr(novelty.llm, "parse", lambda *a, **k: verdicts)
    out = novelty.judge_novelty_llm(claims, release, "")
    assert out[0].novel is True    # invented evidence: the rules decide, and $55 billion is nowhere in the release
    assert out[1].novel is False and out[1].matched_doc == "release"   # whitespace differences still match


def test_call_start_skips_lead_in_and_hold_message():
    """The call starts at the operator's opening line, not at hold music or a hold message before it."""
    seg = lambda t0, text: {"t0_ms": t0, "t1_ms": t0 + 4000, "text": text}
    lead_in = [seg(0, "Thank you for standing by. Your conference will begin shortly."),
               seg(95_000, "Good morning and welcome to the Acme third quarter call."),
               seg(101_000, "This call is being recorded.")]
    assert call_start_ms(lead_in) == 95_000
    assert call_start_ms([seg(0, "Welcome, everyone."), seg(5000, "Thank you.")]) == 0
    assert call_start_ms([seg(1000, "Revenue rose 5%."), seg(700_000, "Welcome back.")]) == 0
