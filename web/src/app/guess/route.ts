// Guess-the-move entry: sends the reader to a random call in guess mode, one they have not guessed while any is left.
import { NextResponse, type NextRequest } from "next/server";
import { listCalls } from "@/lib/data";
import { dir } from "@/lib/format";

/**
 * GET /guess -> redirect to /calls/<id>?guess=1 for a random call whose top moment moved (not flat) and is not in
 * the cm_guesses cookie the replay writes, or any such call once all are guessed. A call read at the next open has
 * no in-call move, so its page opens as a plain replay.
 */
export async function GET(req: NextRequest) {
  let seen: Record<string, boolean> = {};
  try { seen = JSON.parse(req.cookies.get("cm_guesses")?.value ?? "{}"); } catch { /* a malformed cookie is empty */ }
  const all = (await listCalls()).filter((c) => c.top_moment && dir(c.top_moment.adj_ret_5m) !== "flat");
  const left = all.filter((c) => !(c.id in seen));
  const pool = left.length ? left : all;
  const pick = pool[Math.floor(Math.random() * pool.length)];
  return NextResponse.redirect(new URL(pick ? `/calls/${pick.id}?guess=1` : "/", req.url));
}
