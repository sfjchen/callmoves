// Gold answer for the time-to-insight study: the start minute of the largest |sector-adjusted 5-minute move| inside
// the call window, computed from the bars alone so it does not depend on CallMoves' own moment ranking.
import type { CallOutput } from "./types";

const FIVE_MIN_MS = 5 * 60_000;
const TOLERANCE_MIN = 2; // an answer within this many minutes of the gold minute counts as correct

/**
 * Returns the timestamp of the bar that starts the largest |change in cum_adj_ret| over the next 5 minutes, among
 * bars from the call start to the call end that have a bar exactly 5 minutes later. Null when no bar qualifies.
 */
export function goldTs(d: CallOutput): string | null {
  const cum = new Map(d.bars.map((b) => [Date.parse(b.ts), b.cum_adj_ret]));
  const lo = Date.parse(d.call.call_start), hi = Date.parse(d.call_end);
  let best: string | null = null, big = -1;
  for (const b of d.bars) {
    const t = Date.parse(b.ts), end = cum.get(t + FIVE_MIN_MS);
    if (t < lo || t > hi || end === undefined) continue;
    if (Math.abs(end - b.cum_adj_ret) > big) { big = Math.abs(end - b.cum_adj_ret); best = b.ts; }
  }
  return best;
}

/**
 * Returns whether an answer clock time ("17:25", Eastern) is within TOLERANCE_MIN minutes of the gold clock time.
 */
export function withinGold(answer: string, gold: string): boolean {
  const mins = (c: string) => Number(c.split(":")[0]) * 60 + Number(c.split(":")[1]);
  return Math.abs(mins(answer) - mins(gold)) <= TOLERANCE_MIN;
}
