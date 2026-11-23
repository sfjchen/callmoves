"""Shared Claude access for the claim extractor and the novelty judge: 1 model name, 1 structured-output call, and
the tokens it used, so a run can record what its Claude calls cost. Modes, from CALLMOVES_LLM:
  unset  ANTHROPIC_API_KEY only (the public worker), a failed Claude step falls back to the rules
  auto   the key first (the plan's monthly API credits), then the logged-in Claude Code CLI (`claude -p`, the owner's
         subscription) once the key reports no credit left, for calls the owner adds
  cli    the CLI only
In auto and cli a failed Claude step stops the call instead of publishing rule-based output (see strict())."""
from __future__ import annotations

import json
import os
import subprocess
from collections import Counter

from pydantic import BaseModel

MODEL = "claude-sonnet-5-5"
# USD per million tokens for MODEL (2026-11 list price): input, output, cache read, 5-minute cache write
PRICE = {"input_tokens": 2.0, "output_tokens": 10.0, "cache_read_input_tokens": 0.1,
         "cache_creation_input_tokens": 2.5}
USAGE: Counter = Counter()   # tokens used by every parse() call in this process, keyed like PRICE
CLI_TIMEOUT_S = 900
credit_out = False   # set once the key reports an empty credit balance in this process, auto mode then uses the CLI


def backend() -> str | None:
    """Returns "cli" (Claude Code subscription), "api" (ANTHROPIC_API_KEY) or None when no Claude is configured.
    Auto mode returns "api" while the key has credit and "cli" after, so stats record what each call billed."""
    mode, key = os.environ.get("CALLMOVES_LLM"), os.environ.get("ANTHROPIC_API_KEY")
    if mode == "cli" or (mode == "auto" and (not key or credit_out)):
        return "cli"
    return "api" if key else None


def strict() -> bool:
    """Returns whether a failed Claude step must stop the call (owner-added calls, auto and cli modes)."""
    return os.environ.get("CALLMOVES_LLM") in ("auto", "cli")


def parse[T: BaseModel](system: str, content: str | list[dict], schema: type[T], max_tokens: int = 8000,
                         model: str = MODEL) -> T:
    """Returns Claude's reply to `content` parsed into `schema`. Raises on API or CLI errors, callers fall back to
    rules unless strict(). USAGE counts tokens of every model, PRICE holds MODEL's rates. The CLI path joins the
    content blocks into 1 prompt (it has no cache_control) and strips ANTHROPIC_API_KEY so the CLI cannot bill the
    key."""
    if backend() == "cli":
        prompt = content if isinstance(content, str) else "\n\n".join(b["text"] for b in content)
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        proc = subprocess.run(
            ["claude", "-p", "--model", model, "--system-prompt", system, "--tools", "", "--no-session-persistence",
             "--output-format", "json", "--json-schema", json.dumps(schema.model_json_schema())],
            input=prompt, capture_output=True, text=True, env=env, timeout=CLI_TIMEOUT_S, check=False)
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            out = {"is_error": True, "result": proc.stderr or proc.stdout}
        # the CLI's own message (a usage limit, a login prompt) is the diagnosis, a bare exit code hides it
        if proc.returncode or out.get("is_error") or out.get("structured_output") is None:
            raise RuntimeError(f"claude -p exit {proc.returncode}: {str(out.get('result')).strip()[-300:]}")
        for k in PRICE:
            USAGE[k] += out.get("usage", {}).get(k, 0) or 0
        return schema.model_validate(out["structured_output"])
    import anthropic

    try:
        resp = anthropic.Anthropic().messages.parse(model=model, max_tokens=max_tokens, system=system,
                                                    messages=[{"role": "user", "content": content}],
                                                    output_format=schema)
    except anthropic.APIStatusError as e:
        # the monthly credits ran out: auto mode moves this and every later call to the subscription
        if os.environ.get("CALLMOVES_LLM") != "auto" or "credit balance" not in str(e).lower():
            raise
        global credit_out
        credit_out = True
        print("  API credits used up, switching to the Claude Code subscription", flush=True)
        return parse(system, content, schema, max_tokens, model)
    for k in PRICE:
        USAGE[k] += getattr(resp.usage, k, 0) or 0
    return resp.parsed_output
