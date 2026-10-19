"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar, ComposedChart, Line, ReferenceArea, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { CallOutput, Moment, Segment } from "@/lib/types";
import { clock, mmss, pct, shortSpeaker } from "@/lib/format";

const BEFORE_MS = 45 * 60_000; // chart window: 45 min before the call ...
const AFTER_MS = 30 * 60_000;  // ... to 30 min after it ends
const VOL_AXIS_MAX = 12;       // volume multiples above this are block prints, let them clip
const CONF = { high: "bg-emerald-100 text-emerald-800", medium: "bg-amber-100 text-amber-800", low: "bg-neutral-100 text-neutral-600" };

type Point = { t: number; cum: number; vol_mult: number; flagged: boolean };

export default function Replay({ data }: { data: CallOutput }) {
  const start = useMemo(() => new Date(data.call.call_start).getTime(), [data.call.call_start]);
  const endMs = new Date(data.call_end).getTime() - start;
  const [cursorMs, setCursorMs] = useState(data.moments[0]?.t0_ms ?? 0);
  const [audioOk, setAudioOk] = useState(true); // audio is local only, the deployed app has none
  const audioRef = useRef<HTMLAudioElement>(null);

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
        .filter((p) => p.t >= -BEFORE_MS && p.t <= endMs + AFTER_MS),
    [data.bars, start, endMs],
  );
  const cursorPoint = useMemo(() => nearest(points, cursorMs), [points, cursorMs]);
  const active = useMemo(() => activeSegment(data.segments, cursorMs), [data.segments, cursorMs]);
  const flaggedCount = data.bars.filter((b) => b.flagged).length;

  return (
    <div className="max-w-7xl">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 mb-3">
        <h1 className="text-xl font-semibold">
          {data.call.ticker} <span className="font-normal text-neutral-500">{data.call.quarter}</span>
        </h1>
        {data.call.company && <span className="text-sm text-neutral-600">{data.call.company}</span>}
        <span className="text-sm text-neutral-500 tabular">
          Call {clock(data.call.call_start)} to {clock(data.call_end)} ET, vs {data.call.etf}
        </span>
        <span className="text-xs rounded bg-neutral-100 px-2 py-0.5 text-neutral-600">
          {flaggedCount} abnormal minutes, {data.candidate_seg_ids.length} turns read, {data.claims.length} claims, {data.llm_used ? "Claude" : "rule-based"} extraction
        </span>
      </div>

      <section className="border border-neutral-200 rounded-lg p-3 mb-4">
        <div className="flex items-baseline justify-between mb-1">
          <div className="text-sm font-medium">Sector-adjusted return vs {data.call.etf} (%, cumulative from {clock(data.bars[0]?.ts ?? data.call.call_start)}) and volume multiple</div>
          <div className="text-sm text-neutral-600 tabular">
            cursor {clock(new Date(start + cursorMs))} ET ({mmss(cursorMs)} into call)
            {cursorPoint && <> · {pct(cursorPoint.cum / 100)} · {cursorPoint.vol_mult.toFixed(1)}x volume</>}
          </div>
        </div>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart
              data={points}
              margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
              onClick={(s) => { if (s && typeof s.activeLabel === "number") seek(clamp(s.activeLabel, 0, endMs)); }}
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
              <ReferenceArea yAxisId="ret" x1={0} x2={endMs} fill="#f5f5f5" fillOpacity={0.9} />
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
          Grey band is the call. Orange dots are abnormal minutes. Numbered dots are the ranked moments. Click anywhere to move the cursor.
        </div>
      </section>

      {data.call.audio_url && audioOk && (
        <audio ref={audioRef} controls src={data.call.audio_url} className="w-full mb-4" onError={() => setAudioOk(false)}
          onTimeUpdate={(e) => setCursorMs(Math.round(e.currentTarget.currentTime * 1000))} />
      )}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <section className="lg:col-span-2 border border-neutral-200 rounded-lg p-3">
          <h2 className="text-sm font-medium mb-1">Moments, ranked</h2>
          <p className="text-xs text-neutral-500 mb-2">Statements made just before an abnormal minute, ranked by novelty, size of the move and timing. Associations, not proof.</p>
          <ol className="space-y-2">
            {data.moments.map((m) => (
              <MomentCard key={m.rank} m={m} active={active?.seg_id === m.seg_id} onSelect={() => seek(m.t0_ms)} />
            ))}
          </ol>
        </section>

        <section className="lg:col-span-3 border border-neutral-200 rounded-lg p-3">
          <h2 className="text-sm font-medium mb-2">Transcript</h2>
          <TranscriptList segments={data.segments} activeId={active?.seg_id ?? null} momentSegs={new Set(data.moments.map((m) => m.seg_id))} onSelect={(s) => seek(s.t0_ms)} />
        </section>
      </div>
    </div>
  );
}

function MomentCard({ m, active, onSelect }: { m: Moment; active: boolean; onSelect: () => void }) {
  const up = m.adj_ret_5m >= 0;
  return (
    <li>
      <button onClick={onSelect} className={`w-full text-left rounded-md border p-2.5 hover:bg-neutral-50 ${active ? "border-blue-600 bg-blue-50/40" : "border-neutral-200"}`}>
        <div className="flex items-center gap-2 text-xs text-neutral-500 mb-1">
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-blue-700 text-white font-semibold">{m.rank}</span>
          <span className="tabular">{clock(m.ts)} ET · {mmss(m.t0_ms)}</span>
          <span className="font-medium text-neutral-700">{shortSpeaker(m.speaker)}</span>
          <span className="ml-auto rounded bg-neutral-100 px-1.5 py-0.5">{m.kind}</span>
          <span className={`rounded px-1.5 py-0.5 ${m.novel ? "bg-emerald-100 text-emerald-800" : "bg-neutral-100 text-neutral-600"}`}>
            {m.novel ? "new" : `in ${m.matched_doc === "release" ? "release" : "prior call"}`}
          </span>
        </div>
        <p className="text-sm leading-snug">&ldquo;{m.quote}&rdquo;</p>
        <div className="mt-1 text-xs tabular flex flex-wrap gap-x-3 gap-y-0.5">
          <span className={up ? "text-emerald-700 font-medium" : "text-red-700 font-medium"}>{pct(m.ret_5m)} next 5 min</span>
          <span className="text-neutral-600">vs {pct(m.etf_ret_5m)} sector, {pct(m.adj_ret_5m)} adjusted</span>
          <span className="text-neutral-600">{m.vol_mult.toFixed(1)}x volume</span>
          {m.nearest_flag_min >= 0 && <span className="text-neutral-500">abnormal minute {m.nearest_flag_min === 0 ? "at" : `+${m.nearest_flag_min.toFixed(0)} min after`} the statement</span>}
          <span className={`ml-auto rounded px-1.5 py-0.5 ${CONF[m.confidence]}`}>{m.confidence} confidence</span>
        </div>
      </button>
    </li>
  );
}

function TranscriptList({ segments, activeId, momentSegs, onSelect }: {
  segments: Segment[]; activeId: number | null; momentSegs: Set<number>; onSelect: (s: Segment) => void;
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
        return (
          <li key={s.seg_id} ref={isActive ? activeRef : undefined}>
            <button onClick={() => onSelect(s)} className={`w-full text-left rounded px-2 py-1.5 text-sm hover:bg-neutral-50 ${isActive ? "bg-blue-50 ring-1 ring-blue-300" : ""}`}>
              <span className="text-xs text-neutral-500 tabular mr-2">{mmss(s.t0_ms)}</span>
              <span className={`text-xs font-medium mr-2 ${momentSegs.has(s.seg_id) ? "text-blue-700" : "text-neutral-700"}`}>{shortSpeaker(s.speaker)}</span>
              <span className="text-neutral-800">{s.text}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
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
