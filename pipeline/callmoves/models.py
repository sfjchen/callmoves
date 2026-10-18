from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CallMeta(BaseModel):
    ticker: str
    quarter: str
    call_start: datetime  # wall clock, tz-aware
    etf: str = "IGV"
    audio_url: str | None = None
    company: str | None = None


class Segment(BaseModel):
    seg_id: int
    t0_ms: int
    t1_ms: int
    speaker: str
    text: str
    ts: datetime | None = None  # filled by align()


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
    vol_mult: float
    nearest_flag_min: float
    score: float


class CallOutput(BaseModel):
    call: CallMeta
    call_end: datetime
    segments: list[Segment]
    bars: list[Bar]
    claims: list[Claim]
    moments: list[Moment]
    llm_used: bool
