// Library list and call submission endpoints.
import { NextResponse } from "next/server";
import { listCalls } from "@/lib/data";
import { withPost } from "@/lib/request";
import { ERRORS, submitCall } from "@/lib/submit";

// GET /api/calls?ticker=WFC  -> library list with each call's top moment
export async function GET(req: Request) {
  const ticker = new URL(req.url).searchParams.get("ticker")?.toUpperCase();
  const calls = await listCalls();
  return NextResponse.json(ticker ? calls.filter((c) => c.ticker === ticker) : calls);
}

// POST /api/calls {ticker, quarter, etf, call_start (ISO with offset), audio_url, release_url, prior_url?, company?}
// -> 202 {id, status: "queued", status_url}, rate-limited per IP. The worker picks it up within about 15 minutes, and
// status_url (a page) or GET /api/submissions/{id} (JSON) shows its progress.
export const POST = (req: Request) => withPost(req, async (body, ip) => {
  const r = await submitCall(body, ip);
  return r.ok ? NextResponse.json({ id: r.id, status: "queued", status_url: `/submit/status?id=${r.id}` },
      { status: 202 })
    : NextResponse.json({ error: ERRORS[r.code][1] }, { status: ERRORS[r.code][0] });
});
