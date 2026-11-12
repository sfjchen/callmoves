// Comps panel (client component): how the stock, its sector ETF, its peers and cross-asset ETFs moved over the call
// and in the 5 minutes of the selected moment, so a reader can tell a company-specific move from a sector-wide one.
"use client";

import type { Context, Moment } from "@/lib/types";
import { clock, dir, DIR_HEX, DIR_TEXT, pct, WINDOW_MS } from "@/lib/format";

const ROLE_LABEL: Record<string, string> = {
  stock: "stock", sector: "sector", peer: "peer", market: "S&P 500", rates: "long Treasuries", dollar: "dollar",
  credit: "high yield", gold: "gold", oil: "oil",
};

/**
 * Returns the return of px from its last value at or before t0 to its last value at or before t1, null when either
 * end has no price.
 */
function move(ts: number[], px: (number | null)[], t0: number, t1: number): number | null {
  let a: number | null = null, b: number | null = null;
  ts.forEach((t, i) => {
    if (px[i] == null) return;
    if (t <= t0) a = px[i];
    if (t <= t1) b = px[i];
  });
  return a != null && b != null ? (b as number) / (a as number) - 1 : null;
}

/**
 * Renders 1 row per symbol: a sparkline over the call, the move over the call and the move in the selected
 * moment's 5-minute window, stock first, then sector, peers and cross assets.
 */
export default function Comps({ ctx, ticker, start, end, moment }: {
  ctx: Context; ticker: string; start: string; end: string; moment?: Moment;
}) {
  const ts = ctx.ts.map((t) => new Date(t).getTime());
  const t0 = new Date(start).getTime(), t1 = new Date(end).getTime();
  // the window opens at the last close before the statement's minute, as the pipeline measures it
  const m0 = moment ? Math.floor(new Date(moment.ts).getTime() / 60_000) * 60_000 - 1 : null;
  const inCall = ts.map((t) => t >= t0 && t <= t1);
  return (
    <div className="mt-4">
      <h2 className="text-lg">Comps</h2>
      <p className="text-[11px] text-muted mb-1">
        Over the call, and {moment ? <>in the 5 min after #{moment.rank} at {clock(moment.ts)} ET</> :
          "around a moment once one is selected"}.
      </p>
      <table className="w-full text-xs tabular">
        <thead>
          <tr className="text-left text-muted border-b border-line">
            <th className="py-1 font-normal">Symbol</th>
            <th className="py-1 font-normal hidden sm:table-cell" />
            <th className="py-1 font-normal text-right">Call</th>
            <th className="py-1 font-normal text-right">{moment ? `#${moment.rank} 5 min` : "Moment"}</th>
          </tr>
        </thead>
        <tbody>
          {ctx.series.map((s) => {
            const call = move(ts, s.px, t0, t1);
            const mom = m0 != null ? move(ts, s.px, m0, m0 + WINDOW_MS) : null;
            const pts = s.px.filter((_, i) => inCall[i]).filter((x): x is number => x != null);
            return (
              <tr key={s.sym} className={`border-b border-line/60 ${s.sym === ticker ? "font-semibold" : ""}`}>
                <td className="py-1 pr-2 whitespace-nowrap">
                  {s.sym} <span className="font-normal text-muted">{ROLE_LABEL[s.role]}</span></td>
                <td className="py-1 hidden sm:table-cell"><Spark pts={pts} /></td>
                <td className={`py-1 text-right ${call != null ? DIR_TEXT[dir(call)] : ""}`}>
                  {call != null ? pct(call) : "—"}</td>
                <td className={`py-1 text-right ${mom != null ? DIR_TEXT[dir(mom)] : ""}`}>
                  {mom != null ? pct(mom) : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Renders a tiny price path relative to its first point, colored by where it ends.
 */
function Spark({ pts }: { pts: number[] }) {
  if (pts.length < 2) return null;
  const W = 72, H = 16;
  const lo = Math.min(...pts), hi = Math.max(...pts), span = hi - lo || 1;
  const d = pts.map((v, i) => `${((i * W) / (pts.length - 1)).toFixed(1)},${(H - ((v - lo) * H) / span).toFixed(1)}`);
  return (
    <svg width={W} height={H} viewBox={`0 -1 ${W} ${H + 2}`} aria-hidden>
      <polyline points={d.join(" ")} fill="none" strokeWidth={1}
        stroke={DIR_HEX[dir(pts[pts.length - 1] / pts[0] - 1)]} />
    </svg>
  );
}
