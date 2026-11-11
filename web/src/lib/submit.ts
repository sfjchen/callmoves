// Server-only submission queue: validates a call submitted from the form or POST /api/calls, rate-limits by
// hashed IP, and inserts it into callmoves_submissions for the GitHub Actions worker (pipeline/callmoves/worker.py).
// Uses the service key: the table has row-level security with no policies, so the browser never reads it directly.
import { TZ } from "./format";
import { count, field, hashIp, ipHourCount } from "./request";
import { SB_SERVICE, SB_URL, sb } from "./supabase";

const TABLE = "callmoves_submissions";
const PER_IP_HOUR = 3;     // submissions per IP per hour
const PER_DAY = 20;        // submissions per day across everyone, so a spam run cannot exhaust the worker
export const ETFS = ["XLF", "XLK", "XLY", "XLC", "XLV", "XLE", "XLI", "XLP", "XLU", "XLB", "XLRE", "SPY"];

export const ERRORS = {
  config: [503, "Submissions are not configured here."],
  ticker: [400, "Ticker must be 1 to 6 capital letters."],
  quarter: [400, "Quarter must look like Q3-2026."],
  etf: [400, "Pick a sector ETF from the list."],
  start: [400, "Call start must be a date and time, with a UTC offset when sent to the API."],
  future: [400, "The call must already have happened."],
  links: [400, "Audio and release links must be https URLs."],
  company: [400, "Company name is too long."],
  rate: [429, `At most ${PER_IP_HOUR} submissions an hour. Try again later.`],
  full: [429, "The queue is full for today. Try again tomorrow."],
  exists: [409, "That call is already in the library."],
  queued: [409, "That call is already in the queue."],
  fail: [500, "Could not queue the call."],
} as const;
export type ErrorCode = keyof typeof ERRORS;
export type SubmissionRow = {
  id: string; created_at: string; ticker: string; quarter: string; status: "queued" | "running" | "done" | "failed";
  call_id: string | null; error: string | null;
};
// 1 submission for its status page: `ahead` counts the queued submissions created before it, null once it left the
// queue.
export type SubmissionStatus = SubmissionRow & { started_at: string | null; ahead: number | null };
const PUBLIC_COLS = "id,created_at,ticker,quarter,status,call_id,error";
export type SubmitResult = { ok: true; id: string } | { ok: false; code: ErrorCode };

/**
 * Converts a datetime-local value ("2026-11-17T17:00"), read as US Eastern time, to an ISO string with offset.
 * The offset is that of the given date, so daylight saving is handled. Any other value passes through unchanged,
 * and `invalid` then requires it to carry its own offset.
 */
function easternToIso(local: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(local);
  if (!m) return local;
  const [y, mo, d, h, mi] = m.slice(1).map(Number);
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: TZ, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  }).formatToParts(guess).map((x) => [x.type, x.value]));
  const asEastern = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute);
  const offMin = (asEastern - guess) / 60000;
  const sign = offMin < 0 ? "-" : "+", abs = Math.abs(offMin);
  return `${local}:00${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
}

/**
 * Returns the code of the first problem with a trimmed submission, or null. Mirrors the CHECK constraints in
 * db/schema.sql.
 */
function invalid(s: Record<string, string>): ErrorCode | null {
  const https = (u: string) => !u || (/^https:\/\/\S+$/.test(u) && u.length <= 500);
  if (!/^[A-Z][A-Z.]{0,5}$/.test(s.ticker)) return "ticker";
  if (!/^Q[1-4]-20\d{2}$/.test(s.quarter)) return "quarter";
  if (!ETFS.includes(s.etf)) return "etf";
  // an ISO string without an offset parses as local or UTC depending on its form, hours off for a US call
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(s.call_start) || Number.isNaN(Date.parse(s.call_start))) return "start";
  if (Date.parse(s.call_start) > Date.now()) return "future";
  if (!s.audio_url || !s.release_url || !https(s.audio_url) || !https(s.release_url) || !https(s.prior_url))
    return "links";
  return s.company.length > 80 ? "company" : null;
}

/**
 * Trims and normalizes the raw form or JSON fields, validates, rate-limits and queues 1 submission. `ip` comes from
 * the x-forwarded-for header Vercel sets.
 */
export async function submitCall(raw: Record<string, unknown>, ip: string): Promise<SubmitResult> {
  if (!SB_URL || !SB_SERVICE) return { ok: false, code: "config" };
  const f = field(raw);
  const s = {
    ticker: f("ticker").toUpperCase(), quarter: f("quarter").toUpperCase(), company: f("company"),
    etf: f("etf").toUpperCase(), call_start: easternToIso(f("call_start")), audio_url: f("audio_url"),
    release_url: f("release_url"), prior_url: f("prior_url"),
  };
  const bad = invalid(s);
  if (bad) return { ok: false, code: bad };
  const ipHash = hashIp(ip);
  const dayAgo = new Date(Date.now() - 86400e3).toISOString();
  try {
    if (await ipHourCount(TABLE, ipHash) >= PER_IP_HOUR) return { ok: false, code: "rate" };
    if (await count(TABLE, `created_at=gt.${dayAgo}`) >= PER_DAY) return { ok: false, code: "full" };
    // the worker upserts audio and results by call id, so a repeat would overwrite a finished call
    if (await count("callmoves_calls", `id=eq.${s.ticker}_${s.quarter}`)) return { ok: false, code: "exists" };
  } catch {
    return { ok: false, code: "fail" };   // fail closed: no limit check, no insert
  }
  // a pending duplicate is refused by the callmoves_submissions_pending unique index, so 2 racing requests cannot
  // both queue the same call
  const res = await sb(`${TABLE}?select=id`, SB_SERVICE, {
    method: "POST",
    body: JSON.stringify({ ...s, company: s.company || null, prior_url: s.prior_url || null, ip_hash: ipHash }),
  }, "return=representation");
  if (res.status === 409) return { ok: false, code: "queued" };
  if (!res.ok) return { ok: false, code: "fail" };
  return { ok: true, id: ((await res.json()) as { id: string }[])[0].id };
}

/**
 * Lists the latest submissions with only the fields safe to show anyone: no submitted links, no IP hash.
 */
export async function recentSubmissions(limit = 20): Promise<SubmissionRow[]> {
  if (!SB_URL || !SB_SERVICE) return [];
  const res = await sb(`${TABLE}?select=${PUBLIC_COLS}&order=created_at.desc&limit=${limit}`, SB_SERVICE);
  return res.ok ? ((await res.json()) as SubmissionRow[]) : [];
}

/**
 * Returns 1 submission by id with the same public fields plus its start time and place in the queue, or null for an
 * unknown or malformed id. Throws when Supabase fails, so a status page can tell an outage from a missing row.
 */
export async function getSubmission(id: string): Promise<SubmissionStatus | null> {
  if (!SB_URL || !SB_SERVICE || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)) return null;
  const res = await sb(`${TABLE}?select=${PUBLIC_COLS},started_at&id=eq.${id}`, SB_SERVICE);
  if (!res.ok) throw new Error(`Supabase ${res.status} reading ${TABLE}`);
  const row = ((await res.json()) as Omit<SubmissionStatus, "ahead">[])[0];
  if (!row) return null;
  const ahead = row.status === "queued"
    ? await count(TABLE, `status=eq.queued&created_at=lt.${encodeURIComponent(row.created_at)}`) : null;
  return { ...row, ahead };
}
