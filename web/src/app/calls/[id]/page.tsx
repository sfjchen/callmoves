// Replay page for 1 call, with the study bar and the stripped baseline view when opened from /study.
import { notFound } from "next/navigation";
import { getCall } from "@/lib/data";
import Replay from "@/components/replay";
import StudyBar from "@/components/study-bar";

export const dynamic = "force-dynamic";

/**
 * Renders the replay view for 1 call, or the 404 page for an unknown id. With ?study=callmoves|baseline&p=<id> it
 * adds the study bar, and the baseline condition gets the call stripped of everything CallMoves computed (moments,
 * flagged minutes, per-turn returns) so none of it reaches the browser. Outside the study, ?t=<seconds since audio
 * start> opens the replay at that point, as the /guess and /themes links do (rounded up, so the cursor lands in
 * the moment's turn).
 */
export default async function CallPage({ params, searchParams }: PageProps<"/calls/[id]">) {
  const { id } = await params;
  const sp = await searchParams;
  const call = await getCall(id);
  if (!call) notFound();
  const pid = typeof sp.p === "string" && /^[A-Za-z0-9-]{4,40}$/.test(sp.p) ? sp.p : null;
  const study = pid && (sp.study === "callmoves" || sp.study === "baseline") ? sp.study : null;
  const t = typeof sp.t === "string" && /^\d{1,5}$/.test(sp.t) ? Number(sp.t) * 1000 : undefined;
  if (!study) return <Replay data={call} startMs={t} />;
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
