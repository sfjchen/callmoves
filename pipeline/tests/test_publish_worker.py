from pathlib import Path

import httpx
import pytest

from callmoves import worker
from callmoves.run import run

FIXTURE = Path(__file__).parent.parent / "fixtures" / "acme_q3_2026"


def test_run_fills_the_summary_and_each_segments_adjusted_return():
    out = run(FIXTURE)
    s = out.summary
    assert s.id == out.call.call_id == "ACME_Q3-FY2026"
    assert s.n_moments == len(out.moments) and s.n_flagged == sum(b.flagged for b in out.bars)
    assert 2 <= len(s.spark) <= 61 and s.top_moment.quote == out.moments[0].quote
    assert out.n_claims == len(out.claims)
    top = next(g for g in out.segments if g.seg_id == out.moments[0].seg_id)
    assert top.adj_ret_5m == out.moments[0].adj_ret_5m   # same window as the moment's own


ADDRS = {"files.example.com": "93.184.215.14", "cdn.example.com": "93.184.215.15", "internal.example.com": "10.0.0.5"}


@pytest.fixture
def web(monkeypatch):
    """Serves `routes` {path: (status, headers, body)} to worker.download through a mock transport, and resolves the
    example hosts to the addresses in ADDRS (internal.example.com is private)."""
    routes = {}

    def handler(req: httpx.Request) -> httpx.Response:
        status, headers, body = routes[req.url.host + req.url.path]
        return httpx.Response(status, headers=headers, content=body)

    real = httpx.Client
    monkeypatch.setattr(worker.httpx, "Client", lambda **k: real(transport=httpx.MockTransport(handler), **k))
    monkeypatch.setattr(worker.socket, "getaddrinfo", lambda host, *_a, **_k: [(2, 1, 6, "", (ADDRS[host], 443))])
    return routes


def test_download_takes_the_suffix_from_the_content_type_and_caps_the_size(web, tmp_path):
    web["files.example.com/release"] = (200, {"content-type": "application/pdf; charset=binary"}, b"%PDF")
    assert worker.download("https://files.example.com/release", tmp_path / "release.html", 1).name == "release.pdf"
    web["files.example.com/call"] = (200, {"content-type": "audio/mpeg"}, b"0" * ((1 << 20) + 1))
    with pytest.raises(ValueError, match="over 1 MB"):
        worker.download("https://files.example.com/call", tmp_path / "call.audio", 1)


def test_download_follows_public_redirects_and_refuses_private_or_plain_http_hops(web, tmp_path):
    web["files.example.com/a"] = (302, {"location": "https://cdn.example.com/a.pdf"}, b"")
    web["cdn.example.com/a.pdf"] = (200, {"content-type": "application/pdf"}, b"%PDF")
    assert worker.download("https://files.example.com/a", tmp_path / "a.html", 1).read_bytes() == b"%PDF"
    web["files.example.com/b"] = (302, {"location": "https://internal.example.com/secret"}, b"")
    with pytest.raises(ValueError, match="non-public"):
        worker.download("https://files.example.com/b", tmp_path / "b.html", 1)
    web["files.example.com/c"] = (301, {"location": "http://cdn.example.com/c"}, b"")
    with pytest.raises(ValueError, match="not an https"):
        worker.download("https://files.example.com/c", tmp_path / "c.html", 1)
    with pytest.raises(ValueError, match="non-public"):
        worker.download("https://internal.example.com/x", tmp_path / "x.html", 1)
