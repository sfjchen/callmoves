import { NextResponse } from "next/server";
import { listCalls } from "@/lib/data";

// GET /api/calls?ticker=ACME  -> library list with each call's top moment
export async function GET(req: Request) {
  const ticker = new URL(req.url).searchParams.get("ticker")?.toUpperCase();
  const calls = await listCalls();
  return NextResponse.json(ticker ? calls.filter((c) => c.ticker === ticker) : calls);
}
