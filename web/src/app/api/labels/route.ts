// POST /api/labels: stores 1 blind label from the hidden /label page as rater "user", replacing an earlier answer
// for the same flag.
import { NextResponse } from "next/server";
import { CHOICES, FLAGS, keyOk, RATER } from "@/lib/labels";
import { withPost } from "@/lib/request";
import { SB_SERVICE, sb } from "@/lib/supabase";

/**
 * Validates the key, flag, choice, sentence number and confidence, then upserts the label on (flag_id, rater).
 */
export async function POST(req: Request) {
  return withPost(req, async (b) => {
    if (!keyOk(b.k)) return NextResponse.json({ error: "Not found." }, { status: 404 });
    const flag = FLAGS.find((f) => f.id === b.flag_id);
    const choice = CHOICES.find((c) => c === b.choice);
    const conf = Number(b.confidence);
    const sent = choice === "statement" ? flag?.sentences.find((s) => s.n === Number(b.n)) : undefined;
    if (!flag || !choice || ![1, 2, 3].includes(conf) || (choice === "statement" && !sent)) {
      return NextResponse.json({ error: "Bad label." }, { status: 400 });
    }
    const res = await sb("callmoves_labels?on_conflict=flag_id,rater", SB_SERVICE!, {
      method: "POST",
      body: JSON.stringify({
        flag_id: flag.id, call_id: flag.call_id, flag_ts: flag.start, rater: RATER, choice, confidence: conf,
        seg_id: sent?.seg_id ?? null, t_ms: sent?.t_ms ?? null,
      }),
    }, "resolution=merge-duplicates");
    if (!res.ok) return NextResponse.json({ error: "Could not save." }, { status: 502 });
    return NextResponse.json({ ok: true });
  });
}
