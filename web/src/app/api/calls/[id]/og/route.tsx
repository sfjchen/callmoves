// Open Graph preview image for 1 call: ticker, quarter, and 1 moment (?m=<rank>, else the top one) with its time,
// move vs the benchmark and a short quote, on the shared Stanford palette.
import { ImageResponse } from "next/og";
import { getCall } from "@/lib/data";
import { clock, DIR_HEX, dir, PAL, pct, shortQuarter, shortSpeaker } from "@/lib/format";
import { benchName, clip, NO_MOMENT, sharedMoment } from "@/lib/share";

// GET /api/calls/{id}/og?m=<rank> -> 1200x630 PNG
export async function GET(req: Request, { params }: RouteContext<"/api/calls/[id]/og">) {
  const { id } = await params;
  const call = await getCall(id);
  if (!call) return new Response("not found", { status: 404 });
  const m = sharedMoment(call, new URL(req.url).searchParams.get("m"));
  const c = call.call;
  return new ImageResponse(
    (
      <div style={{
        width: "100%", height: "100%", display: "flex", flexDirection: "column", padding: "56px 72px",
        background: PAL.page, color: PAL.ink, fontSize: 28, borderTop: `14px solid ${PAL.brand}`,
      }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 24 }}>
          <span style={{ fontSize: 88, fontWeight: 700, lineHeight: 1 }}>{c.ticker}</span>
          <span style={{ fontSize: 60, lineHeight: 1, color: PAL.muted }}>
            {shortQuarter(c.quarter)}</span>
          {c.company && <span style={{ fontSize: 28, color: PAL.muted }}>{c.company}</span>}
        </div>
        {m ? (
          <div style={{
            display: "flex", flexDirection: "column", marginTop: 36, padding: "28px 36px", flexGrow: 1,
            background: PAL.surface, border: `2px solid ${PAL.rule}`, borderRadius: 16,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 18, color: PAL.muted }}>
              <span style={{
                display: "flex", alignItems: "center", justifyContent: "center", width: 44, height: 44,
                borderRadius: 22, background: PAL.mark, color: PAL.surface, fontSize: 26, fontWeight: 700,
              }}>{m.rank}</span>
              <span>{`${clock(m.ts)} ET`}</span>
              <span style={{ marginLeft: "auto", fontSize: 52, fontWeight: 700, color: DIR_HEX[dir(m.adj_ret_5m)] }}>
                {pct(m.adj_ret_5m)}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", color: PAL.muted, fontSize: 24 }}>
              {`vs ${benchName(call)} ${call.open_reaction ? "at the next open" : "in 5 min"} on ` +
                `${m.vol_mult.toFixed(1)}x volume`}</div>
            <div style={{ display: "flex", marginTop: 20, fontSize: 36, lineHeight: 1.3 }}>
              {`\u201c${clip(m.quote, 170)}\u201d`}</div>
            <div style={{ display: "flex", marginTop: "auto", color: PAL.muted, fontSize: 24 }}>
              {`${shortSpeaker(m.speaker)}, ${m.section === "qa" ? "Q&A" : "prepared remarks"}`}</div>
          </div>
        ) : (
          <div style={{ display: "flex", marginTop: 48, fontSize: 36, color: PAL.muted }}>
            {NO_MOMENT}</div>
        )}
        <div style={{ display: "flex", marginTop: 24, fontSize: 24, color: PAL.muted }}>
          <span style={{ fontSize: 30, fontWeight: 700, color: PAL.brand, marginRight: 16 }}>CallMoves</span>
          <span style={{ alignSelf: "center" }}>what moved the stock during the earnings call</span>
        </div>
      </div>
    ),
    {
      width: 1200, height: 630,
      // a rerun or newly published call changes the numbers, so no year-long immutable cache
      // s-maxage lets Vercel's CDN serve crawlers, the v= hash in the URL changes when the moment does
      headers: { "Cache-Control": "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400" },
    },
  );
}
