// Blind labeling view (client component): the stock and ETF path around 1 flagged event and the numbered sentences
// spoken before it. A click on a sentence, or n / r / m / o for none, release, market, other, saves the label and
// loads the next flag.
"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { LabelFlag } from "@/lib/labels";
import { clock, shortSpeaker } from "@/lib/format";

const KINDS = [
  { key: "n", choice: "none", text: "None stands out" },
  { key: "r", choice: "release", text: "Still digesting the release" },
  { key: "m", choice: "market", text: "Market or sector moved too" },
  { key: "o", choice: "other", text: "Other" },
] as const;
const CONF = { 1: "guess", 2: "plausible", 3: "clear" } as const;

/**
 * Renders the price path chart, the sentence list and the answer buttons for 1 flag, and posts the answer.
 */
export default function LabelView({ flag, k, done, total }: {
  flag: LabelFlag; k: string; done: number; total: number;
}) {
  const router = useRouter();
  const [conf, setConf] = useState<1 | 2 | 3>(2);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  // Saves the answer, then reloads the page, which serves the next unlabeled flag.
  async function send(choice: string, n?: number) {
    if (busy) return;
    setBusy(true);
    const res = await fetch("/api/labels", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ k, flag_id: flag.id, choice, n, confidence: conf }),
    }).catch(() => null);
    if (res?.ok) router.refresh();
    else { setErr("Could not save, try again."); setBusy(false); }
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const kind = KINDS.find((x) => x.key === e.key);
      if (kind) void send(kind.choice);
      else if (e.key === "1" || e.key === "2" || e.key === "3") setConf(Number(e.key) as 1 | 2 | 3);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const start = new Date(flag.start).getTime();
  return (
    <div className="mx-auto max-w-4xl">
      <div className="flex flex-wrap items-baseline gap-x-3 mb-2">
        <h1 className="text-2xl">Which statement moved {flag.ticker}?</h1>
        <span className="text-sm text-nb-muted tabular">flag at {clock(flag.start)} ET · {flag.company} · vs{" "}
          {flag.etf}</span>
        <span className="ml-auto text-xs text-nb-muted tabular">{done} of {total} labeled</span>
      </div>
      <PathChart flag={flag} start={start} />
      <div className="my-2 flex flex-wrap items-center gap-1.5 text-sm">
        {KINDS.map((x) => (
          <button key={x.key} disabled={busy} onClick={() => send(x.choice)}
            className="rounded border border-nb-border bg-white px-2 py-1 hover:border-nb-accent disabled:opacity-50">
            <kbd>{x.key}</kbd> {x.text}</button>
        ))}
        <span className="ml-auto text-xs text-nb-muted">confidence{" "}
          {([1, 2, 3] as const).map((c) => (
            <button key={c} onClick={() => setConf(c)} className={`ml-1 rounded px-1.5 py-0.5 ${conf === c ?
              "bg-nb-accent text-white" : "border border-nb-border"}`}><kbd>{c}</kbd> {CONF[c]}</button>
          ))}
        </span>
      </div>
      {err && <p className="text-sm text-red-700">{err}</p>}
      <p className="text-xs text-nb-muted mb-1">Click the sentence that most plausibly moved the stock. A sentence
        after the move began cannot be its cause.</p>
      <ol className="card divide-y divide-nb-border max-h-[55vh] overflow-y-auto">
        {flag.sentences.map((s) => {
          const after = new Date(s.ts).getTime() >= start;
          return (
            <li key={s.n}>
              <button disabled={busy} onClick={() => send("statement", s.n)}
                className="flex w-full gap-2 px-2 py-1.5 text-left text-sm hover:bg-white disabled:opacity-50">
                <span className={`w-16 shrink-0 tabular text-xs leading-5 ${
                  after ? "text-nb-accent" : "text-nb-muted"}`}>
                  {new Date(s.ts).toLocaleTimeString("en-US", { hour12: false, timeZone: "America/New_York" })}</span>
                <span><span className="text-xs font-medium mr-1.5">{shortSpeaker(s.speaker)}</span>{s.text}</span>
              </button>
            </li>
          );
        })}
        {flag.sentences.length === 0 && <li className="p-2 text-sm text-nb-muted">No speech in this window.</li>}
      </ol>
    </div>
  );
}

/**
 * Renders the stock and ETF paths as % from the window's first close, volume multiples as bars and the flagged
 * minutes as a shaded band.
 */
function PathChart({ flag, start }: { flag: LabelFlag; start: number }) {
  const W = 800, H = 180, PAD = 28;
  const p = flag.path;
  if (p.length < 2) return null;
  const t = p.map((x) => new Date(x.ts).getTime());
  const s = p.map((x) => (x.px / p[0].px - 1) * 100), e = p.map((x) => (x.etf_px / p[0].etf_px - 1) * 100);
  const lo = Math.min(...s, ...e), hi = Math.max(...s, ...e), span = hi - lo || 1;
  const x = (v: number) => PAD + ((v - t[0]) * (W - 2 * PAD)) / (t[t.length - 1] - t[0] || 1);
  const y = (v: number) => 8 + ((hi - v) * (H - 40)) / span;
  const line = (vs: number[]) => vs.map((v, i) => `${x(t[i]).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = new Date(flag.minutes[flag.minutes.length - 1]).getTime() + 60_000;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="card w-full" role="img" aria-label="price path around the flag">
      <rect x={x(start)} width={Math.max(2, x(last) - x(start))} y={0} height={H - 22} fill="#f59e0b" opacity={0.15} />
      {p.map((v, i) => (
        <rect key={i} x={x(t[i]) - 3} width={6} y={H - 22 - Math.min(10, v.vol_mult) * 4}
          height={Math.min(10, v.vol_mult) * 4} fill="#d6cfc4" />
      ))}
      <polyline points={line(e)} fill="none" stroke="#6b6359" strokeWidth={1.25} strokeDasharray="4 3" />
      <polyline points={line(s)} fill="none" stroke="#1a1a1a" strokeWidth={2} />
      {t.filter((v) => new Date(v).getUTCMinutes() % 5 === 0).map((v) => (
        <text key={v} x={x(v)} y={H - 6} fontSize={11} textAnchor="middle" fill="#6b6359">{clock(new Date(v))}</text>
      ))}
      <text x={PAD} y={14} fontSize={11} fill="#1a1a1a">{flag.ticker} {s[s.length - 1] >= 0 ? "+" : ""}
        {s[s.length - 1].toFixed(2)}%</text>
      <text x={PAD + 110} y={14} fontSize={11} fill="#6b6359">{flag.etf} (dashed) {e[e.length - 1] >= 0 ? "+" : ""}
        {e[e.length - 1].toFixed(2)}%</text>
    </svg>
  );
}
