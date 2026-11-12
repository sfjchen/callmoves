// Study results: aggregates only (participants, per condition trials, median time, accuracy), never raw rows.
import { studyStats } from "@/lib/study";

export const dynamic = "force-dynamic";

/**
 * Renders 1 table row per condition with trials, median seconds and the correct and under-2-minute shares.
 */
export default async function StudyResults() {
  const { participants, byCond } = await studyStats();
  const share = (n: number, d: number) => `${Math.round((n / d) * 100)}%`;
  return (
    <div className="max-w-3xl text-sm">
      <h1 className="text-xl font-semibold mb-1">Study results</h1>
      <p className="text-muted mb-3 tabular">{participants} participants</p>
      {byCond.length === 0 ? <p className="text-muted">No trials yet.</p> : (
        <table className="w-full text-left tabular">
          <thead className="text-muted">
            <tr><th>Condition</th><th>Trials</th><th>Median seconds</th><th>Correct</th><th>Under 2 min</th>
              <th>Correct and under 2 min</th></tr>
          </thead>
          <tbody>
            {byCond.map((c) => (
              <tr key={c.condition} className="border-t border-rule">
                <td className="py-1">{c.condition}</td><td>{c.trials}</td><td>{c.medianS.toFixed(0)}</td>
                <td>{share(c.correct, c.trials)}</td><td>{share(c.under, c.trials)}</td>
                <td>{share(c.correctUnder, c.trials)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
