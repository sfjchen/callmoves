// Server-only access to the blind labeling set: the frozen flags with their blind view (written by
// `python -m callmoves.labels flags`), the key that guards the hidden /label page, and the user's stored labels.
import flagsJson from "./flags.json";
import { SB_SERVICE, SB_URL, sb } from "./supabase";

export type LabelFlag = {
  id: string;
  call_id: string;
  start: string;
  minutes: string[];
  ticker: string;
  etf: string;
  company: string | null;
  path: { ts: string; px: number; etf_px: number; vol_mult: number }[];
  sentences: { n: number; seg_id: number; t_ms: number; ts: string; speaker: string; text: string }[];
};

export const FLAGS = flagsJson as LabelFlag[];
export const RATER = "user";
export const CHOICES = ["statement", "none", "release", "market", "other"] as const;

/**
 * Returns true when key matches LABEL_KEY. Without LABEL_KEY or Supabase the page does not exist.
 */
export function keyOk(key: unknown): boolean {
  const want = process.env.LABEL_KEY;
  return !!want && !!SB_URL && !!SB_SERVICE && key === want;
}

/**
 * Returns the flag ids the user rater has labeled. Throws when Supabase fails, so the page never re-serves a
 * labeled flag as new.
 */
export async function labeledIds(): Promise<Set<string>> {
  const res = await sb(`callmoves_labels?select=flag_id&rater=eq.${RATER}&limit=10000`, SB_SERVICE!);
  if (!res.ok) throw new Error(`Supabase ${res.status} on callmoves_labels`);
  return new Set(((await res.json()) as { flag_id: string }[]).map((r) => r.flag_id));
}

/**
 * Orders flags by a hash of their id, so the queue mixes calls and never follows CallMoves' ranking.
 */
export function queueOrder(id: string): number {
  let h = 2166136261;
  for (const c of id) h = Math.imul(h ^ c.charCodeAt(0), 16777619) >>> 0;
  return h;
}
