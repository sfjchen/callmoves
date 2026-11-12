// Submit page: a server-action form that queues a call for the worker and redirects to its status page, plus the
// latest submissions and their status.
// No login: submissions are rate-limited per IP in lib/submit.ts.
import { headers } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";
import { TZ, WORKER_WAIT } from "@/lib/format";
import { ERRORS, ETFS, type ErrorCode, recentSubmissions, submitCall } from "@/lib/submit";

export const dynamic = "force-dynamic";

/**
 * Queues the form's call and redirects to its status page, or back with an error code, so a reload does not
 * resubmit.
 */
async function submit(fd: FormData) {
  "use server";
  const ip = (await headers()).get("x-forwarded-for")?.split(",")[0].trim() ?? "unknown";
  const r = await submitCall(Object.fromEntries(fd), ip);
  redirect(r.ok ? `/submit/status?id=${r.id}` : `/submit?error=${r.code}`);
}

const STATUS = {
  queued: "text-muted", running: "text-accent", done: "text-up", failed: "text-down",
};
const INPUT = "w-full rounded border border-line px-2 py-1 placeholder:text-faint " +
    "focus:outline-none focus:ring-1 focus:ring-accent/40";

/**
 * Renders the submission form, any error notice, and the latest submissions with their status.
 */
export default async function Submit({ searchParams }: PageProps<"/submit">) {
  const sp = await searchParams;
  const code = typeof sp.error === "string" && Object.hasOwn(ERRORS, sp.error) ? (sp.error as ErrorCode) : null;
  const err = code && ERRORS[code][1];
  const subs = await recentSubmissions();
  const field = (name: string, label: string, ph: string, req = true, type = "text") => (
    <label className="block">
      <span className="text-muted">{label}{req ? "" : " (optional)"}</span>
      <input name={name} type={type} required={req} placeholder={ph} className={INPUT} />
    </label>
  );
  return (
    <div className="max-w-3xl text-sm">
      <h1 className="text-xl font-semibold mb-1">Submit a call</h1>
      <p className="text-muted mb-3">
        The worker transcribes the audio, pulls 1-minute prices and ranks the moments. {WORKER_WAIT} At most 3
        submissions an hour per visitor.
      </p>
      {err && <p className="mb-3 rounded bg-down/5 px-3 py-2 text-down">{err}</p>}
      <form action={submit} className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-6">
        {field("ticker", "Ticker", "WFC")}
        {field("quarter", "Quarter", "Q3-2026")}
        {field("company", "Company", "Wells Fargo", false)}
        <label className="block">
          <span className="text-muted">Sector ETF</span>
          <select name="etf" required className={INPUT}>{ETFS.map((e) => <option key={e}>{e}</option>)}</select>
        </label>
        {field("call_start", "Call start (US Eastern)", "", true, "datetime-local")}
        {field("audio_url", "Audio link (mp3, m4a or mpeg)", "https://...", true, "url")}
        {field("release_url", "Earnings release (PDF or HTML)", "https://...", true, "url")}
        {field("prior_url", "Prior-quarter release", "https://...", false, "url")}
        <button
          className="sm:col-span-2 justify-self-start rounded bg-brand px-3 py-1.5 text-white hover:bg-brand-dark">
          Queue call</button>
      </form>
      <h2 className="font-semibold mb-1">Recent submissions</h2>
      {subs.length === 0 ? <p className="text-muted">None yet.</p> : (
        <table className="w-full border-collapse">
          <tbody>
            {subs.map((s) => (
              <tr key={s.id} className="border-b border-rule">
                <td className="py-1.5 pr-4 font-semibold"><Link href={`/submit/status?id=${s.id}`}
                  className="hover:underline underline-offset-2">{s.ticker}</Link></td>
                <td className="py-1.5 pr-4">{s.quarter}</td>
                <td className="py-1.5 pr-4 text-muted">{new Date(s.created_at).toLocaleString("en-US",
                  { timeZone: TZ, month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} ET</td>
                <td className={`py-1.5 pr-4 ${STATUS[s.status]}`}>{s.status}</td>
                <td className="py-1.5">{s.call_id && s.status === "done" ? <Link className="text-accent underline"
                  href={`/calls/${s.call_id}`}>replay</Link> :
                  <span className="text-down">{s.error ?? ""}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
