import { NextResponse } from "next/server";
import { saveTrial } from "@/lib/study";

// POST /api/study {participant, call_id, condition, seconds, answer_clock, answer_quote?}
// -> 201 {ok: true}. Scored server-side against the gold minute, rate-limited per IP.
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  if (!body) return NextResponse.json({ error: "Body must be JSON." }, { status: 400 });
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0].trim() ?? "unknown";
  const r = await saveTrial(body, ip);
  return r.ok ? NextResponse.json({ ok: true }, { status: 201 })
    : NextResponse.json({ error: r.error }, { status: r.status });
}
