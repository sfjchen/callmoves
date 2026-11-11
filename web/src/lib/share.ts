// Shared-link helpers for the replay page, its metadata and its preview image: which moment a ?m=<rank> link points
// at, and the 1-line summary of that moment the page description and the image both show.
import { clock, pct, shortSpeaker } from "./format";
import type { CallOutput, Moment } from "./types";

/**
 * Returns the moment whose rank is the ?m= value, else the top moment, or undefined for a call without moments.
 */
export function sharedMoment(call: CallOutput, m: unknown): Moment | undefined {
  const rank = typeof m === "string" && /^\d{1,2}$/.test(m) ? Number(m) : 1;
  return call.moments.find((x) => x.rank === rank) ?? call.moments[0];
}

/**
 * Returns what a moment's move is measured against: the sector ETF, or the market or peers model when one is
 * shipped for the call.
 */
export function benchName(call: CallOutput): string {
  const b = call.context?.benchmark;
  return b === "market" ? "market model" : b === "peers" ? "peers model" : call.call.etf;
}

/**
 * Shortens a quote to at most n characters, cutting at a word boundary and adding an ellipsis.
 */
export function clip(s: string, n: number): string {
  return s.length <= n ? s : s.slice(0, s.lastIndexOf(" ", n - 3)).replace(/[,;:.]$/, "") + "...";
}

/**
 * Summarizes a moment in 1 line: its time, the move vs the benchmark and the start of the quote.
 */
export function momentLine(call: CallOutput, m: Moment): string {
  const when = call.open_reaction ? "at the next open" : "in 5 min";
  return `${clock(m.ts)} ET: ${pct(m.adj_ret_5m)} vs ${benchName(call)} ${when} after ` +
    `${shortSpeaker(m.speaker)} said "${clip(m.quote, 140)}"`;
}
