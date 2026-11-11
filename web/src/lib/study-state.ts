// Browser-side state for the time-to-insight study, kept in localStorage: the participant id, the 2 assigned trials
// and which are done. Every storage access is wrapped, since private windows and blocked site data make it throw.
import type { Condition } from "./study";

export type Trial = { call_id: string; condition: Condition; done: boolean };
export type StudyState = { pid: string; trials: Trial[] };
const KEY = "cm_study";

/**
 * Returns a copy of xs in random order.
 */
function shuffled<T>(xs: T[]): T[] {
  const a = [...xs];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/**
 * Loads this browser's study state, or creates one: a random id, 2 different calls from ids and the 2 conditions in
 * random order, so each participant sees both conditions on different calls.
 */
export function loadState(ids: string[]): StudyState {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? "null") as StudyState | null;
    if (s?.pid && s.trials?.length === 2) return s;
  } catch { /* falls through to a new state */ }
  const [a, b] = shuffled(ids), [c1, c2] = shuffled<Condition>(["callmoves", "baseline"]);
  const s: StudyState = { pid: crypto.randomUUID().slice(0, 8), trials: [
    { call_id: a, condition: c1, done: false }, { call_id: b, condition: c2, done: false }] };
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* the state lives in memory only */ }
  return s;
}

/**
 * Marks the trial on callId done in the stored state, when there is one.
 */
export function markDone(callId: string) {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? "null") as StudyState | null;
    if (!s) return;
    s.trials.forEach((t) => { if (t.call_id === callId) t.done = true; });
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch { /* the next visit to /study shows the trial as open */ }
}
