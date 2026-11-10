// Mirrors pipeline/callmoves/models.py (CallOutput). The pipeline writes one JSON per call into web/data/.
export type CallMeta = {
  ticker: string;
  quarter: string;
  call_start: string;
  etf: string;
  audio_url: string | null;
  company: string | null;
  audio_offset_ms?: number; // audio position of the call start, 0 when the recording starts with the call
};

export type Segment = {
  seg_id: number;
  t0_ms: number;
  t1_ms: number;
  speaker: string;
  text: string;
  parts: { t0_ms: number; text: string }[];
  ts: string;
};

export type Bar = {
  ts: string;
  px: number;
  etf_px: number;
  vol: number;
  adj_ret: number;
  cum_adj_ret: number;
  vol_mult: number;
  flagged: boolean;
};

export type Claim = {
  claim_id: number;
  seg_id: number;
  speaker: string;
  quote: string;
  metric: string;
  value: string | null;
  period: string | null;
  kind: "guidance" | "result" | "qualitative";
  ts: string;
  novel: boolean | null;
  matched_doc: "release" | "prior_call" | null;
  matched_text: string | null;
  vs_consensus_pct?: number | null; // (spoken value - consensus) / |consensus|, EPS and revenue claims only
  consensus_role?: Estimate["role"] | null;
};

export type Moment = {
  rank: number;
  claim_id: number;
  seg_id: number;
  ts: string;
  t0_ms: number;
  speaker: string;
  quote: string;
  metric: string;
  kind: string;
  novel: boolean;
  matched_doc: string | null;
  matched_text: string | null;
  adj_ret_5m: number;
  ret_5m: number;
  etf_ret_5m: number;
  vol_mult: number;
  nearest_flag_min: number;
  score: number;
  confidence: "high" | "medium" | "low";
  pre_move?: boolean;
  section?: "prepared" | "qa";
  question?: string | null;
  vs_consensus_pct?: number | null;
  consensus_role?: Estimate["role"] | null;
};

// Analyst consensus for 1 period and metric. basis: at_report is frozen with the reported quarter, before_call predates
// the call, after_call was fetched after it and already reflects the call.
export type Estimate = {
  period: string;
  period_end: string;
  role: "reported" | "next_quarter" | "full_year";
  metric: "eps" | "revenue";
  consensus: number;
  actual: number | null;
  surprise_pct: number | null;
  as_of: string;
  basis: "at_report" | "before_call" | "after_call";
  source: "alphavantage" | "yfinance";
};

// Set when the stock did not trade during the call: the move from the last trade before it to just after the next open.
export type OpenReaction = {
  from_ts: string;
  to_ts: string;
  adj_ret: number;
  ret: number;
  etf_ret: number;
  vol_mult: number;
};

export type CallOutput = {
  call: CallMeta;
  call_end: string;
  segments: Segment[];
  bars: Bar[];
  claims: Claim[];
  moments: Moment[];
  llm_used: boolean;
  candidate_seg_ids: number[];
  open_reaction?: OpenReaction | null;
  estimates?: Estimate[];
};

export type CallSummary = {
  id: string;
  ticker: string;
  quarter: string;
  company: string | null;
  call_start: string;
  n_moments: number;
  n_flagged: number;
  spark: number[]; // cum_adj_ret over the call window, downsampled to about 60 points for the library sparkline
  top_moment: Pick<Moment, "ts" | "quote" | "adj_ret_5m" | "speaker"> | null;
};
