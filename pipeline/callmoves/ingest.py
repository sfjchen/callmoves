"""Build a call folder from raw inputs, so `callmoves.run` can process it.

    python -m callmoves.ingest --ticker WFC --quarter Q3-2026 --etf XLF \
        --call-start 2026-10-13T10:00:00-04:00 --audio raw/wfc_q3.mpeg \
        --release raw/wfc_q3_release.pdf --prior raw/wfc_q2_release.pdf --out data/wfc_q3_2026

Writes call.json (transcript with speakers), bars.csv (stock + ETF, from Polygon or yfinance),
release.txt and prior_transcript.txt. Transcription is faster-whisper on CPU, about 20 to 40 min per hour of audio.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .docs import load_doc
from .market import bars_for_call
from .transcript import label_speakers, merge_turns, transcribe


def main(argv=None):
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
    ap.add_argument("--audio-url", help="URL the web player should use, e.g. /audio/WFC_Q3-2026.mp3")
    ap.add_argument("--model", default="medium", help="faster-whisper model size")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    call_start = datetime.fromisoformat(args.call_start)

    call_json = out / "call.json"
    raw_json = out / "whisper_segments.json"   # kept so speaker labels can be redone without re-transcribing
    if args.audio:
        print(f"transcribing {args.audio} with faster-whisper {args.model} ...", flush=True)
        raw = transcribe(args.audio, model_size=args.model)
        raw_json.write_text(json.dumps(raw))
    elif raw_json.exists():
        raw = json.loads(raw_json.read_text())
    else:
        raw = None
    if raw is not None:
        segments = merge_turns(label_speakers(raw))
        call_json.write_text(json.dumps({
            "ticker": args.ticker, "quarter": args.quarter, "company": args.company, "call_start": args.call_start,
            "etf": args.etf, "audio_url": args.audio_url, "segments": segments,
        }, indent=1))
        print(f"  {len(raw)} whisper segments -> {len(segments)} turns, "
              f"{sum(s['speaker'] == 'Management' for s in segments)} management")
    elif not call_json.exists():
        ap.error("--audio is required when there is no call.json or whisper_segments.json yet")

    print("fetching 1-minute bars ...", flush=True)
    bars = bars_for_call(args.ticker, args.etf, call_start)
    bars.to_csv(out / "bars.csv", index=False)
    print(f"  {len(bars)} bars {bars['ts'].min()} -> {bars['ts'].max()}")

    (out / "release.txt").write_text(load_doc(args.release))
    (out / "prior_transcript.txt").write_text(load_doc(args.prior) if args.prior else "")
    print(f"wrote {out}/")


if __name__ == "__main__":
    main()
