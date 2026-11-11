"""Call audio for the web player: transcodes to small mono MP3 and uploads it to Supabase Storage.

The bucket `callmoves-audio` is public, so the returned URL plays on the deployed site without a key.
Uploading needs SUPABASE_URL and SUPABASE_SERVICE_KEY in pipeline/.env.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .publish import db_client

BUCKET = "callmoves-audio"


def transcode(src: Path, dst: Path) -> Path:
    """
    Writes `src` as mono 48 kbps MP3 at `dst` with metadata stripped, about 20 MB per hour of speech.
    Returns `dst`.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-b:a", "48k",
                    "-map_metadata", "-1", str(dst)], check=True)
    return dst


def upload(path: Path, object_name: str) -> str:
    """
    Uploads `path` to the audio bucket as `object_name`, overwriting an earlier upload.
    Returns the public URL.
    """
    store = db_client().storage.from_(BUCKET)
    store.upload(object_name, path.read_bytes(),
                 file_options={"content-type": "audio/mpeg", "upsert": "true", "cache-control": "31536000"})
    return store.get_public_url(object_name).rstrip("?")
