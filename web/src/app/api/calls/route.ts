import { NextResponse } from "next/server";
import { listCalls } from "@/lib/data";
import { ERRORS, submitCall } from "@/lib/submit";

// GET /api/calls?ticker=ACME  -> library list with each call's top moment
export async function GET(req: Request) {
  const ticker = new URL(req.url).searchParams.get("ticker")?.toUpperCase();
  const calls = await listCalls();
  return NextResponse.json(ticker ? calls.filter((c) => c.ticker === ticker) : calls);
}

// POST /api/calls {ticker, quarter, etf, call_start (ISO with offset), audio_url, release_url, prior_url?, company?}
// -> 202 {id, status: "queued"}, rate-limited per IP. The worker picks it up within about 15 minutes.
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  if (!body) return NextResponse.json({ error: "Body must be JSON." }, { status: 400 });
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0].trim() ?? "unknown";
  const r = await submitCall(body, ip);
  return r.ok ? NextResponse.json({ id: r.id, status: "queued" }, { status: 202 })
    : NextResponse.json({ error: ERRORS[r.code][1] }, { status: ERRORS[r.code][0] });
}
