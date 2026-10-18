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


def transcribe(audio_path: Path, model_size: str = "small") -> list[dict]:
    """Optional local transcription. Not used by the fixture run."""
    from faster_whisper import WhisperModel  # lazy, optional dependency

    model = WhisperModel(model_size)
    segments, _ = model.transcribe(str(audio_path))
    return [
        {"t0_ms": int(s.start * 1000), "t1_ms": int(s.end * 1000), "speaker": "SPEAKER", "text": s.text.strip()}
        for s in segments
    ]
