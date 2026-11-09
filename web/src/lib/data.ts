// Server-only data access. The prototype reads the pipeline's JSON files from web/data/.
// Swapping this module for Supabase queries is the next step; nothing else needs to change.
import { promises as fs } from "fs";
import path from "path";
import type { CallOutput, CallSummary } from "./types";

const DATA_DIR = path.join(process.cwd(), "data");

export async function listCallIds(): Promise<string[]> {
  try {
    const files = await fs.readdir(DATA_DIR);
    return files.filter((f) => f.endsWith(".json")).map((f) => f.slice(0, -5)).sort();
  } catch {
    return [];
  }
}

export async function getCall(id: string): Promise<CallOutput | null> {
  if (!/^[A-Za-z0-9_.-]+$/.test(id)) return null;
  try {
    const raw = await fs.readFile(path.join(DATA_DIR, `${id}.json`), "utf8");
    return JSON.parse(raw) as CallOutput;
  } catch {
    return null;
  }
}

export async function listCalls(): Promise<CallSummary[]> {
  const ids = await listCallIds();
  const calls = await Promise.all(ids.map(async (id) => ({ id, call: await getCall(id) })));
  return calls
    .filter((c): c is { id: string; call: CallOutput } => c.call !== null)
    .map(({ id, call }) => {
      const top = call.moments[0];
      const t0 = new Date(call.call.call_start).getTime(), t1 = new Date(call.call_end).getTime();
      const cum = call.bars.filter((b) => { const t = new Date(b.ts).getTime(); return t >= t0 && t <= t1; }).map((b) => b.cum_adj_ret);
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
    })
    .sort((a, b) => b.call_start.localeCompare(a.call_start));
}
