// Formatting helpers and shared constants for the web app: US Eastern clock times, signed percents, mm:ss, the
// price-direction colors, and the 5-minute reaction window both the replay chart and the study gold answer use.
export const TZ = "America/New_York";
export const WINDOW_MS = 5 * 60_000;   // reaction window after a statement

/**
 * Formats a timestamp as hh:mm in US Eastern, 24-hour.
 */
export function clock(ts: string | Date): string {
  const d = typeof ts === "string" ? new Date(ts) : ts;
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: TZ });
}

/**
 * Formats a fraction as a percent with a leading + when positive, 0.0123 -> "+1.23%".
 */
export function pct(x: number, digits = 2): string {
  const s = (x * 100).toFixed(digits);
  return (x > 0 ? "+" : "") + s + "%";
}

/**
 * Formats milliseconds as m:ss, negative values as 0:00.
 */
export function mmss(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * Returns a speaker label without its title, "Marcus Lee, CFO" -> "Marcus Lee".
 */
export function shortSpeaker(speaker: string): string {
  return speaker.split(",")[0];
}

// Return direction for color. Moves under 0.10% are noise and stay grey, so green and red mean price direction only.
export type Dir = "up" | "down" | "flat";

/**
 * Classifies a return as up, down or flat (under 0.10% in size).
 */
export function dir(x: number): Dir {
  return Math.abs(x) < 0.001 ? "flat" : x > 0 ? "up" : "down";
}

export const DIR_HEX: Record<Dir, string> = { up: "#047857", down: "#b91c1c", flat: "#737373" };
export const DIR_TEXT: Record<Dir, string> = { up: "text-emerald-700", down: "text-red-700", flat: "text-neutral-500" };
