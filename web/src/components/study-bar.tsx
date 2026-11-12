// Fixed bar on top of a study replay page: the running timer and the answer box. Submitting stops the timer and
// POSTs the trial to /api/study, which scores it server-side.
"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { markDone } from "@/lib/study-state";
import { mmss } from "@/lib/format";

/**
 * Renders the timer, the clock-time and quote inputs and the Submit button, then a link back to /study once saved.
 */
export default function StudyBar({ callId, pid, condition }: { callId: string; pid: string; condition: string }) {
  const t0 = useRef(0);
  const [ms, setMs] = useState(0);
  const [clk, setClk] = useState("");
  const [quote, setQuote] = useState("");
  const [state, setState] = useState<"open" | "sending" | "done">("open");
  const [err, setErr] = useState("");
  // Reads the start stamped by Start, or stamps now, then ticks until the answer is sent.
  useEffect(() => {
    const k = `cm_study_t0_${pid}_${callId}`;   // so a reload does not restart the timer
    try { t0.current = Number(localStorage.getItem(k)) || 0; } catch { /* stamped below */ }
    if (!t0.current) {
      t0.current = Date.now();
      try { localStorage.setItem(k, String(t0.current)); } catch { /* the timer runs in memory */ }
    }
    if (state !== "open") return;
    const id = setInterval(() => setMs(Date.now() - t0.current), 250);
    return () => clearInterval(id);
  }, [pid, callId, state]);

  // Stops the timer and sends the trial, showing the server's message when it is refused.
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const seconds = (Date.now() - t0.current) / 1000;
    setMs(seconds * 1000);
    setState("sending");
    const res = await fetch("/api/study", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ participant: pid, call_id: callId, condition, seconds, answer_clock: clk,
        answer_quote: quote }),
    }).catch(() => null);
    if (res?.ok) { markDone(callId); setState("done"); return; }
    setErr(res ? ((await res.json().catch(() => null))?.error ?? "Could not save.") : "Could not reach the server.");
    setState("open");
  }

  return (
    <div className="fixed top-0 inset-x-0 z-50 border-b border-mark/30 bg-mark-bg px-6 py-2 text-sm">
      <form onSubmit={submit} className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-semibold tabular w-14">{mmss(ms)}</span>
        {state === "done" ? (
          <>
            <span className="text-up">Saved. Thank you.</span>
            <Link href="/study" className="text-accent underline">Back to the study</Link>
          </>
        ) : (
          <>
            <span className="text-ink">Biggest move began at (ET)</span>
            <input
              value={clk} onChange={(e) => setClk(e.target.value)} placeholder="hh:mm" required aria-label="Clock time"
              pattern="([01]?[0-9]|2[0-3]):[0-5][0-9]" className="w-20 rounded border border-line px-2 py-0.5"
            />
            <input
              value={quote} onChange={(e) => setQuote(e.target.value)} placeholder="Quote said just before it"
              aria-label="Quote" maxLength={400}
              className="min-w-48 flex-1 rounded border border-line px-2 py-0.5"
            />
            <button disabled={state === "sending"}
              className="rounded bg-brand px-3 py-0.5 text-white hover:bg-brand-dark">
              Submit
            </button>
            {err && <span className="text-down">{err}</span>}
          </>
        )}
      </form>
    </div>
  );
}
