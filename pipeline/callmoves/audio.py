"""Call audio for the web player: transcodes to small mono MP3 and uploads it to the public Hugging Face dataset
`jchen04/callmoves-audio`, which serves files with CORS and range requests and has no hard storage cap for public
datasets. The first 45 calls moved there byte for byte from the Supabase bucket `callmoves-audio` on 2026-11-17,
when it neared the free plan's 1 GB. Uploading needs HF_TOKEN (write access to the dataset) in pipeline/.env.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

HF_REPO = "jchen04/callmoves-audio"


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
    Uploads `path` to the audio dataset as `object_name`, replacing an earlier upload in a new commit.
    Returns the public URL.
    """
    from huggingface_hub import HfApi

    HfApi(token=os.environ["HF_TOKEN"]).upload_file(
        path_or_fileobj=str(path), path_in_repo=object_name, repo_id=HF_REPO, repo_type="dataset",
        commit_message=f"Upload {object_name}")
    return f"https://huggingface.co/datasets/{HF_REPO}/resolve/main/{object_name}"
