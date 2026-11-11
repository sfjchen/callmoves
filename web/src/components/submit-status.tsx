// Status of 1 submitted call (client component): queued, processing, done or failed, refreshed from
// /api/submissions/{id} every 15 s until the call is done or failed, with a link to the replay when done.
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { clock } from "@/lib/format";
import type { SubmissionStatus } from "@/lib/submit";

const POLL_MS = 15_000;
const STEPS = [["queued", "Queued"], ["running", "Processing"], ["done", "Done"]] as const;

/**
 * Renders the 3 steps with the current one marked, what it means, and the typical wait while the call is pending.
 */
export default function SubmitStatus({ initial }: { initial: SubmissionStatus }) {
  const [s, setS] = useState(initial);
  const [stale, setStale] = useState(false);  // the last refresh failed, so the shown status may be old
  const pending = s.status === "queued" || s.status === "running";
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => {
      fetch(`/api/submissions/${s.id}`, { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
        .then((next: SubmissionStatus) => { setS(next); setStale(false); }, () => setStale(true));
    }, POLL_MS);
    return () => clearInterval(t);
  }, [s.id, pending]);
  const at = STEPS.findIndex(([k]) => k === s.status);
  return (
    <div className="card p-4">
      <ol className="flex flex-wrap items-center gap-2 text-sm mb-3">
        {STEPS.map(([k, label], i) => (
          <li key={k} className="flex items-center gap-2">
            {i > 0 && <span className="text-nb-muted">→</span>}
            <span className={`rounded-full border px-2.5 py-0.5 ${i === at ? "border-nb-accent bg-white font-semibold" :
              i < at ? "border-nb-border text-nb-text" : "border-nb-border text-nb-muted"}`}>{label}</span>
          </li>
        ))}
      </ol>
      {s.status === "queued" && <p>Waiting for the worker. {s.ahead ? `${s.ahead} ${s.ahead === 1 ? "call is" :
        "calls are"} ahead of it.` : "It is next in line."}</p>}
      {s.status === "running" && <p>Processing{s.started_at && ` since ${clock(s.started_at)} ET`}: transcription,
        speaker diarization, 1-minute bars, claims and scoring.</p>}
      {s.status === "done" && (s.call_id ? <p>Done. <Link href={`/calls/${s.call_id}`}
        className="text-nb-accent underline underline-offset-2">Open the replay</Link></p> : <p>Done.</p>)}
      {s.status === "failed" && <p className="text-red-700">Failed{s.error ? `: ${s.error}` : "."}</p>}
      {pending && (
        <p className="mt-3 text-xs text-nb-muted">
          The worker checks the queue every 15 minutes and runs up to 4 calls at once. A whole job took 10.2 min at
          the median over the last 25 calls (6.6 to 13.0). This page checks every 15 s, and the link keeps working if
          you come back later.{stale && " The last check failed, retrying."}
        </p>
      )}
    </div>
  );
}
