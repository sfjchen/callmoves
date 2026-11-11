"""Worker for calls submitted on the website: claims the oldest queued row of callmoves_submissions, downloads its
audio and documents, runs ingest (transcription, bars, audio upload) and the pipeline, publishes the result to
callmoves_calls and marks the row done or failed. Runs on GitHub Actions (.github/workflows/worker.yml), 1 call
per run, or locally:

    python -m callmoves.worker            # needs SUPABASE_URL and SUPABASE_SERVICE_KEY, the other keys optional
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from . import ingest
from .market import ET
from .publish import db_client, publish
from .run import run

TABLE = "callmoves_submissions"
MAX_AUDIO_MB = 400            # a 2-hour call at 128 kbps is about 115 MB
MAX_DOC_MB = 30
DOC_SUFFIX = {"application/pdf": ".pdf", "text/html": ".html", "text/plain": ".txt"}
MAX_REDIRECTS = 5
UA = "CallMoves worker (github.com/sfjchen/callmoves)"


def public_https(url: httpx.URL) -> httpx.URL:
    """Returns url when it is https and every address its host resolves to is public, else raises. Submitted links
    are untrusted, and the runner holds the job's secrets, so neither a link nor a redirect may reach a private,
    loopback or link-local address such as the cloud metadata service."""
    if url.scheme != "https" or not url.host:
        raise ValueError(f"{url} is not an https URL")
    for *_, addr in socket.getaddrinfo(url.host, url.port or 443, proto=socket.IPPROTO_TCP):
        if not ipaddress.ip_address(addr[0]).is_global:
            raise ValueError(f"{url.host} resolves to a non-public address")
    return url


def download(url: str, dst: Path, max_mb: int) -> Path:
    """Streams url to dst with a size cap and returns the path. Redirects are followed by hand, at most
    MAX_REDIRECTS, so each hop gets the public_https check. A document's suffix comes from its content type, since
    docs.load_doc picks the parser by suffix and IR links often have none. SEC EDGAR refuses requests without a
    contact User-Agent, so sec.gov gets SEC_USER_AGENT."""
    target = public_https(httpx.URL(url))
    with httpx.Client(timeout=120) as client:
        for _ in range(MAX_REDIRECTS + 1):
            sec = target.host.endswith("sec.gov") and os.environ.get("SEC_USER_AGENT")
            r = client.send(client.build_request("GET", target, headers={"User-Agent": sec or UA}), stream=True)
            if not r.is_redirect:
                break
            r.close()
            target = public_https(target.join(r.headers["location"]))
        else:
            raise ValueError(f"{url} redirects more than {MAX_REDIRECTS} times")
        try:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").split(";")[0].strip()
            if ctype in DOC_SUFFIX:
                dst = dst.with_suffix(DOC_SUFFIX[ctype])
            size = 0
            with dst.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    size += len(chunk)
                    if size > max_mb << 20:
                        raise ValueError(f"{url} is over {max_mb} MB")
                    f.write(chunk)
        finally:
            r.close()
    return dst


def process(sub: dict, root: Path) -> str:
    """Builds the call folder for 1 submission, runs the pipeline, publishes it and returns the call id."""
    call_id = f"{sub['ticker']}_{sub['quarter']}"
    raw, out = root / "raw" / call_id, root / "data" / call_id
    raw.mkdir(parents=True, exist_ok=True)
    release = download(sub["release_url"], raw / "release.html", MAX_DOC_MB)   # documents first: they fail fast
    prior = download(sub["prior_url"], raw / "prior.html", MAX_DOC_MB) if sub.get("prior_url") else None
    audio = download(sub["audio_url"], raw / "call.audio", MAX_AUDIO_MB)
    args = ["--ticker", sub["ticker"], "--quarter", sub["quarter"], "--etf", sub["etf"],
            "--call-start", datetime.fromisoformat(sub["call_start"]).astimezone(ET).isoformat(), "--audio", str(audio),
            "--release", str(release), "--upload", "--out", str(out)]
    if sub.get("company"):
        args += ["--company", sub["company"]]
    if prior:
        args += ["--prior", str(prior)]
    ingest.main(args)
    return publish(run(out))


def main() -> int:
    """Processes at most 1 submission. Exits 0 when the queue is empty or the call is done, 1 when it failed."""
    load_dotenv()
    db = db_client()
    rows = db.rpc("callmoves_claim_submission").execute().data
    if not rows:
        print("queue empty")
        return 0
    sub = rows[0]
    print(f"processing {sub['ticker']} {sub['quarter']} ({sub['id']})", flush=True)
    try:
        call_id = process(sub, Path(os.environ.get("CALLMOVES_WORK", "work")))
    except Exception as e:  # noqa: BLE001 - any failure is recorded on the row, the submitter sees it on /submit
        db.table(TABLE).update({"finished_at": datetime.now(UTC).isoformat(), "status": "failed",
                                "error": f"{type(e).__name__}: {e}"[:300]}).eq("id", sub["id"]).execute()
        print(f"failed: {e}", file=sys.stderr)
        return 1
    db.table(TABLE).update({"finished_at": datetime.now(UTC).isoformat(), "status": "done", "call_id": call_id,
                            "error": None}).eq("id", sub["id"]).execute()
    print(f"done {call_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
