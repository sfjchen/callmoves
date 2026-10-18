import { NextResponse } from "next/server";
import { getCall } from "@/lib/data";

// GET /api/calls/{id} -> full call: meta, segments, bars, claims, moments
export async function GET(_req: Request, { params }: RouteContext<"/api/calls/[id]">) {
  const { id } = await params;
  const call = await getCall(id);
  if (!call) return NextResponse.json({ error: "not found" }, { status: 404 });
  return NextResponse.json(call);
}
