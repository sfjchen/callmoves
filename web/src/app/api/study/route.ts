// Study trial endpoint.
import { NextResponse } from "next/server";
import { withPost } from "@/lib/request";
import { saveTrial } from "@/lib/study";

// POST /api/study {participant, call_id, condition, seconds, answer_clock, answer_quote?}
// -> 201 {ok: true}. Scored server-side against the gold minute, rate-limited per IP.
export const POST = (req: Request) => withPost(req, async (body, ip) => {
  const r = await saveTrial(body, ip);
  return r.ok ? NextResponse.json({ ok: true }, { status: 201 })
    : NextResponse.json({ error: r.error }, { status: r.status });
});
