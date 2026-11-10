// Server-only store for the time-to-insight study: picks the calls a trial may use, validates and scores a trial
// against the gold minute, writes it to callmoves_study (service key, no RLS policies) and aggregates the results.
// The rate limit and IP hash come from submit.ts so both tables hash visitors the same way.
import { getCall, listCalls } from "./data";
import { clock } from "./format";
import { goldTs, withinGold } from "./gold";
import { SB_URL, sb } from "./supabase";
import { count, hashIp } from "./submit";

const SB_SERVICE = process.env.SUPABASE_SERVICE_KEY;
const PER_IP_HOUR = 20;  // trials per IP per hour
const UNDER_S = 120;     // the metric's threshold: top mover found in under 2 minutes
export const CONDITIONS = ["callmoves", "baseline"] as const;
export type Condition = (typeof CONDITIONS)[number];
export type StudyResult = { ok: true } | { ok: false; status: number; error: string };
export type CondStats = { condition: Condition; trials: number; medianS: number; correct: number; under: number;
  correctUnder: number };

/**
 * Lists the ids of calls a trial may use: real calls (not the fictional ACME one) that traded during the call, so
 * the gold minute exists.
 */
export async function studyCalls(): Promise<string[]> {
  const calls = await Promise.all((await listCalls()).filter((c) => c.ticker !== "ACME").map((c) => getCall(c.id)));
  return calls.flatMap((c) => (c && !c.open_reaction && goldTs(c) ? [`${c.call.ticker}_${c.call.quarter}`] : []));
}

/**
 * Validates 1 trial from the browser, scores it against the gold minute, rate-limits by hashed IP and stores it.
 * `ip` comes from the x-forwarded-for header Vercel sets.
 */
export async function saveTrial(raw: Record<string, unknown>, ip: string): Promise<StudyResult> {
  const fail = (status: number, error: string): StudyResult => ({ ok: false, status, error });
  if (!SB_URL || !SB_SERVICE) return fail(503, "The study is not configured here.");
  const s = (k: string) => (typeof raw[k] === "string" ? (raw[k] as string).trim() : "");
  const [pid, callId, cond, clk, quote] = [s("participant"), s("call_id"), s("condition"), s("answer_clock"),
    s("answer_quote")];
  const secs = Number(raw.seconds);
  if (!/^[A-Za-z0-9-]{4,40}$/.test(pid)) return fail(400, "Bad participant id.");
  if (!CONDITIONS.includes(cond as Condition)) return fail(400, "Bad condition.");
  if (!/^([01]?\d|2[0-3]):[0-5]\d$/.test(clk)) return fail(400, "Enter the time like 17:25.");
  if (quote.length > 400) return fail(400, "The quote is too long.");
  if (!Number.isFinite(secs) || secs <= 0 || secs > 86400) return fail(400, "Bad timer value.");
  const call = await getCall(callId);
  const gold = call && goldTs(call);
  if (!gold) return fail(400, "Unknown call.");
  const ipHash = hashIp(ip), hourAgo = new Date(Date.now() - 3600e3).toISOString();
  if (await count(`ip_hash=eq.${ipHash}&created_at=gt.${hourAgo}`, "callmoves_study") >= PER_IP_HOUR)
    return fail(429, "Too many trials this hour. Try again later.");
  const res = await sb("callmoves_study", SB_SERVICE, {
    method: "POST",
    body: JSON.stringify({
      participant: pid, call_id: callId, condition: cond, seconds: secs, answer_clock: clk,
      answer_quote: quote || null, correct: withinGold(clk, clock(gold)), ip_hash: ipHash,
    }),
  });
  return res.ok ? { ok: true } : fail(500, "Could not save the trial.");
}

/**
 * Returns the middle value of a non-empty list, averaging the 2 middle values for an even count.
 */
function median(xs: number[]): number {
  const v = [...xs].sort((a, b) => a - b), m = v.length >> 1;
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

/**
 * Aggregates the stored trials per condition and counts participants. Never returns a row, answer or quote.
 */
export async function studyStats(): Promise<{ participants: number; byCond: CondStats[] }> {
  if (!SB_URL || !SB_SERVICE) return { participants: 0, byCond: [] };
  const res = await sb("callmoves_study?select=participant,condition,seconds,correct&limit=10000", SB_SERVICE);
  const rows = res.ok ? ((await res.json()) as { participant: string; condition: Condition; seconds: number;
    correct: boolean }[]) : [];
  const byCond = CONDITIONS.flatMap((condition) => {
    const r = rows.filter((x) => x.condition === condition);
    return r.length ? [{
      condition, trials: r.length, medianS: median(r.map((x) => x.seconds)), correct: r.filter((x) => x.correct).length,
      under: r.filter((x) => x.seconds < UNDER_S).length,
      correctUnder: r.filter((x) => x.correct && x.seconds < UNDER_S).length,
    }] : [];
  });
  return { participants: new Set(rows.map((x) => x.participant)).size, byCond };
}
