import Link from "next/link";
import { listCalls } from "@/lib/data";
import { clock, pct, shortSpeaker } from "@/lib/format";

export const dynamic = "force-dynamic";


export default async function Library() {
  const calls = await listCalls();
  return (
    <div className="max-w-5xl">
      <h1 className="text-xl font-semibold mb-1">Call library</h1>
      <p className="text-sm text-neutral-500 mb-4">
        Processed calls. Each row shows the top-ranked moment. Add a call by running the pipeline into{" "}
        <code className="text-xs bg-neutral-100 px-1 rounded">web/data/</code>.
      </p>
      {calls.length === 0 ? (
        <p className="text-neutral-500">No processed calls yet.</p>
      ) : (
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-left text-neutral-500 border-b border-neutral-200">
              <th className="py-2 pr-4 font-medium">Ticker</th>
              <th className="py-2 pr-4 font-medium">Quarter</th>
              <th className="py-2 pr-4 font-medium">Call</th>
              <th className="py-2 pr-4 font-medium">Top moment</th>
              <th className="py-2 pr-4 font-medium text-right">5-min adj. return</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((c) => (
              <tr key={c.id} className="border-b border-neutral-100 hover:bg-neutral-50">
                <td className="py-2 pr-4 font-semibold">
                  <Link href={`/calls/${c.id}`} className="underline underline-offset-2">{c.ticker}</Link>
                </td>
                <td className="py-2 pr-4">{c.quarter}</td>
                <td className="py-2 pr-4 tabular">
                  {new Date(c.call_start).toLocaleDateString("en-US", { timeZone: "America/New_York" })} {clock(c.call_start)} ET
                </td>
                <td className="py-2 pr-4 text-neutral-700">
                  {c.top_moment ? (
                    <>
                      <span className="text-neutral-500 tabular">{clock(c.top_moment.ts)}</span>{" "}
                      <span className="font-medium">{shortSpeaker(c.top_moment.speaker)}:</span> {c.top_moment.quote}
                    </>
                  ) : "none"}
                </td>
                <td className={`py-2 pr-4 text-right tabular font-medium ${c.top_moment && c.top_moment.adj_ret_5m < 0 ? "text-red-700" : "text-emerald-700"}`}>
                  {c.top_moment ? pct(c.top_moment.adj_ret_5m) : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
