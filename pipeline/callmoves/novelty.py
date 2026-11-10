"""Step 6: is each claim new, or already in the earnings release or last quarter's call?

A claim already in the release cannot explain a move during the call. Rule-based matching when offline:
the claim's numbers appear in the document, or the sentence overlaps heavily with a document sentence.
"""
from __future__ import annotations

import os
import re

from pydantic import BaseModel

from . import llm
from .claims import NUMBER, split_sentences
from .models import Claim

STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "we", "our", "is", "are", "was", "were", "be",
    "been", "it", "this", "that", "with", "as", "at", "by", "from", "than", "up", "down", "over", "under",
    "about", "into", "out", "which", "who", "you", "i", "they", "them", "their", "its",
}


_UNIT = {"billion": 1e9, "b": 1e9, "million": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}


def _num_values(text: str, table_scale: float = 1e6, dollars_only: bool = False) -> set[float]:
    """Numbers in a text as plain magnitudes. '$21.9 billion' -> 2.19e10, '18%' -> 18.
    A bare dollar figure of 1,000 or more also gets a table reading (x table_scale), because releases
    print '$ 21,870' under a '$ in millions' header while the call says '$21.9 billion'.
    dollars_only keeps the dollar figures, which is what an EPS or revenue consensus compares against."""
    out: set[float] = set()
    for raw in NUMBER.findall(text):
        m = re.match(
            r"(\$?)\s?([\d,]*\.?\d+)\s*(%|percent|billion|million|thousand|basis points|bps|[bmk](?![a-z]))?",
            raw.strip(), re.IGNORECASE)
        if not m:
            continue
        dollar, num, unit = m.group(1), m.group(2).replace(",", ""), (m.group(3) or "").lower()
        try:
            v = float(num)
        except ValueError:
            continue
        if dollars_only and not dollar:
            continue
        if unit in ("%", "percent", "basis points", "bps"):
            out.add(round(v, 4)); continue
        scale = _UNIT.get(unit, 1.0)
        out.add(v * scale)
        if dollar and not unit and v >= 1000:
            out.add(v * table_scale)
    return out


def _nums_match(a: set[float], b: set[float], tol: float = 0.01) -> bool:
    """Exact for small numbers (percentages, basis points), 1% tolerance for dollar magnitudes (rounding on the
    call)."""
    return any(x == y or (x >= 1e5 and abs(x - y) / x <= tol) for x in a for y in b)


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in STOP and len(w) > 2}


def best_match(quote: str, doc: str) -> tuple[float, str]:
    """Highest Jaccard overlap between the quote and any sentence in the document."""
    q = _words(quote)
    best, best_s = 0.0, ""
    for sent in split_sentences(doc):
        w = _words(sent)
        if not w or not q:
            continue
        j = len(q & w) / len(q | w)
        if j > best:
            best, best_s = j, sent
    return best, best_s


def judge_novelty_rules(claim: Claim, release: str, prior: str, overlap: float = 0.5) -> Claim:
    """Marks a claim not novel when its numbers or, for a claim without numbers, its wording appear in a document."""
    docs = (("release", release), ("prior_call", prior))
    nums = _num_values(claim.quote)
    for name, doc in docs:
        if not doc.strip():
            continue
        if nums:
            for sent in split_sentences(doc):
                if _nums_match(nums, _num_values(sent)):
                    return claim.model_copy(update={"novel": False, "matched_doc": name, "matched_text": sent[:300]})
        j, sent = best_match(claim.quote, doc)
        # same wording with different numbers is new information (a raised guide), so overlap alone
        # only counts when the claim carries no numbers
        if j >= overlap and not nums:
            return claim.model_copy(update={"novel": False, "matched_doc": name, "matched_text": sent[:300]})
    return claim.model_copy(update={"novel": True, "matched_doc": None, "matched_text": None})


class _VerdictLLM(BaseModel):
    claim_id: int
    novel: bool
    matched_doc: str | None = None
    matched_text: str | None = None


class _VerdictsLLM(BaseModel):
    verdicts: list[_VerdictLLM]


BATCH = 25

_SYSTEM = (
    "You decide whether statements from an earnings call are new information relative to two reference documents: "
    "the earnings release published before the call and the prior quarter's materials. A statement is NOT novel when "
    "the same fact or number already appears in a document, even if worded differently. If not novel, set matched_doc "
    "to 'release' or 'prior_call' and matched_text to the sentence it matches, copied from the document. Return one "
    "verdict per claim_id."
)


def judge_novelty_llm(claims: list[Claim], release: str, prior: str) -> list[Claim]:
    """Judges novelty with Claude in batches of BATCH claims, the documents cached across batches. A claim the model
    skips, or calls not novel with a matched_text that is not in the named document, falls back to the rules."""
    by_id = {c.claim_id: c for c in claims}
    flat = {doc: " ".join(text.split()).casefold() for doc, text in (("release", release), ("prior_call", prior))}
    out: dict[int, Claim] = {}
    for i in range(0, len(claims), BATCH):
        batch = claims[i:i + BATCH]
        listing = "\n".join(f"[claim_id {c.claim_id}] {c.speaker}: {c.quote}" for c in batch)
        content = [
            {"type": "text", "text": "Earnings release:\n" + release},
            {"type": "text", "text": "Prior-quarter materials:\n" + (prior or "(none)"),
             "cache_control": {"type": "ephemeral"}},   # the two documents are reused by every batch
            {"type": "text", "text": "Statements:\n" + listing},
        ]
        for v in llm.parse(_SYSTEM, content, _VerdictsLLM).verdicts:
            c = by_id.get(v.claim_id)
            if c is None:
                continue
            md = v.matched_doc if v.matched_doc in ("release", "prior_call") else None
            text = None if v.novel else (v.matched_text or "")[:300]
            # a "not novel" verdict cuts the score, so its evidence must be in the document it names, else the
            # rules decide that claim
            if not v.novel and (md is None or not text.strip() or " ".join(text.split()).casefold() not in flat[md]):
                continue
            out[c.claim_id] = c.model_copy(update={"novel": v.novel, "matched_doc": None if v.novel else md,
                                                   "matched_text": text})
    # anything the model skipped falls back to the rules
    return [out.get(c.claim_id) or judge_novelty_rules(c, release, prior) for c in claims]


def judge_novelty(claims: list[Claim], release: str, prior: str) -> list[Claim]:
    """Judges every claim with Claude when ANTHROPIC_API_KEY is set, falling back to the rules on failure."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return judge_novelty_llm(claims, release, prior)
        except Exception as e:  # noqa: BLE001
            print(f"claude novelty failed, using rules: {str(e)[:160]}")
    return [judge_novelty_rules(c, release, prior) for c in claims]
