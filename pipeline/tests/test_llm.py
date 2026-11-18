"""Checks the subscription CLI backend of llm.parse offline: it strips the API key, parses the structured output,
counts tokens, and makes a failed Claude step raise instead of falling back to the rules."""
import json
import subprocess
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from callmoves import llm
from callmoves.claims import extract_claims
from callmoves.models import Segment


class Nums(BaseModel):
    nums: list[float]


def fake_cli(out: dict, seen: dict):
    """Returns a subprocess.run stand-in that records its call and prints `out` as the CLI's JSON."""
    def run(cmd, **kw):
        seen.update(cmd=cmd, env=kw["env"], input=kw["input"])
        return SimpleNamespace(stdout=json.dumps(out))
    return run


def test_cli_backend_strips_the_key_and_parses_structured_output(monkeypatch):
    monkeypatch.setenv("CALLMOVES_LLM", "cli")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    seen: dict = {}
    monkeypatch.setattr(subprocess, "run", fake_cli(
        {"structured_output": {"nums": [4.2]}, "usage": {"input_tokens": 3, "output_tokens": 5}}, seen))
    llm.USAGE.clear()
    got = llm.parse("sys", [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], Nums)
    assert got.nums == [4.2]
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert seen["input"] == "a\n\nb" and "--json-schema" in seen["cmd"]
    assert llm.USAGE["output_tokens"] == 5 and llm.backend() == "cli"


def test_cli_failure_stops_the_call_instead_of_using_rules(monkeypatch):
    monkeypatch.setenv("CALLMOVES_LLM", "cli")
    monkeypatch.setattr(subprocess, "run", fake_cli({"is_error": True, "result": "usage limit"}, {}))
    seg = Segment(seg_id=0, t0_ms=0, t1_ms=5000, speaker="CFO", text="We expect revenue of $4 billion next quarter.")
    with pytest.raises(RuntimeError, match="usage limit"):
        extract_claims([seg])
