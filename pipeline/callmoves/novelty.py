"""Step 6: is each claim new, or already in the earnings release or last quarter's call?

A claim already in the release cannot explain a move during the call. Rule-based matching when offline:
the claim's numbers appear in the document, or the sentence overlaps heavily with a document sentence.
"""
from __future__ import annotations

import os
import re

from pydantic import BaseModel

from .claims import NUMBER, split_sentences
from .models import Claim

STOP = set("the a an and or of to in on for we our is are was were be been it this that with as at by from "
           "than up down over under about into out which who you i they them their its".split())


def _norm_num(s: str) -> str:
    s = s.lower().replace(",", "").replace("$", "").replace(" ", "")
    s = re.sub(r"percent", "%", s)
    s = re.sub(r"billion", "b", s); s = re.sub(r"million", "m", s)
    return s.rstrip(".")


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
    docs = (("release", release), ("prior_call", prior))
    nums = {_norm_num(n) for n in NUMBER.findall(claim.quote)}
    for name, doc in docs:
        doc_nums = {_norm_num(n) for n in NUMBER.findall(doc)}
        hit = nums & doc_nums
        if nums and hit:
            # find the sentence carrying the number
            sent = next((s for s in split_sentences(doc) if any(_norm_num(n) in hit for n in NUMBER.findall(s))), "")
            return claim.model_copy(update={"novel": False, "matched_doc": name, "matched_text": sent})
        j, sent = best_match(claim.quote, doc)
        sent_nums = {_norm_num(n) for n in NUMBER.findall(sent)}
        # same wording with different numbers is new information (a raised guide), so overlap alone
        # only counts when the claim carries no numbers or the matched sentence shares one
        if j >= overlap and (not nums or nums & sent_nums):
            return claim.model_copy(update={"novel": False, "matched_doc": name, "matched_text": sent})
    return claim.model_copy(update={"novel": True, "matched_doc": None, "matched_text": None})


class _NoveltyLLM(BaseModel):
    novel: bool
    matched_doc: str | None = None
    matched_text: str | None = None


def judge_novelty_llm(claim: Claim, release: str, prior: str, model: str = "claude-sonnet-5-5") -> Claim:
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.parse(
        model=model, max_tokens=1000,
        system=("Decide whether a statement from an earnings call is new information relative to two reference documents. "
                "It is NOT novel if the same fact or number appears in either document. If not novel, set matched_doc to "
                "'release' or 'prior_call' and matched_text to the verbatim sentence it matches."),
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "Earnings release:\n" + release, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Prior-quarter call transcript:\n" + prior, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"Statement ({claim.speaker}): {claim.quote}"},
        ]}],
        output_format=_NoveltyLLM,
    )
    r = resp.parsed_output
    md = r.matched_doc if r.matched_doc in ("release", "prior_call") else None
    return claim.model_copy(update={"novel": r.novel, "matched_doc": md, "matched_text": r.matched_text})


def judge_novelty(claims: list[Claim], release: str, prior: str) -> list[Claim]:
    fn = judge_novelty_llm if os.environ.get("ANTHROPIC_API_KEY") else judge_novelty_rules
    return [fn(c, release, prior) for c in claims]
