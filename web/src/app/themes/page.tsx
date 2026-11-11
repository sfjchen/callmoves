// Themes page: every ranked moment across calls grouped by what it was about, using the pipeline's theme regexes
// (themes.json, kept equal to run.THEMES by pipeline/tests/test_themes.py), each row linking to the replay there.
import Link from "next/link";
import { listMoments } from "@/lib/data";
import { clock, dir, DIR_TEXT, pct, shortQuarter } from "@/lib/format";
import type { CallSummary, Moment } from "@/lib/types";
import THEMES from "@/lib/themes.json";

export const dynamic = "force-dynamic";

const OTHER = "No theme";
const RX = Object.entries(THEMES).map(([t, rx]) => [t, new RegExp(rx, "i")] as const);

type Row = { c: CallSummary; m: Moment; atOpen: boolean };

/**
 * Renders the theme index with counts, the caution line, then 1 section per theme with its moments sorted by move
 * size. A moment joins every theme whose regex matches its metric and quote, as the pipeline matches claims.
 */
export default async function ThemesPage() {
  const calls = await listMoments();
  const groups = new Map<string, Row[]>();
  for (const { summary: c, moments, atOpen } of calls) {
    for (const m of moments) {
      const hits = RX.filter(([, rx]) => rx.test(`${m.metric ?? ""} ${m.quote}`)).map(([t]) => t);
      for (const t of hits.length ? hits : [OTHER]) groups.set(t, [...(groups.get(t) ?? []), { c, m, atOpen }]);
    }
  }
  const ordered = [...groups.entries()]
    .map(([t, rows]) => [t, rows.sort((a, b) => Math.abs(b.m.adj_ret_5m) - Math.abs(a.m.adj_ret_5m))] as const)
    .sort((a, b) => Number(a[0] === OTHER) - Number(b[0] === OTHER) || b[1].length - a[1].length);
  const total = calls.reduce((n, c) => n + c.moments.length, 0);
  const slug = (t: string) => `theme-${t.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="text-3xl leading-tight">Themes</h1>
      <p className="text-sm text-nb-muted">
        {total} ranked moments from {calls.length} calls, grouped by what the statement was about. A moment can sit
        under more than 1 theme.
      </p>
      <p className="mt-2 rounded border border-amber-300 bg-amber-50 px-2.5 py-1.5 text-sm text-amber-900">
        Samples are small, often a handful of moments per theme, and each one is an association: the statement came
        right before an abnormal move, which does not show it caused the move.
      </p>
      <nav aria-label="Themes" className="mt-3 flex flex-wrap gap-1.5 text-sm">
        {ordered.map(([t, rows]) => (
          <a key={t} href={`#${slug(t)}`}
            className="rounded-full border border-nb-border px-2.5 py-0.5 tabular hover:bg-white">
            {t} <span className="text-nb-muted">{rows.length}</span></a>
        ))}
      </nav>
      {ordered.map(([t, rows]) => {
        const nCalls = new Set(rows.map((r) => r.c.id)).size;
        const nTop = calls.filter((c) => c.summary.themes?.includes(t)).length;
        const up = rows.filter((r) => dir(r.m.adj_ret_5m) === "up").length;
        const down = rows.filter((r) => dir(r.m.adj_ret_5m) === "down").length;
        return (
          <section key={t} id={slug(t)} className="mt-5 scroll-mt-4">
            <div className="flex flex-wrap items-baseline gap-x-2 border-b border-nb-border pb-1 mb-2">
              <h2 className="text-xl">{t}</h2>
              <span className="text-xs text-nb-muted tabular">
                {rows.length} {rows.length === 1 ? "moment" : "moments"} in {nCalls} {nCalls === 1 ? "call" : "calls"}
                {" "}· {up} up, {down} down{t !== OTHER && ` · a top 3 theme of ${nTop} ${nTop === 1 ? "call" :
                  "calls"}`}
              </span>
            </div>
            <ol className="space-y-1.5">
              {rows.map(({ c, m, atOpen }) => (
                <li key={`${c.id}-${m.rank}`}>
                  <Link href={`/calls/${c.id}?t=${Math.floor(m.t0_ms / 1000)}`}
                    className="card card-hover block px-3 py-2">
                    <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-nb-muted tabular">
                      <span className="text-sm font-semibold text-nb-text">{c.ticker}</span>
                      <span>{shortQuarter(c.quarter)}</span>
                      <span>{clock(m.ts)} ET</span>
                      <span>#{m.rank}</span>
                      <span className="ml-auto">
                        <span className={`text-sm font-semibold ${DIR_TEXT[dir(m.adj_ret_5m)]}`}>
                          {pct(m.adj_ret_5m)}</span> vs {c.etf ?? "sector"} {atOpen ? "at next open" : "in 5 min"}
                      </span>
                    </div>
                    <p className="mt-0.5 text-sm leading-snug line-clamp-2">&ldquo;{m.quote}&rdquo;</p>
                  </Link>
                </li>
              ))}
            </ol>
          </section>
        );
      })}
    </div>
  );
}
