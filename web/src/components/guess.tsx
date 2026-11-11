// Guess the move (client component): the price path, transcript and audio up to the top-ranked moment, an up or
// down guess vs the sector, then the reveal with the moment card and a running score kept in localStorage.
"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Moment } from "@/lib/types";
import { clock, dir, DIR_HEX, DIR_TEXT, pct, shortQuarter, shortSpeaker } from "@/lib/format";
import { MomentCard } from "@/components/replay";

export type GuessData = {
  id: string;
  ticker: string;
  quarter: string;
  company: string | null;
  etf: string;
  callStart: string;
  audioUrl: string | null;
  moment: Moment;
  pool: string[];                                        // call ids the next round can pick from
  bars: { min: number; cum: number; before: boolean }[]; // minutes from the statement, before = closed ahead of it
  turns: { speaker: string; text: string }[];            // transcript up to the statement's first word
};

type Score = { right: number; total: number; seen: string[] };

const KEY = "callmoves-guess";
const CLIP_MS = 30_000; // audio played before the statement
const STOP_EARLY_MS = 300; // timeupdate fires every 15 to 250 ms, so the clip stops this far ahead of the quote

/**
 * Stores the running score, silently skipped when storage is blocked.
 */
function saveScore(s: Score) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {}
}

/**
 * Renders 1 round: header and score, the chart rebased to 0 at the last minute before the statement, the transcript
 * tail and a 30-second audio clip, the Up and Down buttons, and after the answer the reveal.
 */
export default function Guess({ data }: { data: GuessData }) {
  const m = data.moment, d = dir(m.adj_ret_5m);
  const router = useRouter();
  const [score, setScore] = useState<Score>({ right: 0, total: 0, seen: [] });
  const [guess, setGuess] = useState<"up" | "down" | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [audioOk, setAudioOk] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);
  const revealRef = useRef<HTMLHeadingElement>(null);
  const turnsRef = useRef<HTMLOListElement>(null);

  // Reads the stored score after hydration, keeping the empty one when storage is blocked or holds something else.
  useEffect(() => {
    try {
      const s = JSON.parse(localStorage.getItem(KEY) ?? "null") as Score | null;
      // eslint-disable-next-line react-hooks/set-state-in-effect -- localStorage exists only after hydration
      if (s && Number.isFinite(s.right) && Number.isFinite(s.total) && Array.isArray(s.seen)) setScore(s);
    } catch {}
  }, []);
  useEffect(() => {
    if (!data.audioUrl) return;
    fetch(data.audioUrl, { method: "HEAD" }).then((r) => setAudioOk(r.ok)).catch(() => setAudioOk(false));
  }, [data.audioUrl]);
  useEffect(() => {
    const el = turnsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);
  useEffect(() => {
    if (guess) revealRef.current?.focus();
  }, [guess]);

  const base = [...data.bars].reverse().find((b) => b.before)?.cum ?? 0;
  const points = data.bars.filter((b) => guess || b.before).map((b) => ({ min: b.min, ret: (b.cum - base) * 100 }));
  const replayHref = `/calls/${data.id}?t=${Math.ceil(m.t0_ms / 1000)}`;
  const right = guess !== null && (guess === "up") === m.adj_ret_5m > 0;

  /**
   * Records the answer, updates and stores the score, and picks the next call, unseen ones first.
   */
  function answer(g: "up" | "down") {
    if (guess) return;
    audioRef.current?.pause();
    const ok = (g === "up") === m.adj_ret_5m > 0;
    const seen = [...new Set([...score.seen, data.id])];
    const s = { right: score.right + (ok ? 1 : 0), total: score.total + 1, seen };
    setScore(s);
    saveScore(s);
    setGuess(g);
    const fresh = data.pool.filter((id) => !seen.includes(id));
    const others = fresh.length ? fresh : data.pool.filter((id) => id !== data.id);
    setNext(others.length ? others[Math.floor(Math.random() * others.length)] : null);
  }

  /**
   * Plays the 30 seconds before the statement, stopped just ahead of it by onTimeUpdate.
   */
  function playClip() {
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = Math.max(0, m.t0_ms - CLIP_MS) / 1000;
    void a.play();
  }

  /**
   * Resets the score and the list of calls already played.
   */
  function resetScore() {
    const s = { right: 0, total: 0, seen: [] };
    setScore(s);
    saveScore(s);
  }

  return (
    <div className="mx-auto max-w-3xl">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-3xl leading-tight">Guess the move</h1>
        <span className="ml-auto text-sm tabular text-nb-muted" aria-live="polite">
          Score {score.right} / {score.total}
          {score.total > 0 && (
            <button onClick={resetScore} className="ml-2 text-xs text-nb-accent underline underline-offset-2">
              reset</button>
          )}
        </span>
      </div>
      <p className="mt-1 text-sm text-nb-muted">
        <span className="font-semibold text-nb-text">{data.ticker}</span> {shortQuarter(data.quarter)}
        {data.company && <> · {data.company}</>} · call at {clock(data.callStart)} ET. Below is everything up to
        the statement CallMoves ranked #{m.rank}, at {clock(m.ts)} ET.
      </p>

      <section className="card mt-3 p-3" aria-label="Price before the statement">
        <div className="text-sm font-medium">
          {data.ticker} vs {data.etf}, %, 0 = last minute before the statement
        </div>
        <div className="h-44 sm:h-56">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
              <XAxis dataKey="min" type="number" domain={["dataMin", "dataMax"]} fontSize={11}
                tickFormatter={(v: number) => (v > 0 ? `+${v}` : String(v))} allowDecimals={false} />
              <YAxis tickFormatter={(v: number) => `${v.toFixed(1)}%`} fontSize={11} width={48} />
              <Tooltip labelFormatter={(v) => `${Number(v).toFixed(0)} min from the statement`}
                formatter={(v) => [`${Number(v).toFixed(2)}%`, `vs ${data.etf}`]} />
              {guess && <ReferenceArea x1={0} x2={5} fill={DIR_HEX[d]} fillOpacity={0.15} />}
              <ReferenceLine x={0} stroke="#8c3838" strokeDasharray="4 3"
                label={{ value: "statement", position: "insideTopLeft", fontSize: 11, fill: "#8c3838" }} />
              <Line type="monotone" dataKey="ret" stroke="#1a1a1a" strokeWidth={1.75} dot={false}
                isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="text-xs text-nb-muted">
          Minutes from the statement.{guess && " Shaded: the 5 minutes after."}</div>
      </section>

      {data.audioUrl && audioOk && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button onClick={playClip} className="rounded-full bg-nb-accent px-3 py-1 text-sm text-white">
            ▶ Play the 30 s before</button>
          <audio ref={audioRef} src={data.audioUrl} preload="none" className="hidden"
            onError={() => setAudioOk(false)} onTimeUpdate={(e) => {
              if (!guess && e.currentTarget.currentTime * 1000 >= m.t0_ms - STOP_EARLY_MS) e.currentTarget.pause();
            }} />
          <button onClick={() => audioRef.current?.pause()} className="text-sm text-nb-accent underline">pause</button>
        </div>
      )}

      <section className="card mt-3 p-3" aria-label="Transcript before the statement">
        <h2 className="text-lg">Transcript up to the statement</h2>
        <ol ref={turnsRef} tabIndex={0} className="mt-1 max-h-56 overflow-y-auto space-y-1.5 pr-1 text-sm">
          {data.turns.map((t, i) => (
            <li key={i}><span className="mr-2 text-xs font-medium text-neutral-700">{shortSpeaker(t.speaker)}</span>
              {t.text}</li>
          ))}
          <li className="text-nb-muted">…</li>
        </ol>
      </section>

      {!guess ? (
        <section className="mt-4 text-center">
          <p className="text-lg">Up or down vs the sector in the next 5 minutes?</p>
          <div className="mt-2 flex justify-center gap-3">
            <button onClick={() => answer("up")}
              className="rounded-md border border-emerald-700 px-5 py-2 text-emerald-700 hover:bg-white">▲ Up</button>
            <button onClick={() => answer("down")}
              className="rounded-md border border-red-700 px-5 py-2 text-red-700 hover:bg-white">▼ Down</button>
          </div>
        </section>
      ) : (
        <section className="mt-4" aria-label="Answer">
          <h2 ref={revealRef} tabIndex={-1} className="text-2xl focus:outline-none">
            {right ? "Right" : "Wrong"}: <span className={`tabular ${DIR_TEXT[d]}`}>{pct(m.adj_ret_5m)}</span> vs{" "}
            {data.etf} in the next 5 minutes
          </h2>
          <ol className="mt-2">
            <MomentCard m={m} atOpen={false} active onSelect={() => router.push(replayHref)} />
          </ol>
          {m.placebo_pct != null && (
            <p className="mt-2 text-sm">
              {Math.round(m.placebo_pct * 100)}% of the 5-minute windows starting at a random minute of this call
              moved at least this much.
            </p>
          )}
          <p className="mt-2 text-sm text-nb-muted">
            Most single 5-minute moves are noise, so 1 guess, right or wrong, says little. The statement is associated
            with the move that followed it, not shown to have caused it.
          </p>
          <div className="mt-3 flex flex-wrap gap-3 text-sm">
            {next && <Link href={`/guess?call=${next}`} className="rounded bg-nb-accent px-3 py-1 text-white">
              Next call</Link>}
            <Link href={replayHref} className="text-nb-accent underline underline-offset-2">
              Open the replay at this moment</Link>
          </div>
        </section>
      )}
    </div>
  );
}
