// Submission status endpoint, polled by /submit/status. Reads the queue row with the service key on the server and
// returns only the fields /submit already lists publicly: no submitted links, no IP hash. `error` is the worker's
// message, which can name a link that failed to download.
import { NextResponse } from "next/server";
import { getSubmission } from "@/lib/submit";
import { SB_SERVICE, SB_URL } from "@/lib/supabase";

// GET /api/submissions/{id} -> {id, ticker, quarter, status, call_id, error, created_at, started_at, ahead}
export async function GET(_req: Request, { params }: RouteContext<"/api/submissions/[id]">) {
  if (!SB_URL || !SB_SERVICE) {
    return NextResponse.json({ error: "Submissions are not configured here." }, { status: 503 });
  }
  const { id } = await params;
  try {
    const s = await getSubmission(id);
    return s ? NextResponse.json(s, { headers: { "Cache-Control": "no-store" } })
      : NextResponse.json({ error: "not found" }, { status: 404 });
  } catch {
    return NextResponse.json({ error: "Could not read the queue." }, { status: 502 });
  }
}
