// Formatting helpers and shared constants for the web app: US Eastern clock times, signed percents, mm:ss, the
// Stanford palette shared by the charts and the share image, and the 5-minute reaction window both the replay
// chart and the study gold answer use.
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

// Stanford identity colors (identity.stanford.edu). Class names use the @theme tokens in globals.css, while SVG
// charts and the OG image cannot read CSS variables, so they take these hex values.
export const PAL = {
  page: "#f4f4f4", surface: "#ffffff", ink: "#2e2d29", muted: "#53565a", faint: "#6d6c69", line: "#d5d5d4",
  rule: "#eaeaea", brand: "#8c1515", mark: "#007c92", markBg: "#e5f1f3", flag: "#e98300",
  up: "#006f54", down: "#b1040e",
};
export const DIR_HEX: Record<Dir, string> = { up: PAL.up, down: PAL.down, flat: PAL.faint };
export const DIR_TEXT: Record<Dir, string> = { up: "text-up", down: "text-down", flat: "text-muted" };

/**
 * Formats a pipeline quarter label compactly, "Q3-2026" -> "Q3 '26". Labels in another shape pass through.
 */
export function shortQuarter(q: string): string {
  const m = /^(Q\d)-(?:FY)?\d{2}(\d{2})$/.exec(q);
  return m ? `${m[1]} '${m[2]}` : q;
}

// Sector names of the SPDR sector ETFs the pipeline benchmarks against.
export const SECTOR: Record<string, string> = {
  XLF: "Financials", XLV: "Health care", XLI: "Industrials", XLK: "Technology", XLC: "Communication",
  XLY: "Consumer discretionary", XLP: "Consumer staples", XLE: "Energy", XLU: "Utilities", XLB: "Materials",
  XLRE: "Real estate", IGV: "Software",
};

// Typical wait for a submitted call, from README "Cost and speed" and the worker's cron in
// .github/workflows/worker.yml.
export const WORKER_WAIT = "The worker checks the queue every 15 minutes, and a call takes about 10 minutes once it " +
  "starts.";

// How a moment's peers moved in the same minutes, as shown on cards and the comps panel.
export const SPILL_LABEL = { sector: "peers moved too", company: "stock alone", mixed: "peers partly" } as const;
