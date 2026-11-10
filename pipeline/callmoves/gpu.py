"""Transcription and speaker diarization on a Modal GPU (https://modal.com), the fast path for ingest, with
whisper large-v3 instead of the CPU's medium. GPUs need a payment method on the Modal account, else calls queue and
ingest falls back to the CPU after a timeout.

    modal deploy -m callmoves.gpu      # once, and again after changing this file or transcript.py

Ingest calls the deployed function by name when MODAL_TOKEN_ID is set, else it runs both steps on the CPU. The
Hugging Face token comes from the Modal secret `callmoves-hf` (HF_TOKEN), and model weights stay in a Modal volume
so only the first run downloads them.
"""
from __future__ import annotations

import modal

APP = "callmoves-gpu"
app = modal.App(APP)
cache = modal.Volume.from_name("callmoves-model-cache", create_if_missing=True)
image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04", add_python="3.12")
    .pip_install("faster-whisper==1.2.1", "pyannote.audio==4.0.7", "av", "pydantic>=2")
    .env({"HF_HOME": "/cache"})
    .add_local_python_source("callmoves")
)


@app.function(gpu="L4", image=image, volumes={"/cache": cache}, timeout=1800, memory=16384,
              secrets=[modal.Secret.from_name("callmoves-hf")])
def transcribe_and_diarize(audio: bytes, model_size: str = "large-v3") -> tuple[list[dict], list[dict] | None, str]:
    """Transcribes and diarizes 1 call's audio (any format PyAV reads) and returns (segments, voices, error).
    Voices are None with the error text when diarization fails, for example before the gated model's terms are
    accepted, so the call still gets a transcript and call-shape speaker labels."""
    import os
    import tempfile
    import time
    from pathlib import Path

    from callmoves.transcript import diarize, transcribe

    with tempfile.NamedTemporaryFile(suffix=".audio") as f:
        f.write(audio)
        f.flush()
        t = time.time()
        raw = transcribe(Path(f.name), model_size, device="cuda")
        print(f"transcribed {len(raw)} segments in {time.time() - t:.0f}s", flush=True)
        try:
            voices, err = diarize(Path(f.name), os.environ["HF_TOKEN"], device="cuda"), ""
        except Exception as e:  # noqa: BLE001 - the transcript is still worth returning
            voices, err = None, f"{type(e).__name__}: {str(e)[:200]}"
    print(f"diarized in {time.time() - t:.0f}s total, error: {err or None}", flush=True)
    cache.commit()
    return raw, voices, err
