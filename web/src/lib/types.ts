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
  speaker: string;
  text: string;
  adj_ret_5m: number | null; // sector-adjusted return over the 5 min after the turn, as on the moment cards
};

export type Bar = {
  ts: string;
  cum_adj_ret: number;
  vol_mult: number;
  flagged: boolean;
};

export type Moment = {
  rank: number;
  seg_id: number;
  ts: string;
  t0_ms: number;
  speaker: string;
  quote: string;
  novel: boolean;
  matched_doc: string | null;
  adj_ret_5m: number;
  ret_5m: number;
  etf_ret_5m: number;
  bench_ret_5m?: number | null; // what adj_ret_5m is measured against (the ETF unless a market model is shipped)
  spy_ret_5m?: number | null;
  peer_ret_5m?: number | null;  // equal-weight peer index over the same 5 minutes
  spillover?: "sector" | "company" | "mixed" | null; // did peers move with the stock in those minutes
  vol_mult: number;
  nearest_flag_min: number;
  confidence: "high" | "medium" | "low";
  pre_move?: boolean;
  section?: "prepared" | "qa";
  question?: string | null;
  vs_consensus_pct?: number | null;
};

// Analyst consensus for 1 period and metric. basis: at_report is frozen with the reported quarter, before_call predates
// the call, after_call was fetched after it and already reflects the call.
export type Estimate = {
  role: "reported" | "next_quarter" | "full_year";
  metric: "eps" | "revenue";
  consensus: number;
  actual: number | null;
  surprise_pct: number | null;
  as_of: string;
  basis: "at_report" | "before_call" | "after_call";
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

// What processing the call took. Fields are null for steps that ran before tracking existed or ran for free.
export type Stats = {
  audio_min: number | null;
  ingest_s: number | null;
  gpu: string | null;
  gpu_s: number | null;
  gpu_usd: number | null;
  claude_s: number | null;
  claude_usd: number | null;
};

export type CallOutput = {
  call: CallMeta;
  call_end: string;
  segments: Segment[];
  bars: Bar[];
  moments: Moment[];
  llm_used: boolean;
  candidate_seg_ids: number[];
  n_claims: number;
  summary: CallSummary;
  open_reaction?: OpenReaction | null;
  estimates?: Estimate[];
  stats?: Stats | null;
  context?: Context | null;
};

// Peer and cross-asset prices on 1 minute grid over the call, for the comps panel.
export type ContextSeries = {
  sym: string;
  role: "stock" | "sector" | "peer" | "market" | "rates" | "dollar" | "credit" | "gold" | "oil";
  px: (number | null)[];
};

export type Context = {
  ts: string[];
  series: ContextSeries[];
  benchmark: "etf" | "market" | "peers";
  betas: Record<string, number>;
  r2: number | null;
};

export type CallSummary = {
  id: string;
  ticker: string;
  quarter: string;
  company: string | null;
  call_start: string;
  etf?: string | null;
  themes?: string[];
  n_moments?: number;
  spark: number[]; // cum_adj_ret over the call window, downsampled to about 60 points for the library sparkline
  top_moment: (Pick<Moment, "ts" | "quote" | "adj_ret_5m" | "speaker"> &
    Partial<Pick<Moment, "vol_mult" | "spillover">>) | null;
};
