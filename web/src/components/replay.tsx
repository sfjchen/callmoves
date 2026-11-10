"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar, ComposedChart, Line, ReferenceArea, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { CallOutput, Estimate, Moment, Segment } from "@/lib/types";
import { clock, dir, DIR_HEX, DIR_TEXT, mmss, pct, shortSpeaker } from "@/lib/format";

const BEFORE_MS = 45 * 60_000; // chart window: 45 min before the call ...
const AFTER_MS = 30 * 60_000;  // ... to 30 min after it ends
const WINDOW_MS = 5 * 60_000;  // reaction window after a statement
const VOL_AXIS_MAX = 12;       // volume multiples above this are block prints, let them clip
// confidence is blue or grey so that green and red mean price direction only
const CONF = { high: "bg-blue-100 text-blue-800", medium: "bg-neutral-200 text-neutral-700", low: "bg-neutral-100 text-neutral-500" };
const GLYPH = { up: "▲", down: "▼", flat: "" };

type Point = { t: number; cum: number; vol_mult: number; flagged: boolean };

// All times in ms since audio t=0, which is the call start minus audio_offset_ms. Wall clock = start + ms.
export default function Replay({ data }: { data: CallOutput }) {
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

  // audio drives the cursor when playing, the cursor drives audio when the user clicks
  function seek(ms: number) {
    setCursorMs(ms);
    const a = audioRef.current;
    if (a && Math.abs(a.currentTime * 1000 - ms) > 500) a.currentTime = ms / 1000;
  }

  const points: Point[] = useMemo(
    () =>
      data.bars
        .map((b) => ({ t: new Date(b.ts).getTime() - start, cum: b.cum_adj_ret * 100, vol_mult: b.vol_mult, flagged: b.flagged }))
        .filter((p) => p.t >= offset - BEFORE_MS && p.t <= endMs + AFTER_MS),
    [data.bars, start, offset, endMs],
  );
  const cursorPoint = useMemo(() => nearest(points, cursorMs), [points, cursorMs]);
  const active = useMemo(() => activeSegment(data.segments, cursorMs), [data.segments, cursorMs]);
  const byTime = useMemo(() => [...data.moments].sort((a, b) => a.t0_ms - b.t0_ms), [data.moments]);
  const gutter = useMemo(() => segmentReturns(data, start), [data, start]);
  const hits = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? data.segments.filter((s) => s.text.toLowerCase().includes(q)) : [];
  }, [data.segments, query]);
  const flaggedCount = data.bars.filter((b) => b.flagged).length;

  // keyboard: j/k moments, 1 to 5 by rank, [ ] transcript turns, space audio. Inputs keep their keys.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey || (el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable))) return;
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

  // Enter and Shift+Enter cycle the search hits, each hit moves the cursor so the list scrolls to it
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
      <div className="lg:hidden sticky top-0 z-10 -mx-6 -mt-5 mb-3 px-6 py-1.5 bg-white/95 border-b border-neutral-200 text-xs tabular text-neutral-700">
        {clock(new Date(start + cursorMs))} ET · {mmss(cursorMs - offset)}
        {cursorPoint && <> · <span className={DIR_TEXT[dir(cursorPoint.cum / 100)]}>{pct(cursorPoint.cum / 100)}</span> · {cursorPoint.vol_mult.toFixed(1)}x volume</>}
      </div>

      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 mb-1">
        <h1 className="text-xl font-semibold">
          {data.call.ticker} <span className="font-normal text-neutral-500">{data.call.quarter}</span>
        </h1>
        {data.call.company && <span className="text-sm text-neutral-600">{data.call.company}</span>}
        <span className="text-sm text-neutral-500 tabular">
          Call {clock(data.call.call_start)} to {clock(data.call_end)} ET, vs {data.call.etf}
        </span>
        <span className="text-xs rounded bg-neutral-100 px-2 py-0.5 text-neutral-600 tabular">
          {flaggedCount} abnormal minutes → {data.candidate_seg_ids.length} turns read → {data.claims.length} claims → {data.moments.length} moments
        </span>
      </div>
      <ConsensusStrip estimates={(data.estimates ?? []).filter((e) => e.role === "reported")} />
      <details className="text-xs text-neutral-500 mb-3">
        <summary className="cursor-pointer w-fit underline underline-offset-2 hover:text-neutral-700">How scoring works</summary>
        <p className="mt-1 max-w-3xl text-neutral-600">
          Only turns spoken in the 5 min before an abnormal minute are read for claims ({data.llm_used ? "Claude" : "rule-based"} extraction).
          Score = novelty × kind × |adjusted return over the next 5 min| × volume multiple × closeness to the flagged minute.
          An EPS or revenue figure more than 1% off analyst consensus counts as new even when the release printed it.
          <br />
          Top 5 become moments, at most 1 per price event (flagged minutes within 2 min of each other), each graded
          high, medium or low confidence. &ldquo;Move began earlier&rdquo; marks a statement made after its event started, where the
          price may have shaped what was said. Associations, not causes.
        </p>
      </details>

      {data.open_reaction ? (
        <section className="border border-neutral-200 rounded-lg p-3 mb-4 text-sm">
          <div className="font-medium mb-1">
            No {data.call.ticker} trades while the call ran, so the reaction is read at the next open
          </div>
          <div className="tabular">
            {clock(data.open_reaction.from_ts)} ET last trade before the call to {clock(data.open_reaction.to_ts)} ET after the open:{" "}
            <span className={`font-medium ${DIR_TEXT[dir(data.open_reaction.adj_ret)]}`}>
              {pct(data.open_reaction.ret)}
            </span>{" "}
            vs {pct(data.open_reaction.etf_ret)} {data.call.etf}, {pct(data.open_reaction.adj_ret)} adjusted, {data.open_reaction.vol_mult.toFixed(1)}x volume
          </div>
          <div className="text-xs text-neutral-500 mt-1">
            Every statement shares this one move, so moments are ranked by novelty and kind only, all at low confidence.
          </div>
        </section>
      ) : (
        <section className="border border-neutral-200 rounded-lg p-3 mb-4">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 mb-1">
            <div className="text-sm font-medium">Sector-adjusted return vs {data.call.etf} (%, cumulative from {clock(new Date(start + (points[0]?.t ?? 0)))}) and volume multiple</div>
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
                onClick={(s) => { if (s && typeof s.activeLabel === "number") seek(clamp(s.activeLabel, offset, endMs)); }}
                style={{ cursor: "pointer" }}
              >
                <XAxis
                  dataKey="t" type="number" domain={["dataMin", "dataMax"]} scale="time"
                  tickFormatter={(t: number) => clock(new Date(start + t))} ticks={ticks(points, start)} fontSize={11}
                />
                <YAxis yAxisId="ret" tickFormatter={(v: number) => `${v.toFixed(1)}%`} fontSize={11} width={48} />
                <YAxis yAxisId="vol" orientation="right" tickFormatter={(v: number) => `${v.toFixed(0)}x`} fontSize={11} width={36} domain={[0, VOL_AXIS_MAX]} allowDataOverflow />
                <Tooltip
                  labelFormatter={(t) => `${clock(new Date(start + Number(t)))} ET`}
                  formatter={(v, name) => (name === "cum" ? [`${Number(v).toFixed(2)}%`, "adj. return"] : [`${Number(v).toFixed(1)}x`, "volume"])}
                />
                <ReferenceArea yAxisId="ret" x1={offset} x2={endMs} fill="#f5f5f5" fillOpacity={0.9} />
                {data.moments.map((m) => (
                  <ReferenceArea key={m.rank} yAxisId="ret" x1={m.t0_ms} x2={m.t0_ms + WINDOW_MS} fill={DIR_HEX[dir(m.adj_ret_5m)]} fillOpacity={0.15} />
                ))}
                <Bar yAxisId="vol" dataKey="vol_mult" fill="#d4d4d4" isAnimationActive={false} />
                <Line yAxisId="ret" type="monotone" dataKey="cum" stroke="#1d4ed8" strokeWidth={2} dot={false} isAnimationActive={false} />
                {points.filter((p) => p.flagged).map((p) => (
                  <ReferenceDot key={p.t} yAxisId="ret" x={p.t} y={p.cum} r={4} fill="#f59e0b" stroke="none" />
                ))}
                {data.moments.map((m) => {
                  const p = nearest(points, m.t0_ms);
                  return p ? (
                    <ReferenceDot key={m.rank} yAxisId="ret" x={p.t} y={p.cum} r={9} fill="#1d4ed8" stroke="#fff" strokeWidth={1.5}
                      label={{ value: String(m.rank), fill: "#fff", fontSize: 10, fontWeight: 600 }} onClick={() => seek(m.t0_ms)} />
                  ) : null;
                })}
                <ReferenceLine yAxisId="ret" x={cursorMs} stroke="#171717" strokeDasharray="4 3" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="text-xs text-neutral-500 mt-1">
            Grey band is the call. Orange dots are abnormal minutes. Numbered dots are the ranked moments, each with its 5-min window shaded by the sign of the adjusted return. Click anywhere to move the cursor.
          </div>
          <div className="hidden lg:block text-xs text-neutral-400 mt-0.5">
            Keys: j / k next and previous moment, 1 to 5 jump to a rank, [ / ] previous and next turn{audioOk ? ", space plays or pauses audio" : ""}.
          </div>
        </section>
      )}

      {data.call.audio_url && audioOk && (
        <audio ref={audioRef} controls src={data.call.audio_url} className="w-full mb-4" onError={() => setAudioOk(false)}
          onTimeUpdate={(e) => setCursorMs(Math.round(e.currentTarget.currentTime * 1000))} />
      )}
      {noAudio && <p className="text-xs text-neutral-500 mb-4">Audio is unavailable, click the chart or transcript to move the cursor.</p>}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <section className="lg:col-span-2 border border-neutral-200 rounded-lg p-3">
          <h2 className="text-sm font-medium mb-1">Moments, ranked</h2>
          <p className="text-xs text-neutral-500 mb-2">Statements made just before an abnormal minute, ranked by novelty, size of the move and timing. Associations, not proof.</p>
          {data.moments.length === 0 ? (
            <p className="text-sm text-neutral-500">No minute cleared the threshold during the call, {data.candidate_seg_ids.length} turns read.</p>
          ) : (
            <ol className="space-y-2">
              {data.moments.map((m) => (
                <MomentCard key={m.rank} m={m} offset={offset} atOpen={!!data.open_reaction} active={active?.seg_id === m.seg_id} onSelect={() => {
                  seek(m.t0_ms);
                  if (window.innerWidth < 1024) document.getElementById("transcript")?.scrollIntoView({ behavior: "smooth" }); // panels stack on phones
                }} />
              ))}
            </ol>
          )}
        </section>

        <section className="lg:col-span-3 border border-neutral-200 rounded-lg p-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
            <h2 id="transcript" className="text-sm font-medium">Transcript</h2>
            <input
              type="search" value={query} placeholder="Search the transcript" aria-label="Search the transcript"
              onChange={(e) => { setQuery(e.target.value); setHit(0); }} onKeyDown={onSearchKey}
              className="ml-auto w-48 rounded border border-neutral-300 px-2 py-0.5 text-xs placeholder:text-neutral-400 focus:outline-none focus:ring-1 focus:ring-blue-300"
            />
            {query.trim() && <span className="text-xs text-neutral-500 tabular w-16">{hits.length ? `${hit + 1} of ${hits.length}` : "0 hits"}</span>}
          </div>
          <TranscriptList
            segments={data.segments} activeId={active?.seg_id ?? null} momentSegs={new Set(data.moments.map((m) => m.seg_id))}
            gutter={gutter} offset={offset} query={query.trim()} onSelect={(s) => seek(s.t0_ms)}
          />
        </section>
      </div>
    </div>
  );
}

// reported-quarter consensus under the header, colored by beat or miss, captioned with when the consensus was current
function ConsensusStrip({ estimates }: { estimates: Estimate[] }) {
  if (estimates.length === 0) return null;
  const money = (e: Estimate, x: number) => (e.metric === "eps" ? `$${x.toFixed(2)}` :
      x >= 1e9 ? `$${(x / 1e9).toFixed(1)}B` : `$${(x / 1e6).toFixed(0)}M`);
  const day = (s: string) => new Date(s).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  return (
    <div className="flex flex-wrap gap-x-2 text-xs text-neutral-600 tabular mb-1">
      {estimates.map((e, i) => (
        <span key={e.metric}>
          {i > 0 && "· "}
          {e.metric === "eps" ? "EPS" : "Revenue"}{" "}
          {e.actual != null && e.surprise_pct != null ? (
            <span className={DIR_TEXT[dir(e.surprise_pct)]}>
              {money(e, e.actual)} vs {money(e, e.consensus)} est ({pct(e.surprise_pct, 1)})
            </span>
          ) : (
            <>{money(e, e.consensus)} est</>
          )}{" "}
          <span className="text-neutral-400">consensus as of {day(e.as_of)}, {e.basis.replace("_", " ")}</span>
        </span>
      ))}
    </div>
  );
}

function MomentCard({ m, offset, atOpen, active, onSelect }: { m: Moment; offset: number; atOpen: boolean; active: boolean; onSelect: () => void }) {
  const d = dir(m.adj_ret_5m);
  return (
    <li>
      <button onClick={onSelect} className={`w-full text-left rounded-md border p-2.5 hover:bg-neutral-50 ${active ? "border-blue-600 bg-blue-50/40" : "border-neutral-200"}`}>
        <div className="flex gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2 text-xs text-neutral-500">
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-blue-700 text-white font-semibold">{m.rank}</span>
              <span className="tabular">{clock(m.ts)} ET · {mmss(m.t0_ms - offset)}</span>
              <span className="font-medium text-neutral-700">{shortSpeaker(m.speaker)}</span>
              <span className="rounded bg-neutral-100 px-1.5 py-0.5">{m.kind}</span>
              <span className={`rounded px-1.5 py-0.5 ${m.novel ? "bg-blue-100 text-blue-800" : "bg-neutral-100 text-neutral-600"}`}>
                {m.novel ? "new" : `in ${m.matched_doc === "release" ? "release" : "prior call"}`}
              </span>
              {m.vs_consensus_pct != null && (
                <span className={`rounded bg-neutral-100 px-1.5 py-0.5 tabular ${DIR_TEXT[dir(m.vs_consensus_pct)]}`}>
                  vs consensus {pct(m.vs_consensus_pct, 1)}
                </span>
              )}
            </div>
          </div>
          <div className="shrink-0 text-right tabular">
            <div className={`text-base font-semibold ${DIR_TEXT[d]}`}>{GLYPH[d]} {pct(m.adj_ret_5m)}</div>
            <div className="text-[11px] text-neutral-500 leading-tight">adj. {atOpen ? "at next open" : "next 5 min"}<br />{pct(m.ret_5m)} raw, {pct(m.etf_ret_5m)} sector</div>
          </div>
        </div>
        <p className="mt-1.5 text-sm leading-snug">&ldquo;{m.quote}&rdquo;</p>
        <div className="mt-1.5 text-xs tabular flex flex-wrap gap-x-3 gap-y-0.5 text-neutral-600">
          <span>{m.vol_mult.toFixed(1)}x volume</span>
          {m.nearest_flag_min >= 0 && <span className="text-neutral-500">abnormal minute {m.nearest_flag_min === 0 ? "at" : `+${m.nearest_flag_min.toFixed(0)} min after`} the statement</span>}
          {m.pre_move && <span className="text-amber-700">move began earlier</span>}
          <span className={`ml-auto rounded px-1.5 py-0.5 ${CONF[m.confidence]}`}>{m.confidence} confidence</span>
        </div>
      </button>
    </li>
  );
}

function TranscriptList({ segments, activeId, momentSegs, gutter, offset, query, onSelect }: {
  segments: Segment[]; activeId: number | null; momentSegs: Set<number>; gutter: Map<number, number | null>; offset: number;
  query: string; onSelect: (s: Segment) => void;
}) {
  const listRef = useRef<HTMLOListElement>(null);
  const activeRef = useRef<HTMLLIElement>(null);
  // scroll the list, not the page, so the chart stays in view
  useEffect(() => {
    const list = listRef.current, el = activeRef.current;
    if (!list || !el) return;
    list.scrollTo({ top: el.offsetTop - list.clientHeight / 2 + el.clientHeight / 2, behavior: "smooth" });
  }, [activeId]);
  return (
    <ol ref={listRef} className="relative max-h-[32rem] overflow-y-auto pr-1 space-y-1">
      {segments.map((s) => {
        const isActive = s.seg_id === activeId;
        const ret = gutter.get(s.seg_id);
        return (
          <li key={s.seg_id} ref={isActive ? activeRef : undefined}>
            <button onClick={() => onSelect(s)} className={`flex w-full gap-2 text-left rounded px-2 py-1.5 text-sm hover:bg-neutral-50 ${isActive ? "bg-blue-50 ring-1 ring-blue-300" : ""}`}>
              <span className="w-12 shrink-0 text-right text-[11px] text-neutral-400 tabular leading-5" title={ret != null ? "adjusted return over the next 5 min" : undefined}>
                {ret != null ? pct(ret) : ""}
              </span>
              <span className="min-w-0">
                <span className="text-xs text-neutral-500 tabular mr-2">{mmss(s.t0_ms - offset)}</span>
                <span className={`text-xs font-medium mr-2 ${momentSegs.has(s.seg_id) ? "text-blue-700" : "text-neutral-700"}`}>{shortSpeaker(s.speaker)}</span>
                <span className="text-neutral-800">{highlight(s.text, query)}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

// wraps every case-insensitive occurrence of q in <mark>
function highlight(text: string, q: string): React.ReactNode {
  if (!q) return text;
  const parts = text.split(new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"));
  return parts.map((p, i) => (i % 2 ? <mark key={i} className="bg-yellow-200 rounded-sm">{p}</mark> : p));
}

// adjusted return from the first bar at or after each candidate turn to 5 min later, null when the bars run out
function segmentReturns(data: CallOutput, start: number): Map<number, number | null> {
  const bars = data.bars.map((b) => ({ t: new Date(b.ts).getTime() - start, px: b.px, etf: b.etf_px }));
  const at = (ms: number) => bars.find((b) => b.t >= ms);
  const out = new Map<number, number | null>();
  for (const id of data.candidate_seg_ids) {
    const s = data.segments.find((x) => x.seg_id === id);
    const b0 = s && at(s.t0_ms), b1 = s && at(s.t0_ms + WINDOW_MS);
    out.set(id, b0 && b1 ? b1.px / b0.px - 1 - (b1.etf / b0.etf - 1) : null);
  }
  return out;
}

function activeSegment(segments: Segment[], ms: number): Segment | null {
  let best: Segment | null = null;
  for (const s of segments) if (s.t0_ms <= ms) best = s; else break;
  return best;
}

function nearest(points: Point[], ms: number): Point | null {
  if (points.length === 0) return null;
  let best = points[0];
  for (const p of points) if (Math.abs(p.t - ms) < Math.abs(best.t - ms)) best = p;
  return best;
}

function clamp(x: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, x));
}

function ticks(points: Point[], start: number): number[] {
  if (points.length === 0) return [];
  const step = 15 * 60_000;
  const first = Math.ceil((start + points[0].t) / step) * step - start;
  const out: number[] = [];
  for (let t = first; t <= points[points.length - 1].t; t += step) out.push(t);
  return out;
}
