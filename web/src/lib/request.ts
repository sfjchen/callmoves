// Server-only helpers shared by the submit and study endpoints: reading a JSON POST body with the visitor's IP,
// trimming its string fields, hashing the IP, and counting a visitor's recent rows so both tables rate-limit alike.
import { createHash } from "crypto";
import { NextResponse } from "next/server";
import { SB_SERVICE, sb } from "./supabase";

/**
 * Parses a POST's JSON body and the first x-forwarded-for address Vercel sets, answers 400 for a body that is not
 * JSON, else returns the response of `run`.
 */
export async function withPost(
  req: Request, run: (body: Record<string, unknown>, ip: string) => Promise<Response>,
): Promise<Response> {
  const body = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  if (!body) return NextResponse.json({ error: "Body must be JSON." }, { status: 400 });
  return run(body, req.headers.get("x-forwarded-for")?.split(",")[0].trim() ?? "unknown");
}

/**
 * Returns a reader of raw's string fields that trims them and gives "" for a missing or non-string field.
 */
export function field(raw: Record<string, unknown>): (k: string) => string {
  return (k) => (typeof raw[k] === "string" ? (raw[k] as string).trim() : "");
}

/**
 * Hashes a visitor's IP with the server salt, so rate limits work without storing the address.
 */
export function hashIp(ip: string): string {
  return createHash("sha256").update(`${ip}|${process.env.SUBMIT_SALT ?? ""}`).digest("hex");
}

/**
 * Counts rows of a table matching a PostgREST filter, from the Content-Range header of a HEAD request. Throws when
 * Supabase fails, so a rate limit never reads an error as 0 rows and callers fail closed.
 */
export async function count(table: string, filter: string): Promise<number> {
  const res = await sb(`${table}?select=id&${filter}`, SB_SERVICE!, { method: "HEAD" }, "count=exact");
  const total = res.headers.get("content-range")?.split("/")[1];
  if (!res.ok || total === undefined || total === "*") throw new Error(`Supabase ${res.status} counting ${table}`);
  return Number(total);
}

/**
 * Counts the rows a hashed IP added to a table in the last hour.
 */
export function ipHourCount(table: string, ipHash: string): Promise<number> {
  return count(table, `ip_hash=eq.${ipHash}&created_at=gt.${new Date(Date.now() - 3600e3).toISOString()}`);
}
