// Replay view for 1 call (client component): price chart, audio player, ranked
// moments and transcript on 1 shared cursor.
"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar, ComposedChart, Line, ReferenceArea, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { CallOutput, Estimate, Moment, Segment } from "@/lib/types";
import { clock, dir, DIR_HEX, DIR_TEXT, mmss, pct, shortSpeaker, WINDOW_MS } from "@/lib/format";

const BEFORE_MS = 45 * 60_000; // chart window: 45 min before the call ...
const AFTER_MS = 30 * 60_000;  // ... to 30 min after it ends
const VOL_AXIS_MAX = 12;       // volume multiples above this are block prints, let them clip
const GLYPH = { up: "▲", down: "▼", flat: "" };

type Point = { t: number; cum: number; vol_mult: number; flagged: boolean };

/**
 * Renders the replay view: header, price chart, audio player, ranked moments and transcript. All times are in ms
 * since audio t=0, which is the call start minus audio_offset_ms, so wall clock = start + ms. `baseline` (study
 * condition) hides the moments panel, the scoring explanation and the numbered markers.
 */
export default function Replay({ data, baseline = false }: { data: CallOutput; baseline?: boolean }) {
  const offset = data.call.audio_offset_ms ?? 0;
  const start = useMemo(() => new Date(data.call.call_start).getTime() - offset, [data.call.call_start, offset]);
  const endMs = new Date(data.call_end).getTime() - start;
  const [cursorMs, setCursorMs] = useState(data.moments[0]?.t0_ms ?? offset);
  const [audioOk, setAudioOk] = useState<boolean | null>(null); // null until the HEAD check on the Supabase URL answers
  useEffect(() => {
    const url = data.call.audio_url;
    if (!url) return;
    fetch(url, { method: "HEAD" }).then((r) => setAudioOk(r.ok)).catch(() => setAudioOk(false));
  }, [data.call.audio_url]);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [query, setQuery] = useState("");
  const [hit, setHit] = useState(0);

  // Moves the cursor and, when the audio is more than 0.5 s off, the audio with it.
  function seek(ms: number) {
    setCursorMs(ms);
    const a = audioRef.current;
    if (a && Math.abs(a.currentTime * 1000 - ms) > 500) a.currentTime = ms / 1000;
  }

  const points: Point[] = useMemo(
    () =>
      data.bars
        .map((b) => ({
          t: new Date(b.ts).getTime() - start, cum: b.cum_adj_ret * 100, vol_mult: b.vol_mult, flagged: b.flagged,
        }))
        .filter((p) => p.t >= offset - BEFORE_MS && p.t <= endMs + AFTER_MS),
    [data.bars, start, offset, endMs],
  );
  const cursorPoint = useMemo(() => nearest(points, cursorMs), [points, cursorMs]);
  const active = useMemo(() => activeSegment(data.segments, cursorMs), [data.segments, cursorMs]);
  const byTime = useMemo(() => [...data.moments].sort((a, b) => a.t0_ms - b.t0_ms), [data.moments]);
  const hits = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? data.segments.filter((s) => s.text.toLowerCase().includes(q)) : [];
  }, [data.segments, query]);
  const flaggedCount = data.bars.filter((b) => b.flagged).length;
  const est = (data.estimates ?? []).filter((e) => e.role === "reported");

  // Keyboard: j/k moments, 1 to 5 by rank, [ ] transcript turns, space audio. Inputs keep their keys.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      if (
        e.metaKey || e.ctrlKey || e.altKey ||
        (el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable))
      ) return;
      const idx = active ? data.segments.indexOf(active) : -1;
      let to: number | undefined;
      if (e.key === "j") to = byTime.find((m) => m.t0_ms > cursorMs)?.t0_ms;
      else if (e.key === "k") to = [...byTime].reverse().find((m) => m.t0_ms < cursorMs)?.t0_ms;
      else if (/^[1-5]$/.test(e.key)) to = data.moments.find((m) => m.rank === Number(e.key))?.t0_ms;
      else if (e.key === "]") to = data.segments[idx + 1]?.t0_ms;
      else if (e.key === "[") to = data.segments[Math.max(0, idx - 1)]?.t0_ms;
      else if (e.key === " " && audioRef.current) {
        e.preventDefault();
        const a = audioRef.current;
        if (a.paused) void a.play(); else a.pause();
        return;
      }
      if (to !== undefined) { e.preventDefault(); seek(to); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // Cycles the search hits on Enter and Shift+Enter, moving the cursor so the list scrolls to each hit.
  function onSearchKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter" || hits.length === 0) return;
    e.preventDefault();
    const next = (hit + (e.shiftKey ? -1 : 1) + hits.length) % hits.length;
    setHit(next);
    seek(hits[next].t0_ms);
  }

  const noAudio = !data.call.audio_url || audioOk === false;

  return (
    <div className="max-w-7xl">
      <div
        className={
          "lg:hidden sticky top-0 z-10 -mx-6 -mt-5 mb-3 px-6 py-1.5 bg-white/95 border-b border-neutral-200 " +
          "text-xs tabular text-neutral-700"
        }
      >
        {clock(new Date(start + cursorMs))} ET · {mmss(cursorMs - offset)}
        {cursorPoint && (
          <>
            {" "}· <span className={DIR_TEXT[dir(cursorPoint.cum / 100)]}>{pct(cursorPoint.cum / 100)}</span> ·{" "}
            {cursorPoint.vol_mult.toFixed(1)}x volume
          </>
        )}
      </div>

      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 mb-1">
        <h1 className="text-xl font-semibold">
          {data.call.ticker} <span className="font-normal text-neutral-500">{data.call.quarter}</span>
        </h1>
        {data.call.company && <span className="text-sm text-neutral-600">{data.call.company}</span>}
        <span className="text-sm text-neutral-500 tabular">
          Call {clock(data.call.call_start)} to {clock(data.call_end)} ET, vs {data.call.etf}
        </span>
        {est.length > 0 && (
          <span className="text-xs text-neutral-600 tabular">
            {est.map((e, i) => (
              <span key={e.metric} title={`Consensus as of ${new Date(e.as_of).toLocaleDateString("en-US",
                { month: "short", day: "numeric", timeZone: "UTC" })}${e.basis === "after_call" ?
                ", after the call, so it already reflects it. Display only, never scores a claim" : ""}`}>
                {i > 0 && " · "}{e.metric === "eps" ? "EPS" : "Revenue"}{" "}
                {e.actual != null && e.surprise_pct != null ? (
                  <span className={DIR_TEXT[dir(e.surprise_pct)]}>
                    {money(e, e.actual)} vs {money(e, e.consensus)} est {pct(e.surprise_pct, 1)}
                  </span>
                ) : `${money(e, e.consensus)} est`}
                {e.basis === "after_call" && "*"}
              </span>
            ))}
          </span>
        )}
      </div>
      {!baseline && <p className="text-xs text-neutral-600 tabular mb-1">
        {flaggedCount} abnormal minutes → {data.candidate_seg_ids.length} turns → {data.n_claims} claims →{" "}
        {data.moments.length} moments
      </p>}
      {!baseline && <details className="text-xs text-neutral-500 mb-3">
        <summary className="cursor-pointer w-fit underline underline-offset-2 hover:text-neutral-700">
          How it&rsquo;s scored</summary>
        <ul className="mt-1 max-w-3xl text-neutral-600 space-y-0.5">
          <li>Reads only turns in the 5 min before an abnormal minute ({data.llm_used ? "Claude" : "rule-based"}).</li>
          <li>Score = novelty × kind × |5-min adjusted return| × volume multiple × closeness to the minute.</li>
          <li>An EPS or revenue figure over 1% off consensus counts as new.</li>
          <li>Top 5, 1 per price event. &ldquo;Move began earlier&rdquo; means the price may have shaped the
            statement. Associations, not causes.</li>
          {data.stats?.gpu_usd != null && data.stats.claude_usd != null && <li className="tabular">
            Processed {data.stats.audio_min} min of audio in{" "}
            {Math.round(((data.stats.ingest_s ?? 0) + (data.stats.claude_s ?? 0)) / 60)} min for $
            {(data.stats.gpu_usd + data.stats.claude_usd).toFixed(2)} (GPU ${data.stats.gpu_usd.toFixed(2)}, Claude $
            {data.stats.claude_usd.toFixed(2)}).</li>}
        </ul>
      </details>}

      {data.open_reaction ? (
        <section className="border border-neutral-200 rounded-lg p-3 mb-4 text-sm">
          <div className="font-medium mb-1">
            No {data.call.ticker} trades while the call ran, so the reaction is read at the next open
          </div>
          <div className="tabular">
            {clock(data.open_reaction.from_ts)} ET last trade before the call to{" "}
            {clock(data.open_reaction.to_ts)} ET after the open:{" "}
            <span className={`font-medium ${DIR_TEXT[dir(data.open_reaction.adj_ret)]}`}>
              {pct(data.open_reaction.ret)}
            </span>{" "}
            vs {pct(data.open_reaction.etf_ret)} {data.call.etf}, {pct(data.open_reaction.adj_ret)} adjusted,{" "}
            {data.open_reaction.vol_mult.toFixed(1)}x volume
          </div>
          <div className="text-xs text-neutral-500 mt-1">
            Every statement shares this one move, so moments are ranked by novelty and kind only, all at low confidence.
          </div>
        </section>
      ) : (
        <section className="border border-neutral-200 rounded-lg p-3 mb-4">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 mb-1">
            <div className="text-sm font-medium">
              Sector-adjusted return vs {data.call.etf} (%, cumulative from{" "}
              {clock(new Date(start + (points[0]?.t ?? 0)))}) and volume multiple
            </div>
            <div className="hidden lg:block text-sm text-neutral-600 tabular">
              cursor {clock(new Date(start + cursorMs))} ET ({mmss(cursorMs - offset)} into call)
              {cursorPoint && <> · {pct(cursorPoint.cum / 100)} · {cursorPoint.vol_mult.toFixed(1)}x volume</>}
            </div>
          </div>
          <div className="h-48 lg:h-72">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart
                data={points}
                margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
                onClick={(s) => {
                  if (s && typeof s.activeLabel === "number") seek(clamp(s.activeLabel, offset, endMs));
                }}
                style={{ cursor: "pointer" }}
              >
                <XAxis
                  dataKey="t" type="number" domain={["dataMin", "dataMax"]} scale="time"
                  tickFormatter={(t: number) => clock(new Date(start + t))} ticks={ticks(points, start)} fontSize={11}
                />
                <YAxis yAxisId="ret" tickFormatter={(v: number) => `${v.toFixed(1)}%`} fontSize={11} width={48} />
                <YAxis
                  yAxisId="vol" orientation="right" tickFormatter={(v: number) => `${v.toFixed(0)}x`} fontSize={11}
                  width={36} domain={[0, VOL_AXIS_MAX]} allowDataOverflow
                />
                <Tooltip
                  labelFormatter={(t) => `${clock(new Date(start + Number(t)))} ET`}
                  formatter={(v, name) => (
                    name === "cum"
                      ? [`${Number(v).toFixed(2)}%`, "adj. return"]
                      : [`${Number(v).toFixed(1)}x`, "volume"]
                  )}
                />
                <ReferenceArea yAxisId="ret" x1={offset} x2={endMs} fill="#f5f5f5" fillOpacity={0.9} />
                {data.moments.map((m) => (
                  <ReferenceArea
                    key={m.rank} yAxisId="ret" x1={m.t0_ms} x2={m.t0_ms + WINDOW_MS} fill={DIR_HEX[dir(m.adj_ret_5m)]}
                    fillOpacity={0.15}
                  />
                ))}
                <Bar yAxisId="vol" dataKey="vol_mult" fill="#d4d4d4" isAnimationActive={false} />
                <Line
                  yAxisId="ret" type="monotone" dataKey="cum" stroke="#1d4ed8" strokeWidth={2} dot={false}
                  isAnimationActive={false}
                />
                {points.filter((p) => p.flagged).map((p) => (
                  <ReferenceDot key={p.t} yAxisId="ret" x={p.t} y={p.cum} r={4} fill="#f59e0b" stroke="none" />
                ))}
                {data.moments.map((m) => {
                  const p = nearest(points, m.t0_ms);
                  return p ? (
                    <ReferenceDot
                      key={m.rank} yAxisId="ret" x={p.t} y={p.cum} r={9} fill="#1d4ed8" stroke="#fff" strokeWidth={1.5}
                      label={{ value: String(m.rank), fill: "#fff", fontSize: 10, fontWeight: 600 }}
                      onClick={() => seek(m.t0_ms)}
                    />
                  ) : null;
                })}
                <ReferenceLine yAxisId="ret" x={cursorMs} stroke="#171717" strokeDasharray="4 3" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="text-xs text-neutral-500 mt-1">
            {baseline ? "Grey band is the call." :
              "Grey band is the call, orange dots abnormal minutes, " +
                "numbered dots moments with their 5-min window shaded."}
            <span className="hidden lg:inline cursor-help ml-1 underline decoration-dotted"
              title={`j / k: next and previous moment. 1 to 5: jump to a rank. [ / ]: previous and next turn.${audioOk ?
                " Space: play or pause." : ""}`}>?</span>
          </div>
        </section>
      )}

      {data.call.audio_url && audioOk && (
        <audio
          ref={audioRef} controls src={data.call.audio_url} className="w-full mb-4" onError={() => setAudioOk(false)}
          onTimeUpdate={(e) => setCursorMs(Math.round(e.currentTarget.currentTime * 1000))}
        />
      )}
      {noAudio && (
        <p className="text-xs text-neutral-500 mb-4">
          Audio is unavailable, click the chart or transcript to move the cursor.
        </p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {!baseline && <section className="lg:col-span-2 border border-neutral-200 rounded-lg p-3">
          <h2 className="text-sm font-medium mb-1">Moments, ranked</h2>
          {data.moments.length === 0 ? (
            <p className="text-sm text-neutral-500">
              No minute cleared the threshold during the call, {data.candidate_seg_ids.length} turns read.
            </p>
          ) : (
            <ol className="space-y-2">
              {data.moments.map((m) => (
                <MomentCard
                  key={m.rank} m={m} atOpen={!!data.open_reaction} active={active?.seg_id === m.seg_id}
                  onSelect={() => {
                    seek(m.t0_ms);
                    // panels stack on phones
                    if (window.innerWidth < 1024) {
                      document.getElementById("transcript")?.scrollIntoView({ behavior: "smooth" });
                    }
                  }}
                />
              ))}
            </ol>
          )}
        </section>}

        <section className={`${baseline ? "lg:col-span-5" : "lg:col-span-3"} border border-neutral-200 rounded-lg p-3`}>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
            <h2 id="transcript" className="text-sm font-medium">Transcript</h2>
            <input
              type="search" value={query} placeholder="Search the transcript" aria-label="Search the transcript"
              onChange={(e) => { setQuery(e.target.value); setHit(0); }} onKeyDown={onSearchKey}
              className={
                "ml-auto w-48 rounded border border-neutral-300 px-2 py-0.5 text-xs placeholder:text-neutral-400 " +
                "focus:outline-none focus:ring-1 focus:ring-blue-300"
              }
            />
            {query.trim() && (
              <span className="text-xs text-neutral-500 tabular w-16">
                {hits.length ? `${hit + 1} of ${hits.length}` : "0 hits"}
              </span>
            )}
          </div>
          <TranscriptList
            segments={data.segments} activeId={active?.seg_id ?? null}
            momentSegs={new Set(data.moments.map((m) => m.seg_id))}
            offset={offset} query={query.trim()} onSelect={(s) => seek(s.t0_ms)}
          />
        </section>
      </div>
    </div>
  );
}

/**
 * Formats an EPS in dollars or a revenue in $B or $M.
 */
function money(e: Estimate, x: number): string {
  return e.metric === "eps" ? `$${x.toFixed(2)}` : x >= 1e9 ? `$${(x / 1e9).toFixed(1)}B` : `$${(x / 1e6).toFixed(0)}M`;
}

/**
 * Renders 1 ranked moment: rank, time, quote and adjusted return, with the evidence shown only while it is active.
 * Low-confidence moments are dimmed.
 */
function MomentCard({ m, atOpen, active, onSelect }: {
  m: Moment; atOpen: boolean; active: boolean; onSelect: () => void;
}) {
  const d = dir(m.adj_ret_5m);
  return (
    <li>
      <button onClick={onSelect} className={`w-full text-left rounded-md border p-2.5 hover:bg-neutral-50 ${active ?
        "border-blue-600 bg-blue-50/40" : `border-neutral-200 ${m.confidence === "low" ? "opacity-60" : ""}`}`}>
        <div className="flex items-center gap-2 text-xs text-neutral-500">
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-blue-700 text-white
            font-semibold">{m.rank}</span>
          <span className="tabular">{clock(m.ts)} ET</span>
          <span className="rounded bg-neutral-100 px-1.5 py-0.5">{m.section === "qa" ? "Q&A" : "prepared"}</span>
          <span className={`ml-auto text-base font-semibold tabular ${DIR_TEXT[d]}`}>
            {GLYPH[d]} {pct(m.adj_ret_5m)}</span>
        </div>
        <p className="mt-1.5 text-sm leading-snug">&ldquo;{m.quote}&rdquo;</p>
        {active && (
          <div className="mt-2 space-y-0.5 text-xs text-neutral-600 tabular">
            {m.question && <p className="text-neutral-500">
              <span className="font-medium">Analyst asked:</span> {m.question}</p>}
            <p>
              <span className="font-medium">{shortSpeaker(m.speaker)}</span>, {m.vol_mult.toFixed(1)}x volume, adj.{" "}
              {atOpen ? "at next open" : "next 5 min"}: {pct(m.ret_5m)} raw vs {pct(m.etf_ret_5m)} sector
            </p>
            <p>
              {m.novel ? "New" : `Already in the ${m.matched_doc === "release" ? "release" : "prior call"}`}
              {m.vs_consensus_pct != null && <>, <span className={DIR_TEXT[dir(m.vs_consensus_pct)]}>
                {pct(m.vs_consensus_pct, 1)} vs consensus</span></>}
              {m.nearest_flag_min >= 0 && `, abnormal minute ${m.nearest_flag_min === 0 ? "at" :
                `+${m.nearest_flag_min.toFixed(0)} min after`} the statement`}
            </p>
            <p>
              {m.pre_move && <span className="text-amber-700">Move began earlier. </span>}
              {m.confidence} confidence
            </p>
          </div>
        )}
      </button>
    </li>
  );
}

/**
 * Renders the transcript as clickable turns with their 5-min adjusted return, scrolling the active turn to center.
 */
function TranscriptList({ segments, activeId, momentSegs, offset, query, onSelect }: {
  segments: Segment[]; activeId: number | null; momentSegs: Set<number>; offset: number;
  query: string; onSelect: (s: Segment) => void;
}) {
  const listRef = useRef<HTMLOListElement>(null);
  const activeRef = useRef<HTMLLIElement>(null);
  // Scrolls the list, not the page, so the chart stays in view.
  useEffect(() => {
    const list = listRef.current, el = activeRef.current;
    if (!list || !el) return;
    list.scrollTo({ top: el.offsetTop - list.clientHeight / 2 + el.clientHeight / 2, behavior: "smooth" });
  }, [activeId]);
  return (
    <ol ref={listRef} className="relative max-h-[32rem] overflow-y-auto pr-1 space-y-1">
      {segments.map((s) => {
        const isActive = s.seg_id === activeId;
        const ret = s.adj_ret_5m;
        return (
          <li key={s.seg_id} ref={isActive ? activeRef : undefined}>
            <button
              onClick={() => onSelect(s)}
              className={
                "flex w-full gap-2 text-left rounded px-2 py-1.5 text-sm hover:bg-neutral-50 " +
                (isActive ? "bg-blue-50 ring-1 ring-blue-300" : "")
              }
            >
              <span
                className="w-12 shrink-0 text-right text-[11px] text-neutral-400 tabular leading-5"
                title={ret != null ? "adjusted return over the next 5 min" : undefined}
              >
                {ret != null ? pct(ret) : ""}
              </span>
              <span className="min-w-0">
                <span className="text-xs text-neutral-500 tabular mr-2">{mmss(s.t0_ms - offset)}</span>
                <span
                  className={
                    "text-xs font-medium mr-2 " +
                    (momentSegs.has(s.seg_id) ? "text-blue-700" : "text-neutral-700")
                  }
                >
                  {shortSpeaker(s.speaker)}
                </span>
                <span className="text-neutral-800">{highlight(s.text, query)}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Wraps every case-insensitive occurrence of q in a mark element.
 */
function highlight(text: string, q: string): React.ReactNode {
  if (!q) return text;
  const parts = text.split(new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"));
  return parts.map((p, i) => (i % 2 ? <mark key={i} className="bg-yellow-200 rounded-sm">{p}</mark> : p));
}

/**
 * Returns the last turn that starts at or before ms.
 */
function activeSegment(segments: Segment[], ms: number): Segment | null {
  let best: Segment | null = null;
  for (const s of segments) if (s.t0_ms <= ms) best = s; else break;
  return best;
}

/**
 * Returns the chart point closest in time to ms.
 */
function nearest(points: Point[], ms: number): Point | null {
  if (points.length === 0) return null;
  let best = points[0];
  for (const p of points) if (Math.abs(p.t - ms) < Math.abs(best.t - ms)) best = p;
  return best;
}

/**
 * Limits x to the range lo to hi.
 */
function clamp(x: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, x));
}

/**
 * Returns chart x ticks every 15 wall-clock minutes.
 */
function ticks(points: Point[], start: number): number[] {
  if (points.length === 0) return [];
  const step = 15 * 60_000;
  const first = Math.ceil((start + points[0].t) / step) * step - start;
  const out: number[] = [];
  for (let t = first; t <= points[points.length - 1].t; t += step) out.push(t);
  return out;
}
