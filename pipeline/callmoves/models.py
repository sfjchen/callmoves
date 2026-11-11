"""Pydantic models shared by every pipeline step: the contract the web app reads as JSON."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class CallMeta(BaseModel):
    ticker: str
    quarter: str
    call_start: datetime  # wall clock, tz-aware
    etf: str
    audio_url: str | None = None
    company: str | None = None
    audio_offset_ms: int = 0  # audio position of the call start: + for lead-in music, - if the recording started late

    @property
    def call_id(self) -> str:
        """Identifies the call in URLs, file names and the callmoves_calls table, e.g. WFC_Q3-2026."""
        return f"{self.ticker}_{self.quarter}".replace(" ", "")


class Part(BaseModel):
    """One ASR segment inside a merged speaker turn, so a quote can be timed to the sentence, not the turn."""
    t0_ms: int
    text: str


class Segment(BaseModel):
    seg_id: int
    t0_ms: int
    t1_ms: int
    speaker: str
    text: str
    parts: list[Part] = []
    ts: datetime | None = None  # filled by align()
    adj_ret_5m: float | None = None   # sector-adjusted return over the 5 minutes after ts, filled by run()

    def time_of(self, quote: str) -> int:
        """Audio offset of the part containing the quote, else the turn start."""
        for p in self.parts:
            if quote[:40] in p.text or p.text[:40] in quote:
                return p.t0_ms
        return self.t0_ms


class Bar(BaseModel):
    ts: datetime
    px: float
    etf_px: float
    vol: float
    adj_ret: float = 0.0       # stock minus ETF 1-minute return
    cum_adj_ret: float = 0.0   # cumulative adjusted return since the first bar
    vol_mult: float = 1.0      # volume / trailing median volume
    flagged: bool = False


class Claim(BaseModel):
    claim_id: int
    seg_id: int
    speaker: str
    quote: str = Field(description="Verbatim sentence from the transcript")
    metric: str = Field(description="What is being claimed about, e.g. revenue, FY guidance, gross margin")
    value: str | None = Field(default=None, description="The number as spoken, e.g. '$412 million', '18%'")
    period: str | None = Field(default=None, description="e.g. Q3, FY2026, next quarter")
    kind: Literal["guidance", "result", "qualitative"]
    ts: datetime | None = None
    novel: bool | None = None
    matched_doc: Literal["release", "prior_call"] | None = None
    matched_text: str | None = None
    vs_consensus_pct: float | None = None   # (spoken value - consensus) / |consensus|, set for EPS and revenue claims
    consensus_role: Literal["reported", "next_quarter", "full_year"] | None = None   # the Estimate.role compared with


class Moment(BaseModel):
    rank: int
    claim_id: int
    seg_id: int
    ts: datetime
    t0_ms: int
    speaker: str
    quote: str
    metric: str
    kind: str
    novel: bool
    matched_doc: str | None
    matched_text: str | None
    adj_ret_5m: float
    ret_5m: float = 0.0
    etf_ret_5m: float = 0.0
    bench_ret_5m: float | None = None   # the benchmark adj_ret_5m is measured against (the ETF in the "etf" model)
    spy_ret_5m: float | None = None
    peer_ret_5m: float | None = None    # equal-weight peer index over the same bars
    # did peers move with the stock in these minutes: "sector" (with it), "company" (flat or against), "mixed"
    spillover: Literal["sector", "company", "mixed"] | None = None
    vol_mult: float
    nearest_flag_min: float
    score: float
    confidence: Literal["high", "medium", "low"] = "low"
    pre_move: bool = False   # the price event began in an earlier minute than the claim
    section: Literal["prepared", "qa"] = "prepared"
    question: str | None = None   # in Q&A, the analyst question this answer follows
    vs_consensus_pct: float | None = None
    consensus_role: Literal["reported", "next_quarter", "full_year"] | None = None


class Estimate(BaseModel):
    """1 analyst consensus figure for 1 period and metric, with the actual when the call reported that period."""
    period: str                    # fiscal label, e.g. Q3 FY2026, FY2026
    period_end: date
    role: Literal["reported", "next_quarter", "full_year"]
    metric: Literal["eps", "revenue"]
    consensus: float
    actual: float | None = None    # reported quarter only
    surprise_pct: float | None = None   # (actual - consensus) / |consensus|
    as_of: date                    # the day the value was current (report day for at_report)
    # at_report: frozen with the reported quarter, before_call: a snapshot or fetch before the call,
    # after_call: the current value fetched after the call, so the call's news is already in it
    basis: Literal["at_report", "before_call", "after_call"]
    source: Literal["alphavantage"]


class ContextSeries(BaseModel):
    sym: str
    role: Literal["stock", "sector", "peer", "market", "rates", "dollar", "credit", "gold", "oil"]
    px: list[float | None]   # last print at or before each Context.ts minute


class Context(BaseModel):
    """Peer and cross-asset prices over the call, 1 shared minute grid, for the comps panel, plus the benchmark
    the abnormal returns use and its fit (betas on the days before the call)."""
    ts: list[datetime]
    series: list[ContextSeries]
    benchmark: Literal["etf", "market", "peers"] = "etf"
    betas: dict[str, float] = {}
    r2: float | None = None


class OpenReaction(BaseModel):
    """The move from the last trade before the call to the minutes after the next open, for a call held while the
    stock was closed."""
    from_ts: datetime
    to_ts: datetime
    adj_ret: float
    ret: float
    etf_ret: float
    vol_mult: float


class TopMoment(BaseModel):
    ts: datetime
    quote: str
    adj_ret_5m: float
    speaker: str
    vol_mult: float = 1.0
    spillover: Literal["sector", "company", "mixed"] | None = None


class CallSummary(BaseModel):
    """The library row for 1 call: counts, the top moment and a downsampled cumulative adjusted return."""
    id: str
    ticker: str
    quarter: str
    company: str | None
    call_start: datetime
    etf: str | None = None
    themes: list[str] = []   # up to 3 topics the claims kept coming back to, e.g. NII, AI, guidance
    n_moments: int
    n_flagged: int
    spark: list[float]
    top_moment: TopMoment | None


class Stats(BaseModel):
    """What processing 1 call took: audio length, wall time and USD per paid service. Fields stay None for a step
    that ran before tracking existed or ran for free (the CPU fallback on the GitHub runner)."""
    audio_min: float | None = None
    ingest_s: float | None = None        # download excluded: transcription, diarization, bars, documents, upload
    gpu: str | None = None
    gpu_s: float | None = None
    gpu_usd: float | None = None
    claude_s: float | None = None        # claim extraction and novelty judging
    claude_tokens: dict[str, int] | None = None
    claude_usd: float | None = None

    @property
    def total_usd(self) -> float | None:
        """Returns the summed cost of the paid steps that were recorded, None when none was."""
        parts = [x for x in (self.gpu_usd, self.claude_usd) if x is not None]
        return round(sum(parts), 4) if parts else None


class CallOutput(BaseModel):
    call: CallMeta
    call_end: datetime
    segments: list[Segment]
    bars: list[Bar]
    claims: list[Claim]
    n_claims: int
    summary: CallSummary
    moments: list[Moment]
    llm_used: bool
    candidate_seg_ids: list[int] = []   # turns that preceded an abnormal minute, the only ones claims come from
    open_reaction: OpenReaction | None = None   # set when the stock did not trade during the call
    estimates: list[Estimate] = []
    stats: Stats | None = None
    context: Context | None = None
