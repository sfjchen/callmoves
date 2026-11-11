// Library page: every processed call grouped by sector, as compact cards with the call's move against its sector
// ETF, the top moment and the call's themes. Filter by ticker, company, quarter or theme, sort by date or move size.
import Link from "next/link";
import { listCalls } from "@/lib/data";
import { clock, dir, DIR_HEX, DIR_TEXT, pct, SECTOR, shortQuarter, shortSpeaker, SPILL_LABEL, TZ } from "@/lib/format";
import type { CallSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

const SORTS = { date: "Newest", call: "Biggest call move", moment: "Biggest top moment" } as const;
type Sort = keyof typeof SORTS;

/**
 * Renders a small trend of the adjusted return over the call, last point marked and colored by its sign.
 */
function Sparkline({ pts }: { pts: number[] }) {
  if (pts.length < 2) return null;
  const W = 88, H = 26, PAD = 2;
  const lo = Math.min(0, ...pts), hi = Math.max(0, ...pts), span = hi - lo || 1;
  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / (pts.length - 1);
  const y = (v: number) => H - PAD - ((v - lo) * (H - 2 * PAD)) / span;
  const last = pts[pts.length - 1];
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="shrink-0" aria-hidden>
      <line x1={PAD} x2={W - PAD} y1={y(0)} y2={y(0)} stroke="#e2dbd1" strokeDasharray="2 2" />
      <polyline points={pts.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}
        fill="none" stroke="#6b6359" strokeWidth={1.25} />
      <circle cx={x(pts.length - 1)} cy={y(last)} r={2.5} fill={DIR_HEX[dir(last)]} />
    </svg>
  );
}

/**
 * Renders 1 call as a card: ticker, company and date, the call's move vs its ETF with a sparkline, the top moment
 * with its time, 5-minute move and quote, and up to 3 themes.
 */
function CallCard({ c }: { c: CallSummary }) {
  const end = c.spark[c.spark.length - 1] ?? 0, top = c.top_moment;
  const date = new Date(c.call_start).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: TZ });
  return (
    <Link href={`/calls/${c.id}`} className="card card-hover block p-3 sm:p-3.5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-semibold tracking-tight">{c.ticker}</span>
            <span className="text-xs text-nb-muted tabular whitespace-nowrap">{shortQuarter(c.quarter)} · {date}</span>
          </div>
          {c.company && <div className="text-xs text-nb-muted truncate">{c.company}</div>}
        </div>
        <div className="text-right">
          <div className="flex items-center gap-1.5 justify-end">
            <Sparkline pts={c.spark} />
            <span className={`text-sm font-semibold tabular ${DIR_TEXT[dir(end)]}`}>{pct(end)}</span>
          </div>
          <div className="text-[11px] text-nb-muted">call vs {c.etf ?? "sector"}</div>
        </div>
      </div>
      {top ? (
        <div className="mt-2 border-t border-nb-border pt-2">
          <div className="flex items-baseline gap-2 text-[11px] text-nb-muted tabular">
            <span>Top moment {clock(top.ts)} ET</span>
            {top.speaker && <span className="truncate">{shortSpeaker(top.speaker)}</span>}
            <span className={`ml-auto shrink-0 text-sm font-semibold ${DIR_TEXT[dir(top.adj_ret_5m)]}`}>
              {pct(top.adj_ret_5m)}</span>
            <span className="shrink-0">in 5 min</span>
          </div>
          <p className="mt-0.5 text-sm leading-snug line-clamp-2">&ldquo;{top.quote}&rdquo;</p>
        </div>
      ) : (
        <p className="mt-2 border-t border-nb-border pt-2 text-xs text-nb-muted">No abnormal minute during the call.</p>
      )}
      {(c.themes?.length || top?.spillover) && (
        <div className="mt-2 flex flex-wrap gap-1 text-[11px]">
          {c.themes?.map((t) => <span key={t} className="rounded bg-nb-border/70 px-1.5 py-0.5">{t}</span>)}
          {top?.spillover && <span className="rounded border border-nb-border px-1.5 py-0.5 text-nb-muted">
            top moment: {SPILL_LABEL[top.spillover]}</span>}
        </div>
      )}
    </Link>
  );
}

/**
 * Renders the library: filter and sort controls, then 1 collapsible section per sector with its calls as cards,
 * 2 columns on wide screens and 1 on phones.
 */
export default async function Library({ searchParams }: PageProps<"/">) {
  const sp = await searchParams;
  const q = String(sp.q ?? "").trim();
  const sort: Sort = String(sp.sort) in SORTS ? (String(sp.sort) as Sort) : "date";
  const needle = q.toLowerCase();
  const all = await listCalls();
  const calls = all.filter((c) => [c.ticker, c.company ?? "", c.quarter, SECTOR[c.etf ?? ""] ?? "", ...(c.themes ?? [])]
    .some((f) => f.toLowerCase().includes(needle)));
  const key = (c: CallSummary) => sort === "call" ? -Math.abs(c.spark[c.spark.length - 1] ?? 0)
    : sort === "moment" ? -Math.abs(c.top_moment?.adj_ret_5m ?? 0) : -new Date(c.call_start).getTime();
  const groups = new Map<string, CallSummary[]>();
  for (const c of [...calls].sort((a, b) => key(a) - key(b))) {
    const g = SECTOR[c.etf ?? ""] ?? "Other";
    groups.set(g, [...(groups.get(g) ?? []), c]);
  }
  const ordered = [...groups.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  return (
    <div className="mx-auto max-w-6xl">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2 mb-4">
        <div className="mr-auto">
          <h1 className="text-3xl leading-tight">Call library</h1>
          <p className="text-sm text-nb-muted">
            {all.length} earnings calls. Each card shows the stock&rsquo;s move against its sector ETF over the call
            and the statement just before its biggest abnormal minute.
          </p>
        </div>
        <form className="flex flex-wrap gap-2 text-sm w-full sm:w-auto">
          <input
            type="search" name="q" defaultValue={q} placeholder="Ticker, company, sector or theme"
            className={"min-w-0 flex-1 sm:w-64 rounded border border-nb-border bg-white px-2 py-1 " +
              "placeholder:text-nb-muted/70 focus:outline-none focus:ring-1 focus:ring-nb-accent/40"}
            aria-label="Filter calls"
          />
          <select name="sort" defaultValue={sort} aria-label="Sort calls"
            className="rounded border border-nb-border bg-white px-2 py-1">
            {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <button className="rounded bg-nb-accent px-3 py-1 text-white">Apply</button>
        </form>
      </div>
      {calls.length === 0 ? (
        <p className="text-nb-muted">{all.length === 0 ? "No processed calls yet." : `No call matches "${q}".`}</p>
      ) : ordered.map(([sector, cs]) => (
        <details key={sector} open className="mb-5 group">
          <summary className="flex cursor-pointer items-baseline gap-2 border-b border-nb-border pb-1 mb-2.5
            list-none [&::-webkit-details-marker]:hidden">
            <span className="text-nb-muted text-xs w-3 transition-transform group-open:rotate-90">▸</span>
            <h2 className="text-xl">{sector}</h2>
            <span className="text-xs text-nb-muted tabular">{cs.length} {cs.length === 1 ? "call" : "calls"}
              {cs[0].etf && ` · vs ${cs[0].etf}`}</span>
          </summary>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {cs.map((c) => <CallCard key={c.id} c={c} />)}
          </div>
        </details>
      ))}
    </div>
  );
}
