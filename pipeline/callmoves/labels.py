"""Blind attribution labels: which statement, if any, most plausibly moved the stock at each flagged price event.

    python -m callmoves.labels flags data/*/          # freezes the flag set into labels/flags.json (+ the web copy)
    python -m callmoves.labels opus                   # Claude Opus rates every unrated flag on the blind view
    python -m callmoves.labels packets                # writes the richer per-flag views the protocol agent reads
    python -m callmoves.labels agent labels/agent_protocol.json   # uploads the protocol agent's labels
    python -m callmoves.labels report                 # precision at 3 and 5 per tier, Cohen's kappa between raters
    python -m callmoves.labels align                  # transcript clock vs squawk headline times

A flag is 1 price event (`market.flag_events`) that starts during the call, measured against the sector ETF when the
set was frozen, so labels never move when the shipped benchmark changes. The blind view is what the /label page
shows: the stock and ETF path around the event and the sentences spoken from 5 minutes before it to the end of its
first minute, numbered, with nothing CallMoves computed (no moments, scores or novelty). Tiers are reported apart,
never pooled: gold (a timestamped headline within 2 minutes of the flag repeats 1 of its sentences), silver (the
blind Opus rater and the protocol agent pick the same answer), human (the "user" rater on /label).
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from . import llm, market
from .models import CallOutput, Part
from .publish import db_client
from .transcript import align, load_call

ROOT = Path(__file__).resolve().parents[1]
FLAGS = ROOT / "labels" / "flags.json"
WEB_FLAGS = ROOT.parent / "web" / "src" / "lib" / "flags.json"
HEADLINES = ROOT / "labels" / "headlines.json"
PACKETS = ROOT / "labels" / "packets"
TABLE = "callmoves_labels"
BLIND_BEFORE_MIN = 5     # transcript minutes before the event on the blind view
RICH_BEFORE_MIN = 10     # and on the protocol agent's view
PATH_BEFORE_MIN = 15     # price path minutes shown before and after the event start
PATH_AFTER_MIN = 10
GOLD_TOL = timedelta(minutes=2)
SAME_SENTENCE_MS = 30_000   # 2 statement labels this close in the audio name the same remark
OPUS = "claude-opus-5-5"
KINDS = ("statement", "none", "release", "market", "other")


def flag_id(call_id: str, ts: datetime) -> str:
    """Returns the stable id of the flag starting at ts, e.g. WFC_Q3-2026@2026-10-13T14:31Z."""
    return f"{call_id}@{pd.Timestamp(ts).tz_convert('UTC'):%Y-%m-%dT%H:%MZ}"


def load_flags() -> list[dict]:
    """Returns the frozen flag set."""
    return json.loads(FLAGS.read_text())


def load_folder(call_id: str):
    """Returns (folder, meta, aligned segments, ETF-benchmark bars) of the call folder whose call.json has call_id."""
    for f in sorted((ROOT / "data").glob("*/call.json")):
        meta, segs = load_call(f)
        if meta.call_id == call_id:
            bars, _, _ = market.load_call_bars(f.parent, meta.ticker, meta.etf, meta.call_start, "etf")
            return f.parent, meta, align(meta, segs), bars
    raise KeyError(call_id)


def sentences(meta, segments, start: datetime, before_min: int) -> list[dict]:
    """Every ASR sentence whose wall-clock start lies from before_min minutes before `start` to the end of start's
    minute, in order, as {n, seg_id, t_ms, ts, speaker, text}. A statement in the event's own minute counts, since
    a bar closes at the minute's end."""
    lo, hi = start - timedelta(minutes=before_min), start + timedelta(minutes=1)
    out = []
    for s in segments:
        cur, parts = None, s.parts or [Part(t0_ms=s.t0_ms, text=s.text)]
        for i, p in enumerate(parts):
            # ASR parts break mid-sentence, so parts are joined until 1 ends a sentence, timed by its first part
            if cur is None:
                cur = {"seg_id": s.seg_id, "t_ms": p.t0_ms, "speaker": s.speaker, "text": p.text,
                       "ts": meta.call_start + timedelta(milliseconds=p.t0_ms - meta.audio_offset_ms)}
            else:
                cur["text"] += " " + p.text
            if i == len(parts) - 1 or re.search(r"[.?!][\"')]*$", p.text.strip()):
                if lo <= cur["ts"] < hi:
                    out.append({"n": len(out) + 1, **cur})
                cur = None
    return out


def blind_view(flag: dict, rich: bool = False) -> tuple[str, list[dict]]:
    """Returns the text view of 1 flag and its numbered sentences. The blind view has the stock and ETF path,
    volume multiple and 5 minutes of transcript. The rich view (protocol agent) adds SPY, the peer index and the
    cross-asset ETFs, 10 minutes of transcript, consensus, the release and prior call, and any headlines."""
    folder, meta, segs, bars = load_folder(flag["call_id"])
    start = datetime.fromisoformat(flag["start"])
    ctx_path = folder / "context.csv.gz"
    if rich and ctx_path.exists():
        bars, _ = market.with_context(bars, pd.read_csv(ctx_path, parse_dates=["ts"]), meta.ticker, meta.etf,
                                      meta.call_start, "etf")
    win = bars[(bars["ts"] >= start - timedelta(minutes=PATH_BEFORE_MIN)) &
               (bars["ts"] <= start + timedelta(minutes=PATH_AFTER_MIN))]
    base = bars[bars["ts"] < win["ts"].min()].tail(1)
    base = base.iloc[0] if not base.empty else win.iloc[0]
    cols = [("px", meta.ticker), ("etf_px", meta.etf)] + ([("spy_px", "SPY"), ("peer_px", "peers")] if rich else [])
    cols = [(c, n) for c, n in cols if c in win]
    flagged = ", ".join(f"{datetime.fromisoformat(m).astimezone(market.ET):%H:%M}" for m in flag["minutes"])
    lines = [(f"Call: {meta.company or meta.ticker} ({meta.ticker}) {meta.quarter}, sector ETF {meta.etf}. "
              f"Flagged price event starts {start.astimezone(market.ET):%H:%M} ET (flagged minutes: {flagged})."),
             "", "Price path, % change from the close before the window, and volume vs its trailing median:",
             "time ET | " + " | ".join(n for _, n in cols) + " | volume"]
    for _, r in win.iterrows():
        mark = " <- event" if r["ts"] == start else ""
        lines.append(f"{r['ts'].astimezone(market.ET):%H:%M} | " + " | ".join(
            f"{(r[c] / base[c] - 1) * 100:+.2f}%" for c, _ in cols) + f" | {r['vol_mult']:.1f}x{mark}")
    sents = sentences(meta, segs, start, RICH_BEFORE_MIN if rich else BLIND_BEFORE_MIN)
    lines += ["", (f"Transcript sentences from {RICH_BEFORE_MIN if rich else BLIND_BEFORE_MIN} min before the "
                   "event to the end of its first minute:")]
    lines += [f"[{s['n']}] {s['ts'].astimezone(market.ET):%H:%M:%S} {s['speaker']}: {s['text']}" for s in sents]
    if rich:
        ctx = pd.read_csv(ctx_path, parse_dates=["ts"]) if ctx_path.exists() else None
        if ctx is not None:
            at = ctx[(ctx["ts"] >= start - timedelta(minutes=1)) & (ctx["ts"] <= start + timedelta(minutes=5))]
            moves = at.groupby("sym")["px"].agg(lambda p: p.iloc[-1] / p.iloc[0] - 1)
            lines += ["", "Every context symbol from the minute before the event to 5 min after it: " +
                      ", ".join(f"{s} {v * 100:+.2f}%" for s, v in moves.items())]
        est = json.loads((folder / "estimates.json").read_text()).get("estimates", []) \
            if (folder / "estimates.json").exists() else []
        lines += ["", "Consensus (Alpha Vantage): " + ("; ".join(
            f"{e['role']} {e['metric']} consensus {e['consensus']}" + (f", actual {e['actual']}" if e.get("actual")
                                                                       else "") for e in est) or "none")]
        heads = [h for h in (json.loads(HEADLINES.read_text()) if HEADLINES.exists() else [])
                 if h["call_id"] == flag["call_id"]
                 and abs(datetime.fromisoformat(h["ts"]) - start) <= timedelta(minutes=15)]
        lines += ["", "Squawk headlines within 15 min of the event (UTC time, account, text):"] + (
            [f"- {h['ts']} @{h['account']}: {h['text']}" for h in heads] or ["- none"])
        lines += ["", "Press release (first 6000 characters):", (folder / "release.txt").read_text()[:6000],
                  "", "Prior quarter (first 3000 characters):", (folder / "prior_transcript.txt").read_text()[:3000]]
    return "\n".join(lines), sents


class Label(BaseModel):
    choice: Literal["statement", "none", "release", "market", "other"] = Field(
        description="statement if 1 numbered sentence most plausibly moved the stock, none if nothing stands out, "
                    "release if the move is the market still digesting the earnings release, market if the sector "
                    "or market moved alike, other for anything else")
    sentence: int | None = Field(default=None, description="the [n] of the chosen sentence when choice=statement")
    confidence: int = Field(ge=1, le=3, description="1 a guess, 2 plausible, 3 clear")
    reason: str = Field(description="1 short line")


OPUS_SYSTEM = (
    "You are an equity analyst replaying an earnings call next to a 1-minute price chart. For 1 flagged price event "
    "you see the stock and its sector ETF around the event and the sentences spoken just before it. Pick the 1 "
    "sentence that most plausibly moved the stock, or say none, release, market or other. A sentence spoken after "
    "the move began cannot be its cause. Prefer new, specific, checkable information (guidance, a new number, a "
    "surprising answer) over routine remarks. Answer none when nothing stands out.")


def save(rows: list[dict]) -> None:
    """Upserts label rows on (flag_id, rater)."""
    if rows:
        db_client().table(TABLE).upsert(rows, on_conflict="flag_id,rater").execute()


def as_row(flag: dict, rater: str, lab: Label, sents: list[dict]) -> dict:
    """Returns the table row for 1 label, mapping a sentence number to its seg_id and audio time. A statement label
    with a number outside the view becomes other."""
    s = next((x for x in sents if x["n"] == lab.sentence), None) if lab.choice == "statement" else None
    choice = "other" if lab.choice == "statement" and s is None else lab.choice
    return {"flag_id": flag["id"], "call_id": flag["call_id"], "flag_ts": flag["start"], "rater": rater,
            "choice": choice, "seg_id": s and s["seg_id"], "t_ms": s and s["t_ms"], "confidence": lab.confidence,
            "reason": lab.reason[:500]}


def fetch_labels() -> list[dict]:
    """Returns every stored label."""
    rows, page = [], 0
    while True:
        got = db_client().table(TABLE).select("*").range(page * 1000, page * 1000 + 999).execute().data
        rows += got
        if len(got) < 1000:
            return rows
        page += 1


def kappa(a: list[str], b: list[str]) -> float | None:
    """Cohen's kappa of 2 raters' nominal answers on the same items."""
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in ca) / n ** 2
    return None if pe == 1 else (po - pe) / (1 - pe)


def answer(row: dict, sents_t: list[int]) -> str:
    """Returns a label as a comparable answer: its kind, or the sentence it names, snapped to the nearest sentence
    start of the flag's view so 2 raters naming 1 remark agree."""
    if row["choice"] != "statement":
        return row["choice"]
    t = min(sents_t, key=lambda x: abs(x - row["t_ms"])) if sents_t else row["t_ms"]
    return f"s{t}"


def agree(a: dict, b: dict) -> bool:
    """Whether 2 labels give the same answer: the same kind, or 2 statements less than SAME_SENTENCE_MS apart in
    the audio, since 2 raters may name adjacent sentences of 1 remark."""
    if a["choice"] != b["choice"]:
        return False
    return a["choice"] != "statement" or abs(a["t_ms"] - b["t_ms"]) < SAME_SENTENCE_MS


def quotes(headline: str, sentence: str, company: str) -> bool:
    """Whether a headline quotes a sentence: at least half of the headline's content words (over 3 characters,
    at least 3 of them, not part of the company's name, which every headline and the operator's welcome share)
    appear in the sentence."""
    name = set(re.findall(r"[a-z0-9.%$]+", company.lower()))
    words = {w for w in re.findall(r"[a-z0-9.%$]+", headline.lower()) if len(w) > 3} - name
    return len(words) >= 3 and len(words & set(re.findall(r"[a-z0-9.%$]+", sentence.lower()))) >= len(words) / 2


def gold_labels(flags: list[dict]) -> dict[str, dict]:
    """Flags a headline backs: a headline posted within GOLD_TOL of the event start quotes 1 sentence of the blind
    window (`quotes`), the latest such sentence wins. Returns flag id -> {t_ms, seg_id, headline}."""
    heads = json.loads(HEADLINES.read_text()) if HEADLINES.exists() else []
    out = {}
    for f in flags:
        start = datetime.fromisoformat(f["start"])
        near = [h for h in heads if h["call_id"] == f["call_id"]
                and abs(datetime.fromisoformat(h["ts"]) - start) <= GOLD_TOL]
        if not near:
            continue
        _, meta, segs, _ = load_folder(f["call_id"])
        sents = sentences(meta, segs, start, BLIND_BEFORE_MIN)
        for h in near:
            hit = [x for x in sents if quotes(h["text"], x["text"], meta.company)]
            if hit:
                out[f["id"]] = {"t_ms": hit[-1]["t_ms"], "seg_id": hit[-1]["seg_id"], "headline": h["url"]}
    return out


def clock_check() -> None:
    """Checks the transcript clock against squawk headlines: for every headline that quotes 1 sentence of its call
    (`quotes`, a unique match), the lag is headline time minus the sentence's wall-clock time. Squawk desks post
    after hearing a remark, so a negative lag means our clock runs late by at least that much, and the smallest lag
    per call bounds how early our clock can be. Headlines posted before the call started summarize the release, not
    the call, and are skipped. Prints per-call counts, minimum and median lag."""
    heads = json.loads(HEADLINES.read_text())
    lags = defaultdict(list)
    for call_id in sorted({h["call_id"] for h in heads}):
        _, meta, segs, _ = load_folder(call_id)
        sents = sentences(meta, segs, meta.call_start + timedelta(hours=4), 5 * 60)
        for h in (h for h in heads if h["call_id"] == call_id):
            ts = datetime.fromisoformat(h["ts"])
            hit = [x for x in sents if quotes(h["text"], x["text"], meta.company)]
            if ts >= meta.call_start and len(hit) == 1:
                lags[call_id].append((ts - hit[0]["ts"]).total_seconds() / 60)
    allv = sorted(v for vs in lags.values() for v in vs)
    print(f"{len(heads)} headlines, {len(allv)} quote 1 sentence, in {len(lags)} calls (lag = posted - spoken, min)")
    for call_id, vs in sorted(lags.items()):
        vs.sort()
        print(f"  {call_id:16} n={len(vs):2}  min {vs[0]:+6.1f}  median {vs[len(vs) // 2]:+6.1f}")
    if allv:
        print(f"all: min {allv[0]:+.1f}, median {allv[len(allv) // 2]:+.1f}, "
              f"negative {sum(v < 0 for v in allv)}, under 1 min {sum(v < 1 for v in allv)}")


def report() -> None:
    """Prints label counts per rater, Cohen's kappa per rater pair, and precision at 3 and 5 of the shipped moments
    per tier. A moment is scored when its price event is a labeled flag of the tier, and is a hit when its statement
    lies within GOLD_TOL of the labeled statement; flags labeled none, release, market or other make every moment
    on them a miss."""
    flags = load_flags()
    rows = fetch_labels()
    by_rater: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        by_rater[r["rater"]][r["flag_id"]] = r
    sent_t = {}
    for f in flags:
        _, meta, segs, _ = load_folder(f["call_id"])
        sent_t[f["id"]] = [s["t_ms"] for s in sentences(meta, segs, datetime.fromisoformat(f["start"]),
                                                         RICH_BEFORE_MIN)]
    print(f"{len(flags)} flags in {len({f['call_id'] for f in flags})} calls. Labels per rater: "
          f"{ {k: len(v) for k, v in by_rater.items()} }")
    raters = sorted(by_rater)
    for i, a in enumerate(raters):
        for b in raters[i + 1:]:
            both = sorted(set(by_rater[a]) & set(by_rater[b]))
            ans_a = [answer(by_rater[a][x], sent_t[x]) for x in both]
            # a rater naming a sentence within SAME_SENTENCE_MS of the other's counts as the same answer
            ans_b = [y if agree(by_rater[a][x], by_rater[b][x]) else answer(by_rater[b][x], sent_t[x])
                     for x, y in zip(both, ans_a)]
            k = kappa(ans_a, ans_b)
            print(f"kappa {a} vs {b}: {k if k is None else round(k, 2)} on {len(both)} shared flags")
    gold = gold_labels(flags)
    silver = {}
    for x, r in by_rater.get("opus-blind", {}).items():
        o = by_rater.get("agent-protocol", {}).get(x)
        if o and agree(r, o):
            silver[x] = r
    tiers = {"gold": {x: {"choice": "statement", **g} for x, g in gold.items()}, "silver": silver,
             "human": by_rater.get("user", {})}
    outs = {}
    for f in {f["call_id"] for f in flags}:
        p = ROOT.parent / "web" / "data" / f"{f}.json"
        if p.exists():
            outs[f] = CallOutput.model_validate_json(p.read_text())
    for tier, labs in tiers.items():
        for k in (3, 5):
            hits = n = 0
            for cid, out in outs.items():
                for m in out.moments[:k]:
                    if m.nearest_flag_min < 0:
                        continue
                    minute = market.minute_of(m.ts) + timedelta(minutes=m.nearest_flag_min)
                    fl = next((g for g in flags if g["call_id"] == cid and any(
                        abs(datetime.fromisoformat(t) - minute) < timedelta(seconds=1) for t in g["minutes"])), None)
                    if not fl or fl["id"] not in labs:
                        continue
                    lab = labs[fl["id"]]
                    n += 1
                    t = (m.ts - out.call.call_start).total_seconds() * 1000 + out.call.audio_offset_ms
                    hits += lab["choice"] == "statement" and abs(t - lab["t_ms"]) <= GOLD_TOL.total_seconds() * 1000
            print(f"{tier}: {len(labs)} labeled flags, precision@{k} = "
                  f"{'n/a' if n == 0 else f'{hits / n:.2f}'} ({hits}/{n} scored moments)")
    print(f"label kinds: { {r: dict(Counter(x['choice'] for x in v.values())) for r, v in by_rater.items()} }")


def main(argv=None):
    """Runs 1 subcommand, see the module docstring."""
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["flags", "opus", "packets", "agent", "report", "align"])
    ap.add_argument("paths", type=Path, nargs="*")
    args = ap.parse_args(argv)
    if args.cmd == "flags":
        flags = []
        for folder in args.paths:
            meta, segs = load_call(folder / "call.json")
            bars, _, _ = market.load_call_bars(folder, meta.ticker, meta.etf, meta.call_start, "etf")
            end = meta.call_start + timedelta(milliseconds=max(s.t1_ms for s in segs) - meta.audio_offset_ms)
            ev = market.flag_events(bars)
            for _, g in ev.groupby("event"):
                start = g["start"].iloc[0]
                if meta.call_start <= start <= end:
                    flags.append({"id": flag_id(meta.call_id, start), "call_id": meta.call_id,
                                  "start": start.isoformat(), "minutes": [t.isoformat() for t in g["ts"]]})
        FLAGS.write_text(json.dumps(flags, indent=1))
        web = []
        for f in flags:   # the /label page shows exactly the blind view Opus gets, numbered the same
            _, meta, segs, bars = load_folder(f["call_id"])
            start = datetime.fromisoformat(f["start"])
            win = bars[(bars["ts"] >= start - timedelta(minutes=PATH_BEFORE_MIN)) &
                       (bars["ts"] <= start + timedelta(minutes=PATH_AFTER_MIN))]
            web.append(f | {"ticker": meta.ticker, "etf": meta.etf, "company": meta.company, "path": [
                {"ts": r["ts"].isoformat(), "px": r["px"], "etf_px": r["etf_px"], "vol_mult": round(r["vol_mult"], 2)}
                for _, r in win.iterrows()], "sentences": [
                {k: (v.isoformat() if k == "ts" else v) for k, v in x.items()}
                for x in sentences(meta, segs, start, BLIND_BEFORE_MIN)]})
        WEB_FLAGS.write_text(json.dumps(web))
        print(f"{len(flags)} flags in {len({f['call_id'] for f in flags})} calls -> {FLAGS}")
    elif args.cmd == "opus":
        done = {r["flag_id"] for r in fetch_labels() if r["rater"] == "opus-blind"}
        todo = [f for f in load_flags() if f["id"] not in done]
        for i, f in enumerate(todo, 1):
            view, sents = blind_view(f)
            lab = llm.parse(OPUS_SYSTEM, view, Label, max_tokens=4000, model=OPUS)
            save([as_row(f, "opus-blind", lab, sents)])
            print(f"{i}/{len(todo)} {f['id']}: {lab.choice} {lab.sentence or ''} ({lab.confidence}) {lab.reason}",
                  flush=True)
        print(f"Opus tokens {dict(llm.USAGE)}")
    elif args.cmd == "packets":
        PACKETS.mkdir(parents=True, exist_ok=True)
        for f in load_flags():
            view, _ = blind_view(f, rich=True)
            (PACKETS / f"{f['id'].replace(':', '').replace('@', '_')}.md").write_text(f"flag_id: {f['id']}\n\n{view}")
        print(f"wrote {len(load_flags())} packets to {PACKETS}")
    elif args.cmd == "agent":
        by_id = {f["id"]: f for f in load_flags()}
        rows = []
        for x in json.loads(args.paths[0].read_text()):
            f = by_id[x["flag_id"]]
            _, sents = blind_view(f, rich=True)
            rows.append(as_row(f, "agent-protocol", Label(**{k: x[k] for k in Label.model_fields}), sents))
        save(rows)
        print(f"saved {len(rows)} agent-protocol labels")
    elif args.cmd == "align":
        clock_check()
    else:
        report()


if __name__ == "__main__":
    main()
