"""Checks the Claude backends of llm.parse offline: the subscription CLI strips the API key, parses the structured
output and counts tokens, auto mode moves to the CLI once the key's credits run out, and a failed Claude step in an
owner-added call raises instead of falling back to the rules."""
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
        return SimpleNamespace(stdout=json.dumps(out), stderr="", returncode=1 if out.get("is_error") else 0)
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


def test_cli_crash_reports_the_cli_message(monkeypatch):
    """A CLI that exits non-zero without JSON raises with its stderr, not a bare exit code."""
    monkeypatch.setenv("CALLMOVES_LLM", "cli")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        stdout="", stderr="getaddrinfo ENOTFOUND api.anthropic.com", returncode=1))
    with pytest.raises(RuntimeError, match="exit 1.*ENOTFOUND"):
        llm.parse("sys", "a", Nums)


def test_auto_uses_credits_first_then_the_subscription(monkeypatch):
    """Auto mode bills the key until it reports no credit, then sends this and later calls through the CLI."""
    import anthropic
    import httpx

    monkeypatch.setenv("CALLMOVES_LLM", "auto")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "credit_out", False)
    assert llm.backend() == "api" and llm.strict()

    def broke(**kw):
        """Raises the 400 the API returns once the organization's credit balance is empty."""
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        raise anthropic.BadRequestError("Your credit balance is too low to access the Anthropic API.",
                                        response=httpx.Response(400, request=req), body=None)
    monkeypatch.setattr(anthropic.resources.Messages, "parse", lambda self, **kw: broke(**kw))
    seen: dict = {}
    monkeypatch.setattr(subprocess, "run", fake_cli({"structured_output": {"nums": [1.0]}}, seen))
    assert llm.parse("sys", "a", Nums).nums == [1.0]
    assert "ANTHROPIC_API_KEY" not in seen["env"] and llm.backend() == "cli"


def test_without_a_mode_a_credit_error_falls_back_to_rules(monkeypatch):
    """The public worker (no CALLMOVES_LLM) never touches the subscription and is not strict."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm.backend() == "api" and not llm.strict()
