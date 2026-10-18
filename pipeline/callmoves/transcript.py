"""Step 1 and 3: load a timestamped transcript and align it to wall-clock time.

Transcription itself (WhisperX + pyannote on Modal) is out of scope for the prototype.
The input is the JSON WhisperX would hand back: segments with ms offsets and speaker labels.
If `faster_whisper` is installed and an audio file is given, `transcribe()` produces that JSON locally.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from .models import CallMeta, Segment


def load_call(path: Path) -> tuple[CallMeta, list[Segment]]:
    data = json.loads(Path(path).read_text())
    meta = CallMeta(**{k: v for k, v in data.items() if k != "segments"})
    segs = [Segment(seg_id=i, **s) for i, s in enumerate(data["segments"])]
    return meta, segs


def align(meta: CallMeta, segments: list[Segment]) -> list[Segment]:
    """Audio t=0 is the call start. Every segment gets a wall-clock timestamp."""
    out = []
    for s in segments:
        out.append(s.model_copy(update={"ts": meta.call_start + timedelta(milliseconds=s.t0_ms)}))
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


def transcribe(audio_path: Path, model_size: str = "small") -> list[dict]:
    """Local transcription on CPU. Not used by the fixture run."""
    from faster_whisper import WhisperModel  # optional dependency, pip install -e ".[asr]"

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(decode_audio(audio_path), vad_filter=True, beam_size=1)
    return [
        {"t0_ms": int(s.start * 1000), "t1_ms": int(s.end * 1000), "speaker": "", "text": s.text.strip()}
        for s in segments
    ]


# ---- speaker labels without diarization ----
# An earnings call has a fixed shape: operator, prepared remarks by management, then Q&A where the operator
# introduces each analyst. That is enough to tell management from everyone else, which is all the claim
# extractor needs. Real speaker names would come from pyannote on Modal.
import re

_OPERATOR = re.compile(r"\b(questions? (will )?comes? from|next question|final question|first question|please go ahead|"
                       r"your line is (now )?open|listen.only|concludes today|press star|question and answer session|"
                       r"question-and-answer|thank you for standing by|stand by for|being recorded|"
                       r"turn the (call|conference) (back )?over to)\b", re.I)
_QA_START = re.compile(r"\b(questions? (will )?comes? from|first question|take your questions|begin the question)\b", re.I)
_INTRO = re.compile(r"\b(questions? (will )?comes? from|next question|final question)\b", re.I)
_MAX_ANALYST_SEGMENTS = 8


def label_speakers(segments: list[dict]) -> list[dict]:
    """Assigns Operator, Management or Analyst to raw transcription segments.

    Prepared remarks are management. In Q&A the operator introduces each analyst, the analyst speaks until a
    sentence ends in a question mark (or a few segments pass), then management answers until the next operator."""
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
    return out


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
