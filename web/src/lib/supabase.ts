// Server-only Supabase REST helper shared by data.ts (anon key, reads) and submit.ts and study.ts (service key,
// writes).
export const SB_URL = process.env.SUPABASE_URL;
export const SB_SERVICE = process.env.SUPABASE_SERVICE_KEY;

/**
 * Calls Supabase's REST API with the caller's key. Uncached unless `init.next` sets a revalidation time. `prefer`
 * asks for a row count or the inserted row back.
 */
export async function sb(pathQuery: string, key: string, init: RequestInit = {}, prefer?: string): Promise<Response> {
  return fetch(`${SB_URL}/rest/v1/${pathQuery}`, {
    ...(init.next ? {} : { cache: "no-store" as const }),
    ...init,
    headers: {
      apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json",
      ...(prefer ? { Prefer: prefer } : {}),
    },
  });
}
