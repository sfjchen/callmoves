"""Build a call folder from raw inputs, so `callmoves.run` can process it.

    python -m callmoves.ingest --ticker WFC --quarter Q3-2026 --etf XLF \
        --call-start 2026-10-13T10:00:00-04:00 --audio raw/wfc_q3.mpeg \
        --release raw/wfc_q3_release.pdf --prior raw/wfc_q2_release.pdf --out data/wfc_q3_2026

Writes call.json (transcript with speakers), bars.csv (stock + ETF from Massive, needs POLYGON_API_KEY),
release.txt and prior_transcript.txt. Transcription and diarization run on a Modal GPU when MODAL_TOKEN_ID is set,
else faster-whisper and pyannote on the CPU.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from .audio import transcode, upload
from .docs import load_doc
from .market import bars_for_call
from .transcript import call_start_ms, diarize, label_speakers, merge_turns, transcribe

_MODAL_TIMEOUT_S = 1200   # past this no GPU was scheduled, so the CPU takes over


def main(argv=None):
    """Builds a call folder in --out from audio, release and prior documents, and Massive bars."""
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--etf", required=True)
    ap.add_argument("--call-start", required=True, help="ISO with offset, e.g. 2026-10-13T10:00:00-04:00")
    ap.add_argument("--audio", type=Path, help="audio file; omit to relabel whisper_segments.json already in --out")
    ap.add_argument("--release", type=Path, required=True)
    ap.add_argument("--prior", type=Path, help="prior-quarter transcript or release")
    ap.add_argument("--company")
    ap.add_argument("--audio-url", help="public URL the web player should use, set by --upload")
    ap.add_argument("--upload", action="store_true",
                    help="with --audio: transcode to <out>/<TICKER>_<quarter>.mp3, upload it to Supabase Storage, "
                         "use its URL")
    ap.add_argument("--model", default="medium", help="faster-whisper model size")
    ap.add_argument("--audio-offset", type=float,
                    help="seconds into the audio where the call starts: positive for lead-in music, "
                         "negative if the recording started late. Default: the operator's opening line")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    call_start = datetime.fromisoformat(args.call_start)
    if args.upload and args.audio:
        name = f"{args.ticker}_{args.quarter}.mp3"
        args.audio_url = upload(transcode(args.audio, out / name), name)
        print(f"uploaded {name} -> {args.audio_url}", flush=True)

    call_json = out / "call.json"
    raw_json = out / "whisper_segments.json"   # kept so speaker labels can be redone without re-transcribing
    voices_json = out / "diarization.json"     # pyannote voices, reused the same way
    if args.audio:
        mp3 = out / f"{args.ticker}_{args.quarter}.mp3"   # the transcoded upload is 5x smaller to send to the GPU
        raw = None
        if os.environ.get("MODAL_TOKEN_ID"):
            import modal  # optional dependency, pip install -e ".[gpu]"

            from .gpu import APP

            print(f"transcribing and diarizing {args.audio} on a Modal GPU ...", flush=True)
            call = modal.Function.from_name(APP, "transcribe_and_diarize").spawn(
                    (mp3 if mp3.exists() else args.audio).read_bytes())
            try:
                raw, voices, err = call.get(timeout=_MODAL_TIMEOUT_S)
            except Exception as e:  # noqa: BLE001 - no GPU scheduled (no payment method) or a remote failure
                call.cancel()
                print(f"  Modal failed, falling back to the CPU: {type(e).__name__}: {str(e)[:200]}", flush=True)
        if raw is None:
            print(f"transcribing {args.audio} with faster-whisper {args.model} ...", flush=True)
            raw, voices, err = transcribe(args.audio, model_size=args.model), None, "HF_TOKEN is not set"
            if os.environ.get("HF_TOKEN"):
                print("finding voices with pyannote ...", flush=True)
                try:
                    voices = diarize(args.audio, os.environ["HF_TOKEN"])
                except Exception as e:  # noqa: BLE001 - gated model not accepted or no [diarize] extra
                    err = f"{type(e).__name__}: {str(e)[:200]}"
        raw_json.write_text(json.dumps(raw))
        if voices:
            voices_json.write_text(json.dumps(voices))
        else:
            print(f"  no voices, labeling by call shape: {err}")
    elif raw_json.exists():
        raw = json.loads(raw_json.read_text())
    else:
        raw = None
    if raw is not None:
        voices = json.loads(voices_json.read_text()) if voices_json.exists() else None
        segments = merge_turns(label_speakers(raw, voices))
        offset_ms = call_start_ms(raw) if args.audio_offset is None else round(args.audio_offset * 1000)
        # a relabel run without --audio-url keeps the uploaded URL
        if not args.audio_url and call_json.exists():
            args.audio_url = json.loads(call_json.read_text()).get("audio_url")
        call_json.write_text(json.dumps({
            "ticker": args.ticker, "quarter": args.quarter, "company": args.company, "call_start": args.call_start,
            "etf": args.etf, "audio_url": args.audio_url, "audio_offset_ms": offset_ms, "segments": segments,
        }, indent=1))
        print(f"  {len(raw)} whisper segments -> {len(segments)} turns, "
              f"{sum(s['speaker'] == 'Management' for s in segments)} management")
        print(f"  first speech at {raw[0]['t0_ms'] / 1000:.1f} s into the audio, call start taken at "
              f"{offset_ms / 1000:.1f} s ({'--audio-offset' if args.audio_offset is not None else 'opening line'})")
    elif not call_json.exists():
        ap.error("--audio is required when there is no call.json or whisper_segments.json yet")

    print("fetching 1-minute bars ...", flush=True)
    call = json.loads(call_json.read_text())
    call_end = call_start + timedelta(
            milliseconds=max(s["t1_ms"] for s in call["segments"]) - call.get("audio_offset_ms", 0))
    bars = bars_for_call(args.ticker, args.etf, call_start, call_end)
    bars.to_csv(out / "bars.csv", index=False)
    print(f"  {len(bars)} bars {bars['ts'].min()} -> {bars['ts'].max()}")

    (out / "release.txt").write_text(load_doc(args.release))
    (out / "prior_transcript.txt").write_text(load_doc(args.prior) if args.prior else "")
    print(f"wrote {out}/")


if __name__ == "__main__":
    main()
