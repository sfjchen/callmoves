"""Step 5: pull structured claims out of management's statements.

Uses Claude (structured output) when ANTHROPIC_API_KEY is set, otherwise a rule-based extractor,
so the pipeline and tests run offline. Both return the same Claim shape.
"""
from __future__ import annotations

import os
import re

from pydantic import BaseModel

from .models import Claim, Segment

MANAGEMENT = re.compile(r"\b(ceo|cfo|coo|president|chief|founder|management)\b", re.I)
NUMBER = re.compile(r"(\$\s?\d[\d,.]*\s*(?:billion|million|thousand|[bmk])?|\d[\d,.]*\s?%|\b\d[\d,.]*\s*(?:basis points|bps|million|billion|customers|percent)\b)", re.I)
GUIDANCE = re.compile(r"\b(expect|expects|expecting|guidance|guide|outlook|raise|raising|raised|lower|lowering|forecast|anticipate|target|for the full year|for fiscal|next quarter|fourth quarter|q4)\b", re.I)
METRIC_WORDS = ["revenue", "gross margin", "operating margin", "margin", "free cash flow", "cash flow", "net retention",
                "retention", "customers", "bookings", "arr", "eps", "earnings per share", "operating income", "headcount",
                "buyback", "guidance", "churn", "pipeline", "backlog", "pricing"]
PERIOD = re.compile(r"\b(Q[1-4]|fiscal (?:year )?20\d\d|FY ?20\d\d|full[- ]year|next quarter|fourth quarter|third quarter|this quarter|20\d\d)\b", re.I)


def _claim_ts(seg: Segment, quote: str):
    """Wall-clock time of the sentence itself: turn time plus the offset of the ASR part that holds it."""
    if seg.ts is None:
        return None
    from datetime import timedelta
    return seg.ts + timedelta(milliseconds=seg.time_of(quote) - seg.t0_ms)


def is_management(speaker: str) -> bool:
    return bool(MANAGEMENT.search(speaker))


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def extract_claims_rules(segments: list[Segment]) -> list[Claim]:
    claims: list[Claim] = []
    for seg in segments:
        if not is_management(seg.speaker):
            continue
        for sent in split_sentences(seg.text):
            num = NUMBER.search(sent)
            guid = GUIDANCE.search(sent)
            if not num and not guid:
                continue
            low = sent.lower()
            metric = next((m for m in METRIC_WORDS if m in low), None)
            if metric is None and not num:
                continue
            if not num and len(sent.split()) < 8:   # "Now to guidance." is a transition, not a claim
                continue
            if guid:
                kind = "guidance"
            elif num:
                kind = "result"
            else:
                kind = "qualitative"
            per = PERIOD.search(sent)
            claims.append(Claim(
                claim_id=len(claims), seg_id=seg.seg_id, speaker=seg.speaker, quote=sent,
                metric=metric or "number", value=num.group(0).strip() if num else None,
                period=per.group(0) if per else None, kind=kind, ts=_claim_ts(seg, sent),
            ))
    return claims


class _ClaimLLM(BaseModel):
    seg_id: int
    quote: str
    metric: str
    value: str | None = None
    period: str | None = None
    kind: str


class _ClaimsLLM(BaseModel):
    claims: list[_ClaimLLM]


MODEL = "claude-sonnet-5-5"
CHUNK_CHARS = 6000

_SYSTEM = (
    "You extract financial claims from earnings-call speaker turns. A claim is one sentence that states a number, "
    "a change in guidance, or a material statement about the business (demand, pricing, margins, capital return, "
    "risk). Skip pleasantries, transitions and analysts' questions. For each claim return the seg_id of the turn it "
    "came from and `quote`, the sentence copied verbatim from that turn. kind is guidance, result or qualitative."
)


def _chunks(segments: list[Segment]) -> list[list[Segment]]:
    out, cur, size = [], [], 0
    for seg in segments:
        if cur and size + len(seg.text) > CHUNK_CHARS:
            out.append(cur); cur, size = [], 0
        cur.append(seg); size += len(seg.text)
    if cur:
        out.append(cur)
    return out


def extract_claims_llm(segments: list[Segment], model: str = MODEL) -> list[Claim]:
    import anthropic

    client = anthropic.Anthropic()
    by_id = {s.seg_id: s for s in segments}
    claims: list[Claim] = []
    for chunk in _chunks([s for s in segments if is_management(s.speaker)]):
        body = "\n\n".join(f"[seg_id {s.seg_id}] {s.speaker}: {s.text}" for s in chunk)
        resp = client.messages.parse(model=model, max_tokens=8000, system=_SYSTEM,
                                     messages=[{"role": "user", "content": body}], output_format=_ClaimsLLM)
        for c in resp.parsed_output.claims:
            seg = by_id.get(c.seg_id)
            if seg is None or c.quote.strip() not in seg.text:   # quote must exist verbatim or the claim is dropped
                continue
            kind = c.kind if c.kind in ("guidance", "result", "qualitative") else "qualitative"
            claims.append(Claim(claim_id=len(claims), seg_id=seg.seg_id, speaker=seg.speaker, quote=c.quote.strip(),
                                metric=c.metric, value=c.value, period=c.period, kind=kind, ts=_claim_ts(seg, c.quote)))
    return claims


def extract_claims(segments: list[Segment]) -> tuple[list[Claim], bool]:
    """Returns (claims, llm_used). Falls back to the rules if the API call fails (no credits, network)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return extract_claims_llm(segments), True
        except Exception as e:  # noqa: BLE001
            print(f"claude extraction failed, using rules: {str(e)[:160]}")
    return extract_claims_rules(segments), False
