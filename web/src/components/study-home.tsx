// Client part of /study: reads or creates this browser's participant id and 2 trials, then links to the next open
// trial. The study bar on the replay page starts the timer when it loads.
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { loadState, type StudyState } from "@/lib/study-state";

/**
 * Renders the participant id, the trial list, a Start button for the first trial not yet done, and a reset that
 * hands the browser to the next participant.
 */
export default function StudyHome({ ids }: { ids: string[] }) {
  const [st, setSt] = useState<StudyState | null>(null);
  // localStorage exists only in the browser, so the state loads after hydration
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setSt(loadState(ids)), [ids]);
  if (!st) return <p className="text-neutral-500">Loading.</p>;
  const next = st.trials.find((t) => !t.done);
  return (
    <div>
      <p className="text-xs text-neutral-500 mb-3 tabular">Participant {st.pid}</p>
      <ol className="mb-4 space-y-1">
        {st.trials.map((t, i) => (
          <li key={t.call_id} className={t.done ? "text-neutral-400" : ""}>Trial {i + 1} of 2{t.done && ", done"}</li>
        ))}
      </ol>
      {next ? (
        <Link
          href={`/calls/${next.call_id}?study=${next.condition}&p=${st.pid}`}
          className="inline-block rounded bg-blue-700 px-4 py-1.5 text-white"
        >
          Start trial {st.trials.indexOf(next) + 1}
        </Link>
      ) : (
        <p className="rounded bg-emerald-50 px-3 py-2 text-emerald-800">Both trials are done. Thank you.</p>
      )}
      <button type="button" onClick={() => setSt(loadState(ids, true))}
        className="block mt-4 text-xs text-neutral-500 underline underline-offset-2 hover:text-neutral-700">
        Next participant on this device</button>
    </div>
  );
}
