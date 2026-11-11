// Guess the move: 1 call's price path and transcript cut just before its top-ranked moment, then the reveal. Per-turn
// returns and flagged minutes are left out, the answer itself is in the props (as in /api/calls/[id]/moments).
import Link from "next/link";
import { getCall, listMoments } from "@/lib/data";
import { WINDOW_MS } from "@/lib/format";
import Guess, { type GuessData } from "@/components/guess";

export const dynamic = "force-dynamic";

const LEAD_MS = 20 * 60_000; // minutes of price shown before the statement
const TAIL_MS = 2 * 60_000;  // minutes shown after the 5-minute window once revealed

/**
 * Renders the game for ?call=<id>, or a random call, on its highest-ranked moment whose move began after the
 * statement (a pre_move one would show the answer on the chart). Calls read at the next open are left out, their
 * moments all share 1 overnight move.
 */
export default async function GuessPage({ searchParams }: PageProps<"/guess">) {
  const sp = await searchParams;
  const pool = (await listMoments()).filter((c) => !c.atOpen && c.moments.some((m) => !m.pre_move))
    .map((c) => c.summary.id);
  const asked = typeof sp.call === "string" && pool.includes(sp.call) ? sp.call : null;
  // eslint-disable-next-line react-hooks/purity -- the page renders per request, a new random call each time
  const call = pool.length ? await getCall(asked ?? pool[Math.floor(Math.random() * pool.length)]) : null;
  const m = call?.moments.find((x) => !x.pre_move);
  if (!call || !m) {
    return (
      <p className="mx-auto max-w-3xl text-nb-muted">
        No call with a ranked moment yet. <Link href="/" className="text-nb-accent underline">Back to the library</Link>
      </p>
    );
  }
  const stmt = new Date(m.ts).getTime();
  const seg = call.segments.find((s) => s.seg_id === m.seg_id);
  const cut = seg ? seg.text.indexOf(m.quote) : -1;
  const data: GuessData = {
    id: call.summary.id, ticker: call.call.ticker, quarter: call.call.quarter, company: call.call.company,
    etf: call.call.etf, callStart: call.call.call_start, audioUrl: call.call.audio_url, moment: m, pool,
    bars: call.bars
      .map((b) => ({ t: new Date(b.ts).getTime(), cum: b.cum_adj_ret }))
      .filter((b) => b.t >= stmt - LEAD_MS && b.t <= stmt + WINDOW_MS + TAIL_MS)
      .map((b) => ({ min: (b.t - stmt) / 60_000, cum: b.cum, before: b.t + 60_000 <= stmt })),
    turns: call.segments
      .filter((s) => s.t0_ms < m.t0_ms && (s.seg_id !== m.seg_id || cut > 0))
      .map((s) => ({ speaker: s.speaker, text: s.seg_id === m.seg_id ? s.text.slice(0, cut).trimEnd() : s.text })),
  };
  return <Guess key={data.id} data={data} />;
}
