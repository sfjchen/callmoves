// Server-only data access, the 1 file that knows where calls live. With SUPABASE_URL and SUPABASE_ANON_KEY set
// (production) it reads the callmoves_calls table through Supabase's REST API, published by
// `python -m callmoves.publish`. Without them (local dev, CI) it reads the pipeline's JSON files in web/data/.
import { promises as fs } from "fs";
import path from "path";
import { SB_URL, sb } from "./supabase";
import type { CallOutput, CallSummary } from "./types";

const DATA_DIR = path.join(process.cwd(), "data");
const SB_KEY = process.env.SUPABASE_ANON_KEY;
const ID_RE = /^[A-Za-z0-9_.-]+$/;
const REVALIDATE_S = 60;

/**
 * Fetches rows of callmoves_calls with the anon key, which row-level security limits to reads. Cached for
 * REVALIDATE_S seconds so a newly published call shows up within a minute.
 */
async function sbRows<T>(query: string): Promise<T[]> {
  const res = await sb(`callmoves_calls?${query}`, SB_KEY!, { next: { revalidate: REVALIDATE_S } });
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
  const calls = await Promise.all(files.map((f) => getCall(f.slice(0, -5))));
  return calls
    .filter((c): c is CallOutput => c !== null)
    .map((c) => c.summary)
    .sort((a, b) => b.call_start.localeCompare(a.call_start));
}
