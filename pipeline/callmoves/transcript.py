"""Loads a timestamped transcript and aligns it to wall-clock time.

The input is call.json: segments with ms offsets and speaker labels. `transcribe()` makes the raw segments with
faster-whisper on CPU (optional dependency), `diarize()` finds the voices with pyannote when a Hugging Face token is
set, and `label_speakers()` and `merge_turns()` turn them into speaker turns.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path

from .models import CallMeta, Segment


def load_call(path: Path) -> tuple[CallMeta, list[Segment]]:
    """Reads call.json into the call metadata and its numbered segments."""
    data = json.loads(Path(path).read_text())
    meta = CallMeta(**{k: v for k, v in data.items() if k != "segments"})
    segs = [Segment(seg_id=i, **s) for i, s in enumerate(data["segments"])]
    return meta, segs


def align(meta: CallMeta, segments: list[Segment]) -> list[Segment]:
    """Audio t=audio_offset_ms is the call start. Every segment gets a wall-clock timestamp."""
    out = []
    for s in segments:
        ts = meta.call_start + timedelta(milliseconds=s.t0_ms - meta.audio_offset_ms)
        out.append(s.model_copy(update={"ts": ts}))
    return out


def decode_audio(audio_path: Path, rate: int = 16000):
    """mp3/m4a -> mono float32 at 16 kHz with PyAV. faster-whisper's own decoder breaks on PyAV 15+."""
    import av
    import numpy as np

    chunks = []
    with av.open(str(audio_path)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def transcribe(audio_path: Path, model_size: str = "medium", device: str = "cpu") -> list[dict]:
    """Transcribes audio with faster-whisper (int8 on CPU, float16 on a GPU) and returns segments with ms offsets
    and empty speakers."""
    from faster_whisper import (
        WhisperModel,  # optional dependency, pip install -e ".[asr]"
    )

    model = WhisperModel(model_size, device=device, compute_type="int8" if device == "cpu" else "float16")
    segments, _ = model.transcribe(decode_audio(audio_path), vad_filter=True, beam_size=1)
    return [
        {"t0_ms": int(s.start * 1000), "t1_ms": int(s.end * 1000), "speaker": "", "text": s.text.strip()}
        for s in segments
    ]


DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"   # gated: accept its terms on Hugging Face once


def diarize(audio_path: Path, token: str, device: str = "cpu") -> list[dict]:
    """Splits the audio into speaker turns with pyannote (optional dependency, pip install -e ".[diarize]").
    Returns [{t0_ms, t1_ms, cluster}] from the exclusive output, which has no overlapping turns, so each
    transcript segment maps to 1 voice."""
    import torch
    from pyannote.audio import Pipeline

    pipe = Pipeline.from_pretrained(DIARIZATION_MODEL, token=token).to(torch.device(device))
    out = pipe({"waveform": torch.from_numpy(decode_audio(audio_path))[None], "sample_rate": 16000})
    return [{"t0_ms": int(t.start * 1000), "t1_ms": int(t.end * 1000), "cluster": c}
            for t, c in out.exclusive_speaker_diarization]


# ---- speaker labels without diarization ----
# An earnings call has a fixed shape: operator, prepared remarks by management, then Q&A where the operator
# introduces each analyst. That is enough to tell management from everyone else, which is all the claim
# extractor needs.
_OPERATOR = re.compile(r"\b(questions? (will )?comes? from|next question|final question|first question|please go ahead|"
                       r"your line is (now )?open|listen.only|concludes today|press star|question and answer session|"
                       r"question-and-answer|thank you for standing by|stand by for|being recorded|"
                       r"turn the (call|conference) (back )?over to)\b", re.IGNORECASE)
_QA_START = re.compile(r"\b(questions? (will )?comes? from|first question|next question|take your questions|"
                       r"begin the question)\b", re.IGNORECASE)
_INTRO = re.compile(r"\b(questions? (will )?comes? from|next question|final question)\b", re.IGNORECASE)
_MAX_ANALYST_SEGMENTS = 8


_PREPARED_MIN_MS = 30_000   # a voice with this much speech before Q&A is management


def label_speakers(segments: list[dict], voices: list[dict] | None = None) -> list[dict]:
    """Assigns Operator, Management or Analyst to raw transcription segments.

    Prepared remarks are management. In Q&A the operator introduces each analyst, the analyst speaks until a
    sentence ends in a question mark (or a few segments pass), then management answers until the next operator.
    With `voices` from `diarize()`, each segment takes the voice it overlaps most and roles go by voice instead: the
    voice with the most operator phrases is the operator, a voice with 30 s or more before Q&A is management, and
    any other voice is an analyst if its first Q&A segment follows the operator, or follows an analyst while the
    call-shape rules still give the analyst the floor. That catches follow-up questions without a question mark,
    which the call-shape rules give to management. Segments no voice overlaps keep the
    call-shape label."""
    out, in_qa, analyst_left = [], False, 0
    for s in segments:
        text = s["text"].strip()
        if _OPERATOR.search(text):
            label = "Operator"
            if _QA_START.search(text):
                in_qa = True
            if in_qa and _INTRO.search(text):
                analyst_left = _MAX_ANALYST_SEGMENTS   # "your line is open" follows and must not reset this
        elif not in_qa:
            label = "Management"
        elif analyst_left > 0:
            label = "Analyst"
            analyst_left = 0 if text.endswith("?") else analyst_left - 1
        else:
            label = "Management"
        out.append({**s, "speaker": label})
    if not voices:
        return out
    for s in out:
        overlap = Counter()
        for v in voices:
            overlap[v["cluster"]] += max(0, min(s["t1_ms"], v["t1_ms"]) - max(s["t0_ms"], v["t0_ms"]))
        top = (+overlap).most_common(1)
        s["voice"] = top[0][0] if top else None
    op_hits = Counter(s["voice"] for s in out if s["speaker"] == "Operator" and s["voice"])
    operator = op_hits.most_common(1)[0][0] if op_hits else None
    qa_ms = next((s["t0_ms"] for s in out if s["speaker"] == "Operator" and _QA_START.search(s["text"])), None)
    prepared = Counter()
    for s in out:
        if s["voice"] and (qa_ms is None or s["t0_ms"] < qa_ms):
            prepared[s["voice"]] += s["t1_ms"] - s["t0_ms"]
    role = {v: "Management" for v, ms in prepared.items() if ms >= _PREPARED_MIN_MS} | {operator: "Operator"}
    for prev, s in zip([None, *out], out):
        if s["voice"] not in role and qa_ms is not None and s["t0_ms"] >= qa_ms:
            # diarization can split 1 analyst into 2 voices ("Hi, Yugal." then the question), so a voice that
            # follows an analyst while the call-shape rules still give the floor to the analyst is 1 too
            after_analyst = prev and role.get(prev["voice"]) == "Analyst" and s["speaker"] == "Analyst"
            role[s["voice"]] = "Analyst" if prev and (prev["voice"] == operator or after_analyst) else "Management"
    return [{k: x for k, x in s.items() if k != "voice"} |
            ({"speaker": role.get(s["voice"], "Management")} if s["voice"] else {}) for s in out]


def qa_start_ms(segments) -> int | None:
    """Audio offset where Q&A begins: the first operator turn that introduces a question. None for a call with no
    detected Q&A. Research finds the Q&A carries more new information than the prepared remarks (Matsumoto,
    Pronk and Roelofsen 2011), so moments say which part they come from."""
    return next((s.t0_ms for s in segments if s.speaker == "Operator" and _QA_START.search(s.text)), None)


# The operator's opening line marks the call start. Hold messages before it ("will begin shortly") do not.
_OPENING = re.compile(r"\b(welcome|being recorded|thank you for standing by|good (morning|afternoon|evening|day)|"
                      r"hello|greetings)\b", re.IGNORECASE)
_HOLD = re.compile(r"\b(shortly|momentarily|will (now )?begin|continue to hold|please (stand by|hold))\b",
                   re.IGNORECASE)
_OPENING_MAX_MS = 600_000


def call_start_ms(segments: list[dict]) -> int:
    """Audio offset of the call start: the first operator opening line in the first 10 minutes, else 0.

    Recordings with lead-in music or a hold message start the call later than t=0. All 20 Quartr recordings in
    data/ open on this line within 0.1 s."""
    return next((s["t0_ms"] for s in segments if s["t0_ms"] < _OPENING_MAX_MS and _OPENING.search(s["text"])
                 and not _HOLD.search(s["text"])), 0)


def merge_turns(segments: list[dict], max_ms: int = 45_000) -> list[dict]:
    """Joins consecutive same-speaker segments into turns of at most max_ms, for a readable transcript."""
    turns: list[dict] = []
    for s in segments:
        last = turns[-1] if turns else None
        part = {"t0_ms": s["t0_ms"], "text": s["text"]}
        if last and last["speaker"] == s["speaker"] and s["t1_ms"] - last["t0_ms"] <= max_ms:
            last["t1_ms"] = s["t1_ms"]
            last["text"] = (last["text"] + " " + s["text"]).strip()
            last["parts"].append(part)
        else:
            turns.append({**s, "parts": [part]})
    return turns
