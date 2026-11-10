// Submit page: a server-action form that queues a call for the worker, plus the latest submissions and their status.
// No login: submissions are rate-limited per IP in lib/submit.ts.
import { headers } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";
import { TZ } from "@/lib/format";
import { ERRORS, ETFS, type ErrorCode, recentSubmissions, submitCall } from "@/lib/submit";

export const dynamic = "force-dynamic";

/**
 * Queues the form's call and redirects back with the new id or an error code, so a reload does not resubmit.
 */
async function submit(fd: FormData) {
  "use server";
  const ip = (await headers()).get("x-forwarded-for")?.split(",")[0].trim() ?? "unknown";
  const r = await submitCall(Object.fromEntries(fd), ip);
  redirect(r.ok ? `/submit?queued=${r.id}` : `/submit?error=${r.code}`);
}

const STATUS = {
  queued: "text-neutral-600", running: "text-blue-700", done: "text-emerald-700", failed: "text-red-700",
};
const INPUT = "w-full rounded border border-neutral-300 px-2 py-1 placeholder:text-neutral-400 " +
    "focus:outline-none focus:ring-1 focus:ring-blue-300";

/**
 * Renders the submission form, any queued or error notice, and the latest submissions with their status.
 */
export default async function Submit({ searchParams }: PageProps<"/submit">) {
  const sp = await searchParams;
  const code = typeof sp.error === "string" && Object.hasOwn(ERRORS, sp.error) ? (sp.error as ErrorCode) : null;
  const err = code && ERRORS[code][1];
  const subs = await recentSubmissions();
  const field = (name: string, label: string, ph: string, req = true, type = "text") => (
    <label className="block">
      <span className="text-neutral-600">{label}{req ? "" : " (optional)"}</span>
      <input name={name} type={type} required={req} placeholder={ph} className={INPUT} />
    </label>
  );
  return (
    <div className="max-w-3xl text-sm">
      <h1 className="text-xl font-semibold mb-1">Submit a call</h1>
      <p className="text-neutral-500 mb-3">
        The worker transcribes the audio, pulls 1-minute prices and ranks the moments. Expect about 1 to 2 hours
        per call. At most 3 submissions an hour per visitor.
      </p>
      {sp.queued && <p className="mb-3 rounded bg-emerald-50 px-3 py-2 text-emerald-800">Queued. It shows below and
        moves to the library when done.</p>}
      {err && <p className="mb-3 rounded bg-red-50 px-3 py-2 text-red-800">{err}</p>}
      <form action={submit} className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-6">
        {field("ticker", "Ticker", "WFC")}
        {field("quarter", "Quarter", "Q3-2026")}
        {field("company", "Company", "Wells Fargo", false)}
        <label className="block">
          <span className="text-neutral-600">Sector ETF</span>
          <select name="etf" required className={INPUT}>{ETFS.map((e) => <option key={e}>{e}</option>)}</select>
        </label>
        {field("call_start", "Call start (US Eastern)", "", true, "datetime-local")}
        {field("audio_url", "Audio link (mp3, m4a or mpeg)", "https://...", true, "url")}
        {field("release_url", "Earnings release (PDF or HTML)", "https://...", true, "url")}
        {field("prior_url", "Prior-quarter release", "https://...", false, "url")}
        <button className="sm:col-span-2 justify-self-start rounded bg-neutral-900 px-3 py-1.5 text-white">
          Queue call</button>
      </form>
      <h2 className="font-semibold mb-1">Recent submissions</h2>
      {subs.length === 0 ? <p className="text-neutral-500">None yet.</p> : (
        <table className="w-full border-collapse">
          <tbody>
            {subs.map((s) => (
              <tr key={s.id} className="border-b border-neutral-100">
                <td className="py-1.5 pr-4 font-semibold">{s.ticker}</td>
                <td className="py-1.5 pr-4">{s.quarter}</td>
                <td className="py-1.5 pr-4 text-neutral-500">{new Date(s.created_at).toLocaleString("en-US",
                  { timeZone: TZ, month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} ET</td>
                <td className={`py-1.5 pr-4 ${STATUS[s.status]}`}>{s.status}</td>
                <td className="py-1.5">{s.call_id && s.status === "done" ? <Link className="text-blue-700 underline"
                  href={`/calls/${s.call_id}`}>replay</Link> :
                  <span className="text-red-700">{s.error ?? ""}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
