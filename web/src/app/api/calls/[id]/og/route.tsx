// Open Graph preview image for 1 call: ticker, quarter, and 1 moment (?m=<rank>, else the top one) with its time,
// move vs the benchmark and a short quote, on the sfjc.dev notebook palette from globals.css.
import { ImageResponse } from "next/og";
import { getCall } from "@/lib/data";
import { clock, DIR_HEX, dir, pct, shortQuarter, shortSpeaker } from "@/lib/format";
import { benchName, clip, NO_MOMENT, sharedMoment } from "@/lib/share";

const NB = { bg: "#fcf9f4", paper: "#faf6f0", text: "#1a1a1a", muted: "#6b6359", accent: "#8c3838", border: "#eee9e2" };
const FONT_CSS = "https://fonts.googleapis.com/css2?family=Patrick+Hand";
let hand: Promise<ArrayBuffer | null> | undefined;  // Patrick Hand TTF, fetched once per server instance

// GET /api/calls/{id}/og?m=<rank> -> 1200x630 PNG
export async function GET(req: Request, { params }: RouteContext<"/api/calls/[id]/og">) {
  const { id } = await params;
  const call = await getCall(id);
  if (!call) return new Response("not found", { status: 404 });
  const m = sharedMoment(call, new URL(req.url).searchParams.get("m"));
  // Google serves the TTF Satori needs to a non-browser user agent. Without it the image falls back to the default
  // sans, so a font outage never breaks the preview.
  hand ??= fetch(FONT_CSS, { signal: AbortSignal.timeout(3000) })
    .then((r) => r.text())
    .then((css) => /src: url\((.+?)\) format\('truetype'\)/.exec(css)?.[1])
    .then((u) => (u ? fetch(u, { signal: AbortSignal.timeout(3000) }) : null))
    .then((r) => (r?.ok ? r.arrayBuffer() : null))
    .catch(() => null);
  const font = await hand;
  if (!font) hand = undefined;   // retries on the next request
  const handFam = font ? "Patrick Hand" : "sans-serif";
  const c = call.call;
  return new ImageResponse(
    (
      <div style={{
        width: "100%", height: "100%", display: "flex", flexDirection: "column", padding: "56px 72px",
        background: NB.bg, color: NB.text, fontSize: 28,
      }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 24 }}>
          <span style={{ fontFamily: handFam, fontSize: 96, lineHeight: 1 }}>{c.ticker}</span>
          <span style={{ fontFamily: handFam, fontSize: 72, lineHeight: 1, color: NB.muted }}>
            {shortQuarter(c.quarter)}</span>
          {c.company && <span style={{ fontSize: 28, color: NB.muted }}>{c.company}</span>}
        </div>
        {m ? (
          <div style={{
            display: "flex", flexDirection: "column", marginTop: 36, padding: "28px 36px", flexGrow: 1,
            background: NB.paper, border: `2px solid ${NB.border}`, borderRadius: 16,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 18, color: NB.muted }}>
              <span style={{
                display: "flex", alignItems: "center", justifyContent: "center", width: 44, height: 44,
                borderRadius: 22, background: NB.accent, color: "#fff", fontSize: 26, fontWeight: 700,
              }}>{m.rank}</span>
              <span>{`${clock(m.ts)} ET`}</span>
              <span style={{ marginLeft: "auto", fontSize: 52, fontWeight: 700, color: DIR_HEX[dir(m.adj_ret_5m)] }}>
                {pct(m.adj_ret_5m)}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", color: NB.muted, fontSize: 24 }}>
              {`vs ${benchName(call)} ${call.open_reaction ? "at the next open" : "in 5 min"} on ` +
                `${m.vol_mult.toFixed(1)}x volume`}</div>
            <div style={{ display: "flex", marginTop: 20, fontSize: 36, lineHeight: 1.3 }}>
              {`\u201c${clip(m.quote, 170)}\u201d`}</div>
            <div style={{ display: "flex", marginTop: "auto", color: NB.muted, fontSize: 24 }}>
              {`${shortSpeaker(m.speaker)}, ${m.section === "qa" ? "Q&A" : "prepared remarks"}`}</div>
          </div>
        ) : (
          <div style={{ display: "flex", marginTop: 48, fontSize: 36, color: NB.muted }}>
            {NO_MOMENT}</div>
        )}
        <div style={{ display: "flex", marginTop: 24, fontSize: 24, color: NB.muted }}>
          <span style={{ fontFamily: handFam, fontSize: 34, color: NB.accent, marginRight: 16 }}>CallMoves</span>
          <span style={{ alignSelf: "center" }}>what moved the stock during the earnings call</span>
        </div>
      </div>
    ),
    {
      width: 1200, height: 630,
      fonts: font ? [{ name: "Patrick Hand", data: font, weight: 400, style: "normal" }] : undefined,
      // a rerun or newly published call changes the numbers, so no year-long immutable cache
      headers: { "Cache-Control": "public, max-age=3600" },
    },
  );
}
