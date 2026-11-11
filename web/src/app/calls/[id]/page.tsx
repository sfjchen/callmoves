// Replay page for 1 call, with the study bar and the stripped baseline view when opened from /study. ?m=<rank> opens
// with that moment selected, and the page's Open Graph and Twitter tags preview it.
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getCall } from "@/lib/data";
import { shortQuarter } from "@/lib/format";
import { momentLine, sharedMoment } from "@/lib/share";
import Replay from "@/components/replay";
import StudyBar from "@/components/study-bar";

export const dynamic = "force-dynamic";

/**
 * Builds the title, description and preview image tags for a call link, from the ?m= moment or the top one. The
 * relative image URL resolves against the Vercel production domain, or localhost in development.
 */
export async function generateMetadata({ params, searchParams }: PageProps<"/calls/[id]">): Promise<Metadata> {
  const { id } = await params;
  const call = await getCall(id);
  if (!call) return {};
  const m = sharedMoment(call, (await searchParams).m);
  const title = `${call.call.ticker} ${shortQuarter(call.call.quarter)} earnings call | CallMoves`;
  const description = m ? momentLine(call, m) : "No minute cleared the abnormal-move threshold during the call.";
  const image = { url: `/api/calls/${id}/og${m ? `?m=${m.rank}` : ""}`, width: 1200, height: 630, alt: description };
  return {
    title, description,
    openGraph: { title, description, type: "website", images: [image] },
    twitter: { card: "summary_large_image", title, description, images: [image] },
  };
}

/**
 * Renders the replay view for 1 call, or the 404 page for an unknown id. With ?study=callmoves|baseline&p=<id> it
 * adds the study bar, and the baseline condition gets the call stripped of everything CallMoves computed (moments,
 * flagged minutes, per-turn returns) so none of it reaches the browser.
 */
export default async function CallPage({ params, searchParams }: PageProps<"/calls/[id]">) {
  const { id } = await params;
  const sp = await searchParams;
  const call = await getCall(id);
  if (!call) notFound();
  const pid = typeof sp.p === "string" && /^[A-Za-z0-9-]{4,40}$/.test(sp.p) ? sp.p : null;
  const study = pid && (sp.study === "callmoves" || sp.study === "baseline") ? sp.study : null;
  if (!study) return <Replay data={call} initialRank={sharedMoment(call, sp.m)?.rank} />;
  const base = study === "baseline";
  const data = !base ? call : {
    ...call, moments: [], candidate_seg_ids: [], n_claims: 0, summary: { ...call.summary, top_moment: null },
    bars: call.bars.map((b) => ({ ...b, flagged: false })),
    segments: call.segments.map((s) => ({ ...s, adj_ret_5m: null })),
  };
  return (
    <>
      <StudyBar callId={id} pid={pid!} condition={study} />
      <div className="h-10" />
      <Replay data={data} baseline={base} />
    </>
  );
}
