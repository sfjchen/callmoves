// Server-only data access, the 1 file that knows where calls live. With SUPABASE_URL and SUPABASE_ANON_KEY set
// (production) it reads the callmoves_calls table through Supabase's REST API, published by
// `python -m callmoves.publish`. Without them (local dev, CI) it reads the pipeline's JSON files in web/data/.
import { promises as fs } from "fs";
import path from "path";
import type { CallOutput, CallSummary } from "./types";

const DATA_DIR = path.join(process.cwd(), "data");
const SB_URL = process.env.SUPABASE_URL;
const SB_KEY = process.env.SUPABASE_ANON_KEY;
const ID_RE = /^[A-Za-z0-9_.-]+$/;
const REVALIDATE_S = 60;

/**
 * Fetches rows of callmoves_calls with the anon key, which row-level security limits to reads. Cached for
 * REVALIDATE_S seconds so a newly published call shows up within a minute.
 */
async function sbRows<T>(query: string): Promise<T[]> {
  const res = await fetch(`${SB_URL}/rest/v1/callmoves_calls?${query}`, {
    headers: { apikey: SB_KEY!, Authorization: `Bearer ${SB_KEY}` },
    next: { revalidate: REVALIDATE_S },
  });
  if (!res.ok) throw new Error(`Supabase ${res.status} on callmoves_calls`);
  return (await res.json()) as T[];
}

/**
 * Returns 1 processed call by id, or null for an unknown or malformed id.
 */
export async function getCall(id: string): Promise<CallOutput | null> {
  if (!ID_RE.test(id)) return null;
  if (SB_URL && SB_KEY) {
    const rows = await sbRows<{ data: CallOutput }>(`select=data&id=eq.${encodeURIComponent(id)}`);
    return rows[0]?.data ?? null;
  }
  try {
    return JSON.parse(await fs.readFile(path.join(DATA_DIR, `${id}.json`), "utf8")) as CallOutput;
  } catch {
    return null;
  }
}

/**
 * Builds the library row for a call read from a local file. Published calls carry the same summary, computed by
 * callmoves.publish.summarize, so the 2 must stay in step.
 */
function summarize(id: string, call: CallOutput): CallSummary {
  const top = call.moments[0];
  const t0 = new Date(call.call.call_start).getTime(), t1 = new Date(call.call_end).getTime();
  const cum = call.bars.filter((b) => { const t = new Date(b.ts).getTime(); return t >= t0 && t <= t1; })
    .map((b) => b.cum_adj_ret);
  const step = Math.max(1, Math.ceil(cum.length / 60));
  return {
    id,
    ticker: call.call.ticker,
    quarter: call.call.quarter,
    company: call.call.company,
    call_start: call.call.call_start,
    n_moments: call.moments.length,
    n_flagged: call.bars.filter((b) => b.flagged).length,
    spark: cum.filter((_, i) => i % step === 0 || i === cum.length - 1),
    top_moment: top ? { ts: top.ts, quote: top.quote, adj_ret_5m: top.adj_ret_5m, speaker: top.speaker } : null,
  };
}

/**
 * Lists every call for the library, newest first.
 */
export async function listCalls(): Promise<CallSummary[]> {
  if (SB_URL && SB_KEY) {
    return (await sbRows<{ summary: CallSummary }>("select=summary&order=call_start.desc")).map((r) => r.summary);
  }
  let files: string[] = [];
  try {
    files = (await fs.readdir(DATA_DIR)).filter((f) => f.endsWith(".json"));
  } catch {
    return [];
  }
  const calls = await Promise.all(files.map(async (f) => ({ id: f.slice(0, -5), call: await getCall(f.slice(0, -5)) })));
  return calls
    .filter((c): c is { id: string; call: CallOutput } => c.call !== null)
    .map(({ id, call }) => summarize(id, call))
    .sort((a, b) => b.call_start.localeCompare(a.call_start));
}
