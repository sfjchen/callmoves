"""Checks speaker naming offline: names need verbatim evidence and a surname the call spoke, a voice the operator
introduced twice stays unnamed, a named analyst's follow-up loses the Management role, and the cache makes a rerun
free."""
import json

from conftest import segs

from callmoves import llm
from callmoves.speakers import VoiceName, VoiceNames, name_speakers

# (minute, role from the call-shape rules, text, diarization voice)
ROWS = [
    (0, "Operator", "Welcome. I'll turn the call over to Toshia Hari, head of investor relations.", "OP"),
    (1, "Management", "Thanks. With me today are Jensen Wong, CEO, and Colette Kress, CFO.", "IR"),
    (2, "Management", "Revenue was a record.", "CEO"),
    (3, "Operator", "Your next question comes from the line of CJ Muse with Cantor Fitzgerald.", "OP"),
    (4, "Analyst", "Thanks for taking my question.", "A1"),
    (5, "Operator", "Your next question comes from the line of Van Breitsis with Melius Research.", "OP"),
    (6, "Analyst", "Hi, a question on supply.", "A2"),
    (7, "Operator", "Your next question comes from the line of Joseph Moore with Morgan Stanley.", "OP"),
    (8, "Analyst", "Hi, on margins.", "A3"),
    (9, "Management", "And a follow-up on pricing, please.", "A3"),
    (10, "Operator", "Your next question comes from the line of Chris Caso with Wolfe.", "OP"),
    (11, "Analyst", "Hi, on inventory.", "A1"),
]


def call_dir(tmp_path):
    """Writes diarization.json giving each row its voice and returns the folder and the segments."""
    ss = [s.model_copy(update={"t1_ms": s.t0_ms + 50_000}) for s in segs([r[:3] for r in ROWS])]
    (tmp_path / "diarization.json").write_text(json.dumps(
        [{"t0_ms": s.t0_ms, "t1_ms": s.t1_ms, "cluster": r[3]} for s, r in zip(ss, ROWS)]))
    return tmp_path, ss


def test_names_need_evidence_and_a_spoken_surname(tmp_path, monkeypatch):
    folder, ss = call_dir(tmp_path)
    monkeypatch.setenv("CALLMOVES_LLM", "cli")
    seen = []

    def fake(system, prompt, schema, **kw):
        seen.append(prompt)
        return VoiceNames(voices=[
            VoiceName(voice="CEO", name="Jensen Huang", title="CEO", evidence="With me today are Jensen Wong, CEO",
                      side="company"),
            VoiceName(voice="A2", name="Atif Malik", title="Melius Research", evidence="Van Breitsis with Melius",
                      side="analyst"),
            VoiceName(voice="IR", name="Toshia Hari", title="IR", evidence="Toshia Hari, head of IR", side="company"),
            VoiceName(voice="A3", name="Joseph Moore", title="Morgan Stanley", evidence="Joseph Moore with Morgan",
                      side="analyst"),
            VoiceName(voice="A1", name="CJ Muse", title="Cantor Fitzgerald", evidence="CJ Muse with Cantor",
                      side="analyst")])

    monkeypatch.setattr(llm, "parse", fake)
    out = name_speakers(folder, ss, "NVIDIA")
    names = [s.name for s in out]
    assert names[2] == "Jensen Huang, CEO"   # spelling fix kept, "Jensen Huang" is close to the spoken "Jensen Wong"
    assert names[6] is None                  # a different person swapped in by a "correction"
    assert names[1] is None                  # evidence not verbatim
    assert names[4] is None and "A1 (" not in seen[0]   # A1 was introduced twice, so it is never asked about
    assert names[9] == "Joseph Moore, Morgan Stanley" and out[9].speaker == "Analyst"
    assert out[2].speaker == "Management"
    monkeypatch.setattr(llm, "parse", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cache missed")))
    assert name_speakers(folder, ss, "NVIDIA") == out


def test_no_diarization_leaves_roles_alone(tmp_path):
    ss = segs([r[:3] for r in ROWS])
    assert name_speakers(tmp_path, ss, None) == ss
