import Link from "next/link";
import { listCalls } from "@/lib/data";
import { clock, dir, DIR_HEX, DIR_TEXT, pct, shortSpeaker } from "@/lib/format";

export const dynamic = "force-dynamic";

// Word-sized trend of the sector-adjusted return over the call, last point marked and colored by its sign.
function Sparkline({ pts }: { pts: number[] }) {
  if (pts.length < 2) return <span className="text-neutral-400">—</span>;
  const W = 100, H = 24, PAD = 2;
  const lo = Math.min(0, ...pts), hi = Math.max(0, ...pts), span = hi - lo || 1;
  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / (pts.length - 1);
  const y = (v: number) => H - PAD - ((v - lo) * (H - 2 * PAD)) / span;
  const last = pts[pts.length - 1];
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="inline-block align-middle" aria-label={`adjusted return over the call, ends ${pct(last)}`}>
      <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} stroke="#e5e5e5" strokeDasharray="2 2" />
      <polyline points={pts.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")} fill="none" stroke="#737373" strokeWidth={1.25} />
      <circle cx={x(pts.length - 1)} cy={y(last)} r={2.5} fill={DIR_HEX[dir(last)]} />
    </svg>
  );
}

export default async function Library({ searchParams }: PageProps<"/">) {
  const q = String((await searchParams).q ?? "").trim();
  const needle = q.toLowerCase();
  const all = await listCalls();
  const calls = all.filter((c) => [c.ticker, c.company ?? "", c.quarter].some((f) => f.toLowerCase().includes(needle)));
  return (
    <div className="max-w-6xl">
      <h1 className="text-xl font-semibold mb-1">Call library</h1>
      <p className="text-sm text-neutral-500 mb-3">
        Processed calls, newest first. Each row shows the top-ranked moment, click the ticker to replay the call.
      </p>
      <form className="mb-3 text-sm">
        <input
          type="search" name="q" defaultValue={q} placeholder="Filter by ticker, company or quarter, Enter to apply"
          className="w-full max-w-sm rounded border border-neutral-300 px-2 py-1 placeholder:text-neutral-400 focus:outline-none focus:ring-1 focus:ring-blue-300"
          aria-label="Filter calls"
        />
      </form>
      {calls.length === 0 ? (
        <p className="text-neutral-500">{all.length === 0 ? "No processed calls yet." : `No call matches "${q}".`}</p>
      ) : (
        <div className="overflow-x-auto"><table className="w-full min-w-[56rem] text-sm border-collapse">
          <thead>
            <tr className="text-left text-neutral-500 border-b border-neutral-200">
              <th className="py-2 pr-4 font-medium">Ticker</th>
              <th className="py-2 pr-4 font-medium">Quarter</th>
              <th className="py-2 pr-4 font-medium">Call</th>
              <th className="py-2 pr-4 font-medium">Adj. return</th>
              <th className="py-2 pr-4 font-medium text-right">Abnormal min</th>
              <th className="py-2 pr-4 font-medium">Top moment</th>
              <th className="py-2 pr-4 font-medium text-right">5-min adj. return</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((c) => (
              <tr key={c.id} className="border-b border-neutral-100 hover:bg-neutral-50">
                <td className="py-2 pr-4 font-semibold">
                  <Link href={`/calls/${c.id}`} className="underline underline-offset-2">{c.ticker}</Link>
                  {c.company && <div className="text-xs font-normal text-neutral-500 whitespace-nowrap">{c.company}</div>}
                </td>
                <td className="py-2 pr-4">{c.quarter}</td>
                <td className="py-2 pr-4 tabular whitespace-nowrap">
                  {new Date(c.call_start).toLocaleDateString("en-US", { timeZone: "America/New_York" })} {clock(c.call_start)} ET
                </td>
                <td className="py-2 pr-4 whitespace-nowrap">
                  <Sparkline pts={c.spark} />
                  {c.spark.length > 0 && <span className={`ml-1.5 text-xs tabular ${DIR_TEXT[dir(c.spark[c.spark.length - 1])]}`}>{pct(c.spark[c.spark.length - 1])}</span>}
                </td>
                <td className="py-2 pr-4 text-right tabular">{c.n_flagged}</td>
                <td className="py-2 pr-4 text-neutral-700">
                  {c.top_moment ? (
                    <>
                      <span className="text-neutral-500 tabular">{clock(c.top_moment.ts)}</span>{" "}
                      <span className="font-medium">{shortSpeaker(c.top_moment.speaker)}:</span> {c.top_moment.quote}
                    </>
                  ) : "none"}
                </td>
                <td className={`py-2 pr-4 text-right tabular font-medium ${c.top_moment ? DIR_TEXT[dir(c.top_moment.adj_ret_5m)] : ""}`}>
                  {c.top_moment ? pct(c.top_moment.adj_ret_5m) : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </div>
  );
}
