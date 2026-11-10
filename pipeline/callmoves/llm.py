"""Shared Claude access for the claim extractor and the novelty judge: 1 model name and 1 structured-output call."""
from __future__ import annotations

from pydantic import BaseModel

MODEL = "claude-sonnet-5-5"


def parse[T: BaseModel](system: str, content: str | list[dict], schema: type[T], max_tokens: int = 8000) -> T:
    """Returns Claude's reply to `content` parsed into `schema`. Raises on API errors, callers fall back to rules."""
    import anthropic

    resp = anthropic.Anthropic().messages.parse(model=MODEL, max_tokens=max_tokens, system=system,
                                                messages=[{"role": "user", "content": content}], output_format=schema)
    return resp.parsed_output
