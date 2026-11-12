// Status page for 1 submitted call, the redirect target after /submit queues it. The server reads the queue row with
// the service key, and the client component keeps it current through /api/submissions/{id}.
import type { Metadata } from "next";
import Link from "next/link";
import SubmitStatus from "@/components/submit-status";
import { getSubmission, type SubmissionStatus } from "@/lib/submit";
import { SB_SERVICE, SB_URL } from "@/lib/supabase";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Submission status | CallMoves" };

/**
 * Renders the status of the ?id= submission, or a notice when submissions are not configured, the id is unknown or
 * the queue cannot be read.
 */
export default async function StatusPage({ searchParams }: PageProps<"/submit/status">) {
  const { id } = await searchParams;
  let s: SubmissionStatus | null = null, err = !SB_URL || !SB_SERVICE ? "Submissions are not configured here." : "";
  if (!err) {
    try {
      s = typeof id === "string" ? await getSubmission(id) : null;
      if (!s) err = "No submission with that id.";
    } catch {
      err = "Could not read the queue. Reload in a minute.";
    }
  }
  return (
    <div className="max-w-2xl text-sm">
      <h1 className="text-xl font-semibold mb-1">{s ? `${s.ticker} ${s.quarter}` : "Submission status"}</h1>
      {s ? <SubmitStatus initial={s} /> : <p className="rounded bg-red-50 px-3 py-2 text-red-800">{err}</p>}
      <p className="mt-4"><Link href="/submit" className="text-accent underline underline-offset-2">
        All submissions</Link></p>
    </div>
  );
}
