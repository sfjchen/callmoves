// Replay page for 1 call, with the study bar and the stripped baseline view when opened from /study. ?m=<rank> opens
// with that moment selected, and the page's Open Graph and Twitter tags preview it. ?guess=1 cuts the call at its
// top moment and asks which way the stock went next.
import { createHash } from "crypto";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getCall } from "@/lib/data";
import type { Bar, CallOutput, Segment } from "@/lib/types";
import { dir, shortQuarter } from "@/lib/format";
import { benchName, momentLine, NO_MOMENT, sharedMoment } from "@/lib/share";
import Replay from "@/components/replay";
import StudyBar from "@/components/study-bar";

export const dynamic = "force-dynamic";
const GUESS_LINE = "Hear the call up to its biggest moment, then guess which way the stock went.";
const MIN_MS = 60_000;

/**
 * Returns the call with everything derived from prices removed (moments, claims, flags, per-turn returns, the comps
 * panel, the open reaction, the sparkline, consensus figures dated after the call), keeping only the bars and turns
 * that pass the 2 filters. Every object is built field by field, so a field added to the JSON later never reaches
 * the browser by accident.
 */
function strip(call: CallOutput, keepBar: (b: Bar) => boolean, keepSeg: (s: Segment) => boolean): CallOutput {
  const { id, ticker, quarter, company, call_start, etf, themes } = call.summary;
  return {
    call: call.call, call_end: call.call_end, llm_used: call.llm_used, qa_start_ms: call.qa_start_ms,
    estimates: call.estimates?.filter((e) => e.basis !== "after_call"), moments: [], candidate_seg_ids: [],
    n_claims: 0, summary: { id, ticker, quarter, company, call_start, etf, themes, spark: [], top_moment: null },
    bars: call.bars.filter(keepBar).map((b) => ({ ts: b.ts, cum_adj_ret: b.cum_adj_ret, vol_mult: b.vol_mult,
      flagged: false })),
    segments: call.segments.filter(keepSeg).map((s) => ({ seg_id: s.seg_id, t0_ms: s.t0_ms, speaker: s.speaker,
      text: s.text, adj_ret_5m: null })),
  };
}

/**
 * Builds the title, description and preview image tags for a call link, from the ?m= moment or the top one. The
 * relative image URL resolves against metadataBase (layout.tsx), callmoves.sfjc.dev. Its v= hash of the
 * moment changes when a rerun changes it, since X, Slack and Facebook cache preview images by URL.
 */
export async function generateMetadata({ params, searchParams }: PageProps<"/calls/[id]">): Promise<Metadata> {
  const { id } = await params;
  const call = await getCall(id);
  if (!call) return {};
  const sp = await searchParams;
  const title = `${call.call.ticker} ${shortQuarter(call.call.quarter)} earnings call | CallMoves`;
  if (sp.guess === "1") return { title: `Guess the move: ${title}`, description: GUESS_LINE };
  const m = sharedMoment(call, sp.m);
  const description = m ? momentLine(call, m) : NO_MOMENT;
  const v = createHash("sha1").update(JSON.stringify(m ?? call.call)).digest("hex").slice(0, 8);
  const image = { url: `/api/calls/${id}/og?m=${m?.rank ?? 1}&v=${v}`, width: 1200, height: 630, alt: description };
  return {
    title, description,
    openGraph: { title, description, type: "website", images: [image] },
    twitter: { card: "summary_large_image", title, description, images: [image] },
  };
}

/**
 * Renders the replay view for 1 call, or the 404 page for an unknown id. With ?study=callmoves|baseline&p=<id> it
 * adds the study bar, and the baseline condition gets the stripped call. With ?guess=1 it sends only the bars that
 * closed before the top moment and the turns up to it, so the answer is not in the page, and the replay fetches the
 * full call once the reader guesses.
 */
export default async function CallPage({ params, searchParams }: PageProps<"/calls/[id]">) {
  const { id } = await params;
  const sp = await searchParams;
  const call = await getCall(id);
  if (!call) notFound();
  const top = call.moments[0];
  if (sp.guess === "1" && top && !call.open_reaction && dir(top.adj_ret_5m) !== "flat") {
    const cut = new Date(call.call.call_start).getTime() - (call.call.audio_offset_ms ?? 0) + top.t0_ms;
    const data = strip(call, (b) => new Date(b.ts).getTime() + MIN_MS <= cut, (s) => s.t0_ms <= top.t0_ms);
    return <Replay data={data} guess={{ ms: top.t0_ms, quote: top.quote, speaker: top.speaker,
      bench: benchName(call) }} />;
  }
  const pid = typeof sp.p === "string" && /^[A-Za-z0-9-]{4,40}$/.test(sp.p) ? sp.p : null;
  const study = pid && (sp.study === "callmoves" || sp.study === "baseline") ? sp.study : null;
  if (!study) return <Replay data={call} initialRank={sharedMoment(call, sp.m)?.rank} />;
  const base = study === "baseline";
  return (
    <>
      <StudyBar callId={id} pid={pid!} condition={study} />
      <div className="h-10" />
      <Replay data={base ? { ...strip(call, () => true, () => true), open_reaction: call.open_reaction,
        stats: call.stats } : call} baseline={base} />
    </>
  );
}
