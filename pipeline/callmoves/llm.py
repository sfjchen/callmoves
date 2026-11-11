"""Shared Claude access for the claim extractor and the novelty judge: 1 model name, 1 structured-output call, and
the tokens it used, so a run can record what its Claude calls cost."""
from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

MODEL = "claude-sonnet-5-5"
# USD per million tokens for MODEL (2026-11 list price): input, output, cache read, 5-minute cache write
PRICE = {"input_tokens": 2.0, "output_tokens": 10.0, "cache_read_input_tokens": 0.1,
         "cache_creation_input_tokens": 2.5}
USAGE: Counter = Counter()   # tokens used by every parse() call in this process, keyed like PRICE


def parse[T: BaseModel](system: str, content: str | list[dict], schema: type[T], max_tokens: int = 8000,
                         model: str = MODEL) -> T:
    """Returns Claude's reply to `content` parsed into `schema`. Raises on API errors, callers fall back to rules.
    USAGE counts tokens of every model, PRICE holds MODEL's rates."""
    import anthropic

    resp = anthropic.Anthropic().messages.parse(model=model, max_tokens=max_tokens, system=system,
                                                messages=[{"role": "user", "content": content}], output_format=schema)
    for k in PRICE:
        USAGE[k] += getattr(resp.usage, k, 0) or 0
    return resp.parsed_output
