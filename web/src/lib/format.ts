export const TZ = "America/New_York";

export function clock(ts: string | Date): string {
  const d = typeof ts === "string" ? new Date(ts) : ts;
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: TZ });
}

export function pct(x: number, digits = 2): string {
  const s = (x * 100).toFixed(digits);
  return (x > 0 ? "+" : "") + s + "%";
}

export function mmss(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function shortSpeaker(speaker: string): string {
  return speaker.split(",")[0];
}
