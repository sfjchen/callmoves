import { NextResponse } from "next/server";
import { listCalls } from "@/lib/data";
import { submitCall, type SubmissionInput } from "@/lib/submit";

// GET /api/calls?ticker=ACME  -> library list with each call's top moment
export async function GET(req: Request) {
  const ticker = new URL(req.url).searchParams.get("ticker")?.toUpperCase();
  const calls = await listCalls();
  return NextResponse.json(ticker ? calls.filter((c) => c.ticker === ticker) : calls);
}

// POST /api/calls {ticker, quarter, etf, call_start (ISO with offset), audio_url, release_url, prior_url?, company?}
// -> 202 {id, status: "queued"}, rate-limited per IP. The worker picks it up within about 15 minutes.
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as Partial<SubmissionInput> | null;
  if (!body) return NextResponse.json({ error: "Body must be JSON." }, { status: 400 });
  const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0].trim() ?? "unknown";
  const r = await submitCall({
    ticker: str(body.ticker).toUpperCase(), quarter: str(body.quarter).toUpperCase(), company: str(body.company),
    etf: str(body.etf).toUpperCase(), call_start: str(body.call_start), audio_url: str(body.audio_url),
    release_url: str(body.release_url), prior_url: str(body.prior_url),
  }, ip);
  return r.ok ? NextResponse.json({ id: r.id, status: "queued" }, { status: 202 })
    : NextResponse.json({ error: r.error }, { status: r.status });
}
