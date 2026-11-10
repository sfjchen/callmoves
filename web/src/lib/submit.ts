// Server-only submission queue: validates a call submitted from the form or POST /api/calls, rate-limits by
// hashed IP, and inserts it into callmoves_submissions for the GitHub Actions worker (pipeline/callmoves/worker.py).
// Uses the service key: the table has row-level security with no policies, so the browser never reads it directly.
import { createHash } from "crypto";

const SB_URL = process.env.SUPABASE_URL;
const SB_SERVICE = process.env.SUPABASE_SERVICE_KEY;
const PER_IP_HOUR = 3;     // submissions per IP per hour
const PER_DAY = 20;        // submissions per day across everyone, so a spam run cannot exhaust the worker
export const ETFS = ["XLF", "XLK", "XLY", "XLC", "XLV", "XLE", "XLI", "XLP", "XLU", "XLB", "XLRE", "SPY"];

export type SubmissionInput = {
  ticker: string; quarter: string; company?: string; etf: string; call_start: string;
  audio_url: string; release_url: string; prior_url?: string;
};
export type SubmissionRow = {
  id: string; created_at: string; ticker: string; quarter: string; status: "queued" | "running" | "done" | "failed";
  call_id: string | null; error: string | null;
};
export type SubmitResult = { ok: true; id: string } | { ok: false; status: number; error: string };

/**
 * Calls Supabase's REST API with the service key. `prefer` asks for a row count or the inserted row back.
 */
async function sb(pathQuery: string, init: RequestInit = {}, prefer?: string): Promise<Response> {
  return fetch(`${SB_URL}/rest/v1/${pathQuery}`, {
    ...init,
    cache: "no-store",
    headers: {
      apikey: SB_SERVICE!, Authorization: `Bearer ${SB_SERVICE}`, "Content-Type": "application/json",
      ...(prefer ? { Prefer: prefer } : {}),
    },
  });
}

/**
 * Counts submissions matching a PostgREST filter, from the Content-Range header of a HEAD request.
 */
async function count(filter: string): Promise<number> {
  const res = await sb(`callmoves_submissions?select=id&${filter}`, { method: "HEAD" }, "count=exact");
  return Number(res.headers.get("content-range")?.split("/")[1] ?? 0);
}

/**
 * Converts a datetime-local value ("2026-11-17T17:00"), read as US Eastern time, to an ISO string with offset.
 * The offset is that of the given date, so daylight saving is handled.
 */
export function easternToIso(local: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(local);
  if (!m) return null;
  const [y, mo, d, h, mi] = m.slice(1).map(Number);
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  }).formatToParts(guess).map((x) => [x.type, x.value]));
  const asEastern = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute);
  const offMin = (asEastern - guess) / 60000;
  const sign = offMin < 0 ? "-" : "+", abs = Math.abs(offMin);
  return `${local}:00${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
}

/**
 * Returns the first problem with a submission, or null. Mirrors the CHECK constraints in db/schema.sql.
 */
function invalid(s: SubmissionInput): string | null {
  const https = (u?: string) => !u || (/^https:\/\/\S+$/.test(u) && u.length <= 500);
  if (!/^[A-Z][A-Z.]{0,5}$/.test(s.ticker)) return "Ticker must be 1 to 6 capital letters.";
  if (!/^Q[1-4]-20\d{2}$/.test(s.quarter)) return "Quarter must look like Q3-2026.";
  if (!ETFS.includes(s.etf)) return "Pick a sector ETF from the list.";
  if (Number.isNaN(Date.parse(s.call_start))) return "Call start must be a date and time.";
  if (Date.parse(s.call_start) > Date.now()) return "The call must already have happened.";
  if (!s.audio_url || !s.release_url || !https(s.audio_url) || !https(s.release_url) || !https(s.prior_url))
    return "Audio and release links must be https URLs.";
  if ((s.company ?? "").length > 80) return "Company name is too long.";
  return null;
}

/**
 * Validates, rate-limits and queues 1 submission. `ip` comes from the x-forwarded-for header Vercel sets.
 */
export async function submitCall(s: SubmissionInput, ip: string): Promise<SubmitResult> {
  if (!SB_URL || !SB_SERVICE) return { ok: false, status: 503, error: "Submissions are not configured here." };
  const bad = invalid(s);
  if (bad) return { ok: false, status: 400, error: bad };
  const ipHash = createHash("sha256").update(`${ip}|${process.env.SUBMIT_SALT ?? ""}`).digest("hex");
  const hourAgo = new Date(Date.now() - 3600e3).toISOString(), dayAgo = new Date(Date.now() - 86400e3).toISOString();
  if (await count(`ip_hash=eq.${ipHash}&created_at=gt.${hourAgo}`) >= PER_IP_HOUR)
    return { ok: false, status: 429, error: `At most ${PER_IP_HOUR} submissions an hour. Try again later.` };
  if (await count(`created_at=gt.${dayAgo}`) >= PER_DAY)
    return { ok: false, status: 429, error: "The queue is full for today. Try again tomorrow." };
  // the worker upserts audio and results by call id, so a repeat would overwrite a finished call
  const callId = `${s.ticker}_${s.quarter}`;
  const dupCall = await sb(`callmoves_calls?select=id&id=eq.${encodeURIComponent(callId)}`);
  if (dupCall.ok && ((await dupCall.json()) as unknown[]).length > 0)
    return { ok: false, status: 409, error: `${callId} is already in the library.` };
  if (await count(`ticker=eq.${s.ticker}&quarter=eq.${s.quarter}&status=in.(queued,running)`) > 0)
    return { ok: false, status: 409, error: `${callId} is already in the queue.` };
  const res = await sb("callmoves_submissions?select=id", {
    method: "POST",
    body: JSON.stringify({ ...s, company: s.company || null, prior_url: s.prior_url || null, ip_hash: ipHash }),
  }, "return=representation");
  if (!res.ok) return { ok: false, status: 500, error: "Could not queue the call." };
  return { ok: true, id: ((await res.json()) as { id: string }[])[0].id };
}

/**
 * Lists the latest submissions with only the fields safe to show anyone: no URLs, no IP hash.
 */
export async function recentSubmissions(limit = 20): Promise<SubmissionRow[]> {
  if (!SB_URL || !SB_SERVICE) return [];
  const res = await sb(`callmoves_submissions?select=id,created_at,ticker,quarter,status,call_id,error` +
    `&order=created_at.desc&limit=${limit}`);
  return res.ok ? ((await res.json()) as SubmissionRow[]) : [];
}
