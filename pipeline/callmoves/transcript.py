"""Step 1 and 3: load a timestamped transcript and align it to wall-clock time.

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


def transcribe(audio_path: Path, model_size: str = "medium") -> list[dict]:
    """Transcribes audio on CPU with faster-whisper and returns segments with ms offsets and empty speakers."""
    from faster_whisper import (
        WhisperModel,  # optional dependency, pip install -e ".[asr]"
    )

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(decode_audio(audio_path), vad_filter=True, beam_size=1)
    return [
        {"t0_ms": int(s.start * 1000), "t1_ms": int(s.end * 1000), "speaker": "", "text": s.text.strip()}
        for s in segments
    ]


DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"   # gated: accept its terms on Hugging Face once


def diarize(audio_path: Path, token: str) -> list[dict]:
    """Splits the audio into speaker turns with pyannote on CPU (optional dependency, pip install -e ".[diarize]").
    Returns [{t0_ms, t1_ms, cluster}] from the exclusive output, which has no overlapping turns, so each
    transcript segment maps to 1 voice."""
    import torch
    from pyannote.audio import Pipeline

    pipe = Pipeline.from_pretrained(DIARIZATION_MODEL, token=token)
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
_QA_START = re.compile(
        r"\b(questions? (will )?comes? from|first question|take your questions|begin the question)\b", re.IGNORECASE)
_INTRO = re.compile(r"\b(questions? (will )?comes? from|next question|final question)\b", re.IGNORECASE)
_MAX_ANALYST_SEGMENTS = 8


_PREPARED_MIN_MS = 30_000   # a voice with this much speech before Q&A is management


def label_speakers(segments: list[dict], voices: list[dict] | None = None) -> list[dict]:
    """Assigns Operator, Management or Analyst to raw transcription segments.

    Prepared remarks are management. In Q&A the operator introduces each analyst, the analyst speaks until a
    sentence ends in a question mark (or a few segments pass), then management answers until the next operator.
    With `voices` from `diarize()`, each segment takes the voice it overlaps most and roles go by voice instead: the
    voice with the most operator phrases is the operator, a voice with 30 s or more before Q&A is management, and
    any other voice is an analyst if its first Q&A segment follows the operator. That catches follow-up questions
    without a question mark, which the call-shape rules give to management. Segments no voice overlaps keep the
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
            role[s["voice"]] = "Analyst" if prev and prev["voice"] == operator else "Management"
    return [{k: x for k, x in s.items() if k != "voice"} |
            ({"speaker": role.get(s["voice"], "Management")} if s["voice"] else {}) for s in out]


def qa_start_ms(segments) -> int | None:
    """Audio offset where Q&A begins: the first operator turn that introduces a question. None for a call with no
    detected Q&A. Research finds the Q&A carries more new information than the prepared remarks (Matsumoto,
    Pronk and Roelofsen 2011), so moments say which part they come from."""
    return next((s.t0_ms for s in segments if s.speaker == "Operator" and _QA_START.search(s.text)), None)


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
