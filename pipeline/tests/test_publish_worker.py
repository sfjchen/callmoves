from contextlib import contextmanager
from pathlib import Path

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


class FakeResponse:
    def __init__(self, ctype: str, chunks: list[bytes]):
        self.headers, self._chunks = {"content-type": ctype}, chunks

    def raise_for_status(self):
        pass

    def iter_bytes(self, _size):
        yield from self._chunks


def test_download_takes_the_suffix_from_the_content_type_and_caps_the_size(monkeypatch, tmp_path):
    def fake(ctype, chunks):
        @contextmanager
        def stream(*_a, **_k):
            yield FakeResponse(ctype, chunks)
        monkeypatch.setattr(worker.httpx, "stream", stream)

    fake("application/pdf; charset=binary", [b"%PDF"])
    assert worker.download("https://x/release", tmp_path / "release.html", 1).name == "release.pdf"
    fake("audio/mpeg", [b"0" * (1 << 20), b"0"])
    with pytest.raises(ValueError, match="over 1 MB"):
        worker.download("https://x/call", tmp_path / "call.audio", 1)
