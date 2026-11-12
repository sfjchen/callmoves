// Replay view for 1 call (client component): price chart, audio player, ranked
// moments and transcript on 1 shared cursor.
"use client";

import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import {
  Bar, ComposedChart, Line, ReferenceArea, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { CallOutput, Estimate, Moment, Segment } from "@/lib/types";
import {
  clock, dir, DIR_HEX, DIR_TEXT, mmss, pct, shortQuarter, shortSpeaker, SPILL_LABEL, TZ, WINDOW_MS,
} from "@/lib/format";
import Comps from "@/components/comps";
import { benchName, clip } from "@/lib/share";

const MAX_TICKS = 40;  // more search matches than this would fill the chart with ticks, so it shows none
const BEFORE_MS = 45 * 60_000; // chart window: 45 min before the call ...
const AFTER_MS = 30 * 60_000;  // ... to 30 min after it ends
const VOL_AXIS_MAX = 12;       // volume multiples above this are block prints, let them clip
const GLYPH = { up: "▲", down: "▼", flat: "" };

type Point = { t: number; cum: number; vol_mult: number; flagged: boolean };

/**
 * Records a guess in the cm_guesses cookie (call id -> right) unless the call was guessed before, and returns whether
 * it counted plus the right and total counts. It lives outside the component because React's rules forbid writing
 * document.cookie during a component's own code.
 */
function recordGuess(id: string, right: boolean): { scored: boolean; k: number; n: number } {
  let prev: Record<string, boolean> = {};
  try {
    prev = JSON.parse(decodeURIComponent(document.cookie.match(/(?:^|; )cm_guesses=([^;]*)/)?.[1] ?? "{}"));
  } catch { /* a malformed cookie starts the score over */ }
  const scored = !(id in prev);
  const s = scored ? { ...prev, [id]: right } : prev;
  document.cookie = `cm_guesses=${encodeURIComponent(JSON.stringify(s))}; path=/; max-age=31536000; samesite=lax`;
  return { scored, k: Object.values(s).filter(Boolean).length, n: Object.keys(s).length };
}

/**
 * Renders the replay view: header, price chart, audio player, ranked moments and transcript. All times are in ms
 * since audio t=0, which is the call start minus audio_offset_ms, so wall clock = start + ms. `baseline` (study
 * condition) hides the moments panel, the scoring explanation and the numbered markers. `initialRank` (from a shared
 * ?m= link) is the moment selected on load. Search and the Q&A band come from the transcript alone, so they stay in
 * the baseline view. `guess` (?guess=1) gets a call cut at its top moment and hides what baseline hides until the
 * reader answers, then fetches the full call and shows it in place with the answer and a score kept in the
 * cm_guesses cookie (call id -> right), which /guess reads to skip calls already guessed.
 */
export default function Replay({ data: initial, baseline = false, initialRank, guess }: {
  data: CallOutput; baseline?: boolean; initialRank?: number;
  guess?: { ms: number; quote: string; speaker: string; bench: string };
}) {
  const [full, setFull] = useState<CallOutput | null>(null); // the whole call, fetched once the guess is in
  const [result, setResult] = useState<({ right: boolean } & ReturnType<typeof recordGuess>) | null>(null);
  const [guessState, setGuessState] = useState<"ask" | "busy" | "error">("ask");
  const asking = useRef(false);
  const data = full ?? initial;
  const hide = baseline || (!!guess && !full);
  const offset = data.call.audio_offset_ms ?? 0;
  const start = useMemo(() => new Date(data.call.call_start).getTime() - offset, [data.call.call_start, offset]);
  const endMs = new Date(data.call_end).getTime() - start;
  const [cursorMs, setCursorMs] = useState(guess?.ms ??
    (data.moments.find((m) => m.rank === initialRank) ?? data.moments[0])?.t0_ms ?? offset);
  const [audioOk, setAudioOk] = useState<boolean | null>(null); // null until the HEAD check on the Supabase URL answers
  useEffect(() => {
    const url = data.call.audio_url;
    if (!url) return;
    fetch(url, { method: "HEAD" }).then((r) => setAudioOk(r.ok)).catch(() => setAudioOk(false));
  }, [data.call.audio_url]);
  const audioRef = useRef<HTMLAudioElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [hit, setHit] = useState(-1); // index into hits of the last hit jumped to, -1 before the first jump

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
  // filtering waits for typing to pause, and 1 letter matches nearly every turn, so search starts at 2
  const q = useDeferredValue(query.trim().toLowerCase());
  const hits = useMemo(() => q.length < 2 ? [] : data.segments.filter((s) => s.text.toLowerCase().includes(q)),
    [data.segments, q]);
  const qaMs = data.qa_start_ms != null && data.qa_start_ms < endMs ? data.qa_start_ms : null; // null: no Q&A band
  const flaggedCount = data.bars.filter((b) => b.flagged).length;
  const est = (data.estimates ?? []).filter((e) => e.role === "reported");
  const lead = data.moments[0];
  const benchLabel = benchName(data);

  // Moves the cursor to a moment, keeps it in the URL as ?m=<rank> without a reload so the link can be shared, and
  // on phones where the panels stack scrolls to the transcript.
  function select(m: Moment) {
    seek(m.t0_ms);
    const u = new URL(window.location.href);
    u.searchParams.set("m", String(m.rank));
    window.history.replaceState(null, "", u);
    if (window.innerWidth < 1024) document.getElementById("transcript")?.scrollIntoView({ behavior: "smooth" });
  }

  // Keyboard: j/k moments, 1 to 5 by rank, [ ] transcript turns, space audio, / search. Inputs keep their keys.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      if (
        e.metaKey || e.ctrlKey || e.altKey ||
        (el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable))
      ) return;
      if (e.key === "/") {
        e.preventDefault();
        searchRef.current?.focus();
        return;
      }
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

  // Moves the cursor, audio and transcript to search hit i, wrapping around at either end.
  function jump(i: number) {
    const n = hits.length;
    setHit((i + n) % n);
    seek(hits[(i + n) % n].t0_ms);
  }

  // Enter and Shift+Enter cycle the search hits, starting at the first or last. Esc clears the search, then leaves it.
  function onSearchKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      if (query) { setQuery(""); setHit(-1); } else e.currentTarget.blur();
      return;
    }
    if (e.key !== "Enter" || hits.length === 0) return;
    e.preventDefault();
    jump(hit < 0 ? (e.shiftKey ? -1 : 0) : hit + (e.shiftKey ? -1 : 1));
  }

  const noAudio = !data.call.audio_url || audioOk === false;

  // Fetches the full call, scores the guess unless this call was guessed before, and reveals the rest in place.
  async function answer(up: boolean) {
    if (asking.current) return; // a 2nd click before the re-render would score the call twice
    asking.current = true;
    setGuessState("busy");
    try {
      const r = await fetch(`/api/calls/${data.summary.id}`);
      if (!r.ok) throw new Error(`status ${r.status}`);
      const c = (await r.json()) as CallOutput;
      const right = up === c.moments[0].adj_ret_5m > 0;
      setResult({ right, ...recordGuess(c.summary.id, right) });
      setFull(c);
    } catch {
      asking.current = false;
      setGuessState("error");
    }
  }

  return (
    <div className="max-w-7xl">
      <div
        className={
          "lg:hidden sticky top-0 z-10 -mx-4 sm:-mx-6 -mt-5 mb-3 px-4 sm:px-6 py-1.5 bg-page/95 " +
          "border-b border-line text-xs tabular text-neutral-700"
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

      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl leading-tight">
          {data.call.ticker} <span className="text-muted">{shortQuarter(data.call.quarter)}</span>
        </h1>
        {data.call.company && <span className="text-sm text-muted">{data.call.company}</span>}
        <span className="text-sm text-muted tabular">
          {new Date(data.call.call_start).toLocaleDateString("en-US", { month: "short", day: "numeric",
            year: "numeric", timeZone: TZ })}, {clock(data.call.call_start)} to {clock(data.call_end)} ET
        </span>
        {est.length > 0 && (
          <span className="text-xs text-muted tabular">
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

      {guess && (
        <section className="card mt-2 mb-3 px-3 py-2.5 border-accent" aria-label="Guess the move">
          {!result ? (
            <>
              <p className="text-[15px] leading-snug">
                <span className="font-semibold">Guess the move.</span> At {clock(new Date(start + guess.ms))} ET{" "}
                {shortSpeaker(guess.speaker)} said &ldquo;{clip(guess.quote, 160)}&rdquo;. Over the next 5
                minutes, did {data.call.ticker} beat or trail {guess.bench}?
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
                {([[true, "▲ Beat"], [false, "▼ Trail"]] as const).map(([up, label]) => (
                  <button key={label} onClick={() => answer(up)} disabled={guessState === "busy"}
                    className="rounded-full bg-accent px-3 py-0.5 text-white disabled:opacity-50">{label}</button>
                ))}
                <span className="text-xs text-muted">{guessState === "error" ?
                  "Could not load the answer, try again." : "The chart and transcript stop here."}</span>
              </div>
            </>
          ) : (
            <p className="text-[15px] leading-snug">
              <span className="font-semibold">{result.right ? "Right." : "Wrong."}</span> {data.call.ticker}{" "}
              {lead.adj_ret_5m > 0 ? "beat" : "trailed"} {guess.bench} by{" "}
              <span className={`font-semibold tabular ${DIR_TEXT[dir(lead.adj_ret_5m)]}`}>
                {GLYPH[dir(lead.adj_ret_5m)]} {(Math.abs(lead.adj_ret_5m) * 100).toFixed(2)}%</span> over the next
              5 minutes.{" "}
              {result.scored ? "" : "Guessed before, so not scored. "}Score {result.k} of {result.n}.{" "}
              <a href="/guess" className="text-accent underline underline-offset-2">
                Next call</a>
            </p>
          )}
        </section>
      )}

      {!hide && (
        <section className="card mt-2 mb-3 px-3 py-2.5" aria-label="Top moment">
          {lead ? (
            <p className="text-[15px] leading-snug">
              <span className="font-semibold">Top moment {clock(lead.ts)} ET:</span>{" "}
              <span className={`font-semibold tabular ${DIR_TEXT[dir(lead.adj_ret_5m)]}`}>
                {GLYPH[dir(lead.adj_ret_5m)]} {pct(lead.adj_ret_5m)}</span>{" "}
              vs {benchLabel} in 5 min on {lead.vol_mult.toFixed(1)}x volume
              {lead.spillover && <span className="text-muted"> ({SPILL_LABEL[lead.spillover]})</span>}, after{" "}
              {shortSpeaker(lead.speaker)} said &ldquo;{clip(lead.quote, 160)}&rdquo;
            </p>
          ) : (
            <p className="text-sm text-muted">No minute cleared the abnormal-move threshold during the call.</p>
          )}
          {data.moments.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
              {data.moments.map((m) => (
                <button key={m.rank} onClick={() => select(m)}
                  className={`tabular rounded-full border px-2 py-0.5 hover:bg-white ${active?.seg_id === m.seg_id ?
                    "border-accent bg-white" : "border-line"}`}>
                  <span className="font-semibold text-accent">{m.rank}</span> {clock(m.ts)}{" "}
                  <span className={DIR_TEXT[dir(m.adj_ret_5m)]}>{pct(m.adj_ret_5m)}</span>
                </button>
              ))}
              {audioOk && lead && (
                <button onClick={() => { select(lead); void audioRef.current?.play(); }}
                  className="rounded-full bg-accent px-2.5 py-0.5 text-white">▶ Play #1</button>
              )}
              <span className="hidden lg:inline ml-auto text-muted">
                <kbd>j</kbd> <kbd>k</kbd> next and previous moment · <kbd>1</kbd>-<kbd>5</kbd> rank
                {audioOk && <> · <kbd>space</kbd> play</>}
              </span>
            </div>
          )}
        </section>
      )}

      {data.open_reaction ? (
        <section className="card p-3 mb-4 text-sm">
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
          <div className="text-xs text-muted mt-1">
            Every statement shares this one move, so moments are ranked by novelty and kind only, all at low confidence.
          </div>
        </section>
      ) : (
        <section className="card p-3 mb-4">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 mb-1">
            <div className="text-sm font-medium">
              Return vs {benchLabel} (%, cumulative from{" "}
              {clock(new Date(start + (points[0]?.t ?? 0)))}) and volume multiple
            </div>
            <div className="hidden lg:block text-sm text-muted tabular">
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
                  const p = s?.activeTooltipIndex != null ? points[Number(s.activeTooltipIndex)] : undefined;
                  if (p) seek(clamp(p.t, offset, endMs));
                }}
                style={{ cursor: "pointer" }}
                // mousemove unthrottled: a tap's compat mousemove sets the minute before its click reads it
                throttledEvents={["touchmove", "pointermove", "scroll", "wheel"]}
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
                {qaMs != null && (
                  <ReferenceArea
                    yAxisId="ret" x1={qaMs} x2={endMs} fill="#e8eefc" fillOpacity={0.9}
                    label={{ value: `Q&A ${clock(new Date(start + qaMs))}`, position: "insideTopLeft", fill: "#737373",
                      fontSize: 10 }}
                  />
                )}
                {qaMs != null && <ReferenceLine yAxisId="ret" x={qaMs} stroke="#737373" strokeWidth={1} />}
                {data.moments.map((m) => (
                  <ReferenceArea
                    key={m.rank} yAxisId="ret" x1={m.t0_ms} x2={m.t0_ms + WINDOW_MS} fill={DIR_HEX[dir(m.adj_ret_5m)]}
                    fillOpacity={0.15}
                  />
                ))}
                <Bar yAxisId="vol" dataKey="vol_mult" fill="#d4d4d4" isAnimationActive={false} />
                <Line
                  yAxisId="ret" type="monotone" dataKey="cum" stroke="#171717" strokeWidth={1.75} dot={false}
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
                {hits.length <= MAX_TICKS && hits.map((s, i) => (
                  <ReferenceLine
                    key={s.seg_id} yAxisId="vol" segment={[{ x: s.t0_ms, y: 0 }, { x: s.t0_ms, y: 1.5 }]}
                    stroke={i === hit ? "#171717" : "#737373"} strokeWidth={i === hit ? 2 : 1.25}
                  />
                ))}
                <ReferenceLine yAxisId="ret" x={cursorMs} stroke="#171717" strokeDasharray="4 3" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <div className="text-xs text-muted mt-1">
            {hide ? "Grey band is the call" :
              "Grey band is the call, orange dots abnormal minutes, " +
                "numbered dots moments with their 5-min window shaded"}
            {hits.length > 0 && hits.length <= MAX_TICKS && ", grey ticks at the bottom search matches"}.
            {" "}Click or tap the chart to jump there.
            <span className="hidden lg:inline cursor-help ml-1 underline decoration-dotted"
              title={`j / k: next and previous moment. 1 to 5: jump to a rank. [ / ]: previous and next turn.${audioOk ?
                " Space: play or pause." : ""} /: search, Esc clears it.`}>?</span>
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
        <p className="text-xs text-muted mb-4">
          Audio is unavailable, click the chart or transcript to move the cursor.
        </p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {!hide && <section className="lg:col-span-2 card p-3">
          <h2 className="text-lg mb-1">Moments, ranked</h2>
          {data.moments.length === 0 ? (
            <p className="text-sm text-muted">
              No minute cleared the threshold during the call, {data.candidate_seg_ids.length} turns read.
            </p>
          ) : (
            <ol className="space-y-2">
              {data.moments.map((m) => (
                <MomentCard
                  key={m.rank} m={m} atOpen={!!data.open_reaction} active={active?.seg_id === m.seg_id}
                  onSelect={() => select(m)}
                />
              ))}
            </ol>
          )}
          {data.context && !data.open_reaction && (
            <Comps ctx={data.context} ticker={data.call.ticker} start={data.call.call_start} end={data.call_end}
              moment={data.moments.find((m) => m.seg_id === active?.seg_id) ?? lead} />
          )}
        </section>}

        <section className={`${hide ? "lg:col-span-5" : "lg:col-span-3"} card p-3`}>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
            <h2 id="transcript" className="text-lg">Transcript</h2>
            <input
              ref={searchRef} type="search" value={query} placeholder="Search the transcript ( / )"
              aria-label="Search the transcript" onChange={(e) => { setQuery(e.target.value); setHit(-1); }}
              onKeyDown={onSearchKey}
              className={
                "ml-auto w-48 rounded border border-line px-2 py-0.5 text-xs placeholder:text-neutral-400 " +
                "focus:outline-none focus:ring-1 focus:ring-accent/40"
              }
            />
            {q.length >= 2 && <span className="flex items-center gap-1 text-xs text-muted tabular">
              <span aria-live="polite">{hits.length === 0 ? "no match" : hit >= 0 ? `${hit + 1} of ${hits.length}` :
                `${hits.length} match${hits.length === 1 ? "" : "es"}`}</span>
              {hits.length > 0 && <>
                <button aria-label="Previous match" onClick={() => jump(hit < 0 ? -1 : hit - 1)}
                  className="rounded border border-line px-1.5 hover:bg-white">↑</button>
                <button aria-label="Next match" onClick={() => jump(hit + 1)}
                  className="rounded border border-line px-1.5 hover:bg-white">↓</button>
              </>}
            </span>}
          </div>
          <TranscriptList
            segments={data.segments} activeId={active?.seg_id ?? null}
            momentSegs={new Set(data.moments.map((m) => m.seg_id))}
            offset={offset} query={q.length < 2 ? "" : q} onSelect={(s) => seek(s.t0_ms)}
          />
        </section>
      </div>
      {!hide && <details className="mt-3 text-xs text-muted">
        <summary className="cursor-pointer w-fit underline underline-offset-2 hover:text-ink">
          How moments are found</summary>
        <ul className="mt-1 max-w-3xl space-y-0.5 tabular">
          <li>Price first: {flaggedCount} abnormal minutes → {data.candidate_seg_ids.length} turns spoken in the 5 min
            before them → {data.n_claims} claims ({data.llm_used ? "Claude" : "rule-based"}) →{" "}
            {data.moments.length}{" "}
            moments, 1 per price event.</li>
          <li>Abnormal = 1-minute return vs {benchLabel} far outside its trailing 20 minutes, or 3x volume with a
            0.2% move.{data.context?.benchmark !== "etf" && data.context?.r2 != null &&
              ` Betas fitted on the 3 sessions before the call, R² ${data.context.r2.toFixed(2)}.`}</li>
          <li>Score = novelty × kind × |5-min adjusted return| × volume multiple × closeness to the minute. An EPS or
            revenue figure over 1% off consensus counts as new.</li>
          <li>Each card counts the call&apos;s other 5-min windows (regular session, no opening auction) that moved
            as much. Moments are picked for big moves, so a low count is expected: it says how unusual, not why.</li>
          <li>&ldquo;Move began earlier&rdquo; means the price may have shaped the statement. Associations, not
            causes.</li>
          {data.stats?.gpu_usd != null && data.stats.claude_usd != null && <li>
            Processed {data.stats.audio_min} min of audio in{" "}
            {Math.round(((data.stats.ingest_s ?? 0) + (data.stats.claude_s ?? 0)) / 60)} min for $
            {(data.stats.gpu_usd + data.stats.claude_usd).toFixed(2)} (GPU ${data.stats.gpu_usd.toFixed(2)}, Claude $
            {data.stats.claude_usd.toFixed(2)}).</li>}
        </ul>
      </details>}
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
 * Renders 1 ranked moment: rank, time, quote and adjusted return, with the evidence and a Copy link button shown only
 * while it is active. Low-confidence moments are dimmed.
 */
function MomentCard({ m, atOpen, active, onSelect }: {
  m: Moment; atOpen: boolean; active: boolean; onSelect: () => void;
}) {
  const d = dir(m.adj_ret_5m);
  const [copied, setCopied] = useState(false);
  // Copies the moment's link without the study parameters, or shows it in a prompt where the clipboard is blocked.
  function copy() {
    const url = `${window.location.origin}${window.location.pathname}?m=${m.rank}`;
    // navigator.clipboard is undefined outside a secure context, such as plain http on a LAN address
    (navigator.clipboard?.writeText(url) ?? Promise.reject(new Error("no clipboard")))
      .then(() => setCopied(true), () => window.prompt("Copy this link", url));
  }
  return (
    <li className="relative">
      <button onClick={onSelect} className={`w-full text-left rounded-md border p-2.5 hover:bg-white ${active ?
        "border-accent bg-white" : `border-line ${m.confidence === "low" ? "opacity-60" : ""}`}`}>
        <div className="flex items-center gap-2 text-xs text-muted">
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-accent text-white
            font-semibold">{m.rank}</span>
          <span className="tabular">{clock(m.ts)} ET</span>
          <span className="rounded bg-line/70 px-1.5 py-0.5">{m.section === "qa" ? "Q&A" : "prepared"}</span>
          {m.spillover && <span className="rounded border border-line px-1.5 py-0.5">
            {SPILL_LABEL[m.spillover]}</span>}
          <span className={`ml-auto text-base font-semibold tabular ${DIR_TEXT[d]}`}>
            {GLYPH[d]} {pct(m.adj_ret_5m)}</span>
        </div>
        <p className="mt-1.5 text-sm leading-snug">&ldquo;{m.quote}&rdquo;</p>
        {active && (
          <div className="mt-2 space-y-0.5 text-xs text-muted tabular">
            {m.question && <p className="text-muted">
              <span className="font-medium">Analyst asked:</span> {m.question}</p>}
            <p>
              <span className="font-medium">{shortSpeaker(m.speaker)}</span>, {m.vol_mult.toFixed(1)}x volume, adj.{" "}
              {atOpen ? "at next open" : "next 5 min"}: {pct(m.ret_5m)} raw vs{" "}
              {pct(m.bench_ret_5m ?? m.etf_ret_5m)}{" "}
              {m.bench_ret_5m != null && m.bench_ret_5m !== m.etf_ret_5m ? "benchmark" : "sector"}
              {m.peer_ret_5m != null && <>, peers {pct(m.peer_ret_5m)}</>}
            </p>
            <p>
              {m.novel ? "New" : `Already in the ${m.matched_doc === "release" ? "release" : "prior call"}`}
              {m.vs_consensus_pct != null && <>, <span className={DIR_TEXT[dir(m.vs_consensus_pct)]}>
                {pct(m.vs_consensus_pct, 1)} vs consensus</span></>}
              {m.nearest_flag_min >= 0 && `, abnormal minute ${m.nearest_flag_min === 0 ? "at" :
                `+${m.nearest_flag_min.toFixed(0)} min after`} the statement`}
            </p>
            {m.placebo_n != null && m.placebo_k != null && <p>
              {m.placebo_k === 0 ? `Bigger than all ${m.placebo_n} other 5-min windows of the call`
                : `${m.placebo_k} of ${m.placebo_n} other 5-min windows of the call moved as much`}</p>}
            <p>
              {m.pre_move && <span className="text-amber-700">Move began earlier. </span>}
              {m.confidence} confidence
            </p>
          </div>
        )}
      </button>
      {active && (
        <button onClick={copy} onBlur={() => setCopied(false)}
          className="absolute bottom-2.5 right-2.5 text-xs text-accent hover:underline underline-offset-2">
          {copied ? "Copied" : "Copy link"}</button>
      )}
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
                "flex w-full gap-2 text-left rounded px-2 py-1.5 text-sm hover:bg-white " +
                (isActive ? "bg-white ring-1 ring-accent/40" : "")
              }
            >
              <span
                className="w-12 shrink-0 text-right text-[11px] text-neutral-400 tabular leading-5"
                title={ret != null ? "adjusted return over the next 5 min" : undefined}
              >
                {ret != null ? pct(ret) : ""}
              </span>
              <span className="min-w-0">
                <span className="text-xs text-muted tabular mr-2">{mmss(s.t0_ms - offset)}</span>
                <span
                  className={
                    "text-xs font-medium mr-2 " +
                    (momentSegs.has(s.seg_id) ? "text-accent" : "text-neutral-700")
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
