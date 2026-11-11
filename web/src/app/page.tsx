// Library page: every processed call newest first with its sparkline and top moment, filterable by ticker.
import Link from "next/link";
import { listCalls } from "@/lib/data";
import { dir, DIR_HEX, DIR_TEXT, pct, TZ } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * Renders a word-sized trend of the sector-adjusted return over the call, last point marked and colored by its sign.
 */
function Sparkline({ pts }: { pts: number[] }) {
  if (pts.length < 2) return <span className="text-neutral-400">—</span>;
  const W = 100, H = 24, PAD = 2;
  const lo = Math.min(0, ...pts), hi = Math.max(0, ...pts), span = hi - lo || 1;
  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / (pts.length - 1);
  const y = (v: number) => H - PAD - ((v - lo) * (H - 2 * PAD)) / span;
  const last = pts[pts.length - 1];
  return (
    <svg
      width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="inline-block align-middle"
      aria-label={`adjusted return over the call, ends ${pct(last)}`}
    >
      <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} stroke="#e5e5e5" strokeDasharray="2 2" />
      <polyline
        points={pts.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}
        fill="none" stroke="#737373" strokeWidth={1.25}
      />
      <circle cx={x(pts.length - 1)} cy={y(last)} r={2.5} fill={DIR_HEX[dir(last)]} />
    </svg>
  );
}

/**
 * Renders the library: a filter box and 1 row per call, newest first.
 */
export default async function Library({ searchParams }: PageProps<"/">) {
  const q = String((await searchParams).q ?? "").trim();
  const needle = q.toLowerCase();
  const all = await listCalls();
  const calls = all.filter((c) => [c.ticker, c.company ?? "", c.quarter].some((f) => f.toLowerCase().includes(needle)));
  return (
    <div className="max-w-6xl">
      <h1 className="text-xl font-semibold mb-1">Call library</h1>
      <form className="mb-3 text-sm">
        <input
          type="search" name="q" defaultValue={q} placeholder="Filter by ticker, company or quarter, Enter to apply"
          className={
            "w-full max-w-sm rounded border border-neutral-300 px-2 py-1 placeholder:text-neutral-400 " +
            "focus:outline-none focus:ring-1 focus:ring-blue-300"
          }
          aria-label="Filter calls"
        />
      </form>
      {calls.length === 0 ? (
        <p className="text-neutral-500">{all.length === 0 ? "No processed calls yet." : `No call matches "${q}".`}</p>
      ) : (
        <div className="overflow-x-auto"><table className="w-full min-w-[44rem] text-sm border-collapse">
          <thead>
            <tr className="text-left text-neutral-500 border-b border-neutral-200">
              <th className="py-2 pr-4 font-medium">Call</th>
              <th className="py-2 pr-4 font-medium">Quarter</th>
              <th className="py-2 pr-4 font-medium">Date</th>
              <th className="py-2 pr-4 font-medium">Adj. return</th>
              <th className="py-2 font-medium">Top moment</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((c) => {
              const end = c.spark[c.spark.length - 1], top = c.top_moment;
              return (
                <tr key={c.id} className="border-b border-neutral-100 hover:bg-neutral-50">
                  <td className="py-2 pr-4 font-semibold">
                    <Link href={`/calls/${c.id}`} className="underline underline-offset-2">{c.ticker}</Link>
                    {c.company && <div className="text-xs font-normal text-neutral-500 whitespace-nowrap">
                      {c.company}</div>}
                  </td>
                  <td className="py-2 pr-4">{c.quarter}</td>
                  <td className="py-2 pr-4 tabular whitespace-nowrap">
                    {new Date(c.call_start).toLocaleDateString("en-US", { timeZone: TZ })}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">
                    <Sparkline pts={c.spark} />
                    {end !== undefined && (
                      <span className={`ml-1.5 text-xs tabular ${DIR_TEXT[dir(end)]}`}>{pct(end)}</span>
                    )}
                  </td>
                  <td className="py-2 max-w-0 w-full text-neutral-700">
                    {top ? (
                      <div className="flex gap-2">
                        <span className="truncate">{top.quote}</span>
                        <span className={`shrink-0 tabular font-medium ${DIR_TEXT[dir(top.adj_ret_5m)]}`}>
                          {pct(top.adj_ret_5m)}</span>
                      </div>
                    ) : "none"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table></div>
      )}
    </div>
  );
}
