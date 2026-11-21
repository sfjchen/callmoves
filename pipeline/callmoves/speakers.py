"""Names the voices of a call: each pyannote voice in diarization.json gets the person the call itself introduces,
e.g. "Colette Kress, CFO" or "Stacy Rasgon, Bernstein".

Diarization only says which stretches share a voice. The names come from the call's own handoffs: the operator
introduces each analyst ("next question comes from the line of ..."), and investor relations introduces the
executives ("with me today are ...", "I'll turn it over to ..."). Claude reads each voice's first lines and the
line spoken just before it, maps voices to people, says which side each speaks for, and fixes ASR misspellings it
is sure of ("Jensen Wong" -> "Jensen Huang"). A name is kept only when Claude quotes the line that introduces it
verbatim, the same rule claims follow, and the name is close to words the call actually spoke, so a "correction"
cannot swap in a different person (it named Melius's "Van Breitsis" Atif Malik on NVDA Q2-2027). An analyst the
call-shape rules took for management (a follow-up without a question mark) becomes Analyst. Claude's answer is
cached in speakers.json, keyed by what it saw, so reruns are free.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import llm
from .models import Segment
from .transcript import _INTRO

OPENING_CHARS = 5000   # the opening remarks, where IR introduces the executives
LINE_CHARS = 300


class VoiceName(BaseModel):
    voice: str = Field(description="Voice id exactly as given, e.g. SPEAKER_03")
    name: str = Field(description="Full name, spelling corrected only when certain, e.g. 'Jensen Huang'")
    title: str = Field(description="Short executive title (CEO, CFO, Head of IR) or the analyst's firm (Goldman Sachs)")
    evidence: str = Field(description="Verbatim excerpt of the given lines that introduces or names this person")
    side: Literal["company", "analyst"] = Field(description="company: an officer or IR of the company, analyst: a "
                                                "sell-side or buy-side analyst asking questions")


class VoiceNames(BaseModel):
    voices: list[VoiceName]


SYSTEM = (
    "You label the speakers of an earnings call transcript. Each voice id is 1 person found by speaker "
    "diarization. For each voice you get its role, its first lines and the line spoken just before it. Name a "
    "voice only when the transcript itself says who it is: the operator introducing an analyst, an executive "
    "handing over to a named colleague, a speaker naming themselves. Fix the spelling of a name or firm only "
    "when the transcript is clearly a mishearing of a well-known person or firm. Leave out the operator and any "
    "voice you cannot name with evidence. Quote the evidence verbatim from the lines given, and say whether each "
    "person speaks for the company or is an analyst.")


def _norm(s: str) -> str:
    """Lowercases s and collapses whitespace, for verbatim checks that ignore line breaks."""
    return re.sub(r"\s+", " ", s).strip().lower()


def spoken(name: str, text: str) -> bool:
    """Whether `name` is, or closely matches, a run of as many words in `text`: a spelling fix keeps most letters
    ("Jensen Huang" for "Jensen Wong", ratio 0.78, "Ben Reitzes" for "Van Breitsis", 0.61), a different person
    does not ("Atif Malik" for "Van Breitsis", 0.27)."""
    words, n = re.findall(r"[a-z']+", text.lower()), len(name.split())
    runs = {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}
    return n > 0 and bool(difflib.get_close_matches(name.lower(), runs, n=1, cutoff=0.6))


def voice_of(seg: Segment, voices: list[dict]) -> str | None:
    """Returns the diarization cluster that overlaps the turn longest, None when none does."""
    overlap = Counter()
    for v in voices:
        overlap[v["cluster"]] += max(0, min(seg.t1_ms, v["t1_ms"]) - max(seg.t0_ms, v["t0_ms"]))
    top = (+overlap).most_common(1)
    return top[0][0] if top else None


def name_speakers(call: Path, segments: list[Segment], company: str | None) -> list[Segment]:
    """Returns the segments with `name` set for every turn whose voice Claude could name. Without diarization.json
    or Claude, or when Claude fails, the segments come back unchanged and nothing is cached, so a later run
    retries."""
    vpath = call / "diarization.json"
    if not vpath.exists() or not segments:
        return segments
    voices = json.loads(vpath.read_text())
    vids = [voice_of(s, voices) for s in segments]
    first: dict[str, int] = {}
    intros = Counter()   # operator introductions right before a stretch of the voice
    for i, v in enumerate(vids):
        if v is not None and segments[i].speaker != "Operator":
            first.setdefault(v, i)
            if i and vids[i - 1] != v and segments[i - 1].speaker == "Operator" and _INTRO.search(segments[i - 1].text):
                intros[v] += 1
    # diarization sometimes gives 2 analysts 1 voice, and a voice the operator introduced twice would carry 1 name
    # into the other analyst's question
    first = {v: i for v, i in first.items() if intros[v] < 2}
    blocks = []
    for v, i in first.items():
        role = Counter(segments[j].speaker for j, x in enumerate(vids) if x == v).most_common(1)[0][0]
        lines = [segments[j].text[:LINE_CHARS] for j, x in enumerate(vids) if x == v][:2]
        before = segments[i - 1].text[-LINE_CHARS:] if i else ""
        blocks.append(f"{v} ({role})\n  line before: {before}\n  first lines: " + "\n  ".join(lines))
    opening = " ".join(s.text for s in segments)[:OPENING_CHARS]
    prompt = f"Company: {company or 'unknown'}\n\nOpening of the call:\n{opening}\n\nVoices:\n" + "\n\n".join(blocks)
    path, key = call / "speakers.json", hashlib.sha256((SYSTEM + prompt).encode()).hexdigest()[:16]
    cached = json.loads(path.read_text()) if path.exists() else {}
    if cached.get("key") == key:
        names = [VoiceName(**x) for x in cached["voices"]]
    elif llm.backend():
        try:
            names = llm.parse(SYSTEM, prompt, VoiceNames, max_tokens=4000).voices
        except Exception as e:  # noqa: BLE001 - names are optional, the call still renders by role
            print(f"  speaker names skipped: {type(e).__name__}: {str(e)[:200]}", flush=True)
            return segments
        path.write_text(json.dumps({"key": key, "voices": [n.model_dump() for n in names]}))
    else:
        return segments
    # the cache keeps Claude's raw answer, so a stricter check here applies to cached calls too
    # Claude joins 2 quotes with "..." when the introduction and the handoff are apart ("Joining me are our CEO Rick
    # Wallace ... I'll pass the call now to Rick"), so each piece must be verbatim, not the joined string
    named = {n.voice: n for n in names
             if n.voice in first and spoken(n.name, prompt) and
             all(_norm(q) in _norm(prompt) for q in re.split(r"\.{3,}|\u2026", n.evidence) if q.strip())}

    def relabel(s: Segment, n: VoiceName) -> Segment:
        """Returns s with the person's name and, for an analyst the call-shape rules took for management (a
        follow-up without a question mark), the Analyst role, so their words are not read as company claims."""
        role = "Analyst" if n.side == "analyst" and s.speaker == "Management" else s.speaker
        return s.model_copy(update={"name": f"{n.name}, {n.title}" if n.title else n.name, "speaker": role})

    return [relabel(s, named[v]) if v in named else s for s, v in zip(segments, vids)]
