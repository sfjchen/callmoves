"""Timing study: do statements line up with price moves better than chance, and are call minutes abnormal at all.

    python -m callmoves.timing data/*/ [--outcome volume] [--calibrate 200]

Text first, price blind. Each call gets event minutes from the transcript alone (see `events`). The outcome of a
minute is the percentile rank, among the call's own minutes, of the |adjusted return| over the 5 minutes from it
(or of the peak volume multiple with --outcome volume), so 1 huge move cannot carry a call. A call's statistic is
the mean rank at its event minutes, and its z is that statistic against a null that moves the events and keeps the
bars:

- local: every event shifted by the same 6 to 15 minutes either way, a shifted event that leaves the call dropped.
  Keeps the time of day and roughly the place in the call. On random events it rejects at most as often as it
  should (--calibrate). Trimming to events that every shift keeps inside the call was tried and rejected 13 to 21%
  of the time, because the trimmed events sit in the middle and their shifted copies reach the quieter ends. A
  sharp peak shared by claims and volatility can still pass it, so a positive local result counts only when the
  cross null agrees.
- cross: the events laid at another call's positions (fractions of its prepared remarks and Q&A). Keeps the usual
  shape of a call.
- rotate: events rotated inside their own section by 10 minutes or more. Keeps the section only, so it also picks
  up that claims and volatility both crowd the start of a call. Reported for continuity with the old placebo.

Calls pool by the mean z. The test is across calls: a 95% interval and a 1-sided p from a bootstrap over call days
(calls on 1 day share market news), which needs nothing from the within-call null but its centre. The permutation p
(1 null placement drawn per call, the same one for every family, so the max-statistic p across families is valid
too) is printed for the local and cross nulls only: neighbouring rotations are correlated with each other and not
with the real placement, so the rotate null's spread is too small and it rejects 11 to 13% of the time on random
events (--calibrate measures this). The older sign test (eval --placebo) threw the effect size away and
needed a 64% hit rate for 80% power at 75 calls.

The calibration part asks the opposite question, whether the price side is real: the share of regular-session call
minutes flagged abnormal, against the rest of the same day's regular session and against the same clock minutes of
the sessions on file before the call day.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from . import market
from .claims import is_management
from .eval import MIN_SHIFT_MIN, placebo_inputs
from .models import Claim, Segment
from .score import touches_open
from .transcript import qa_start_ms

LOCAL_SHIFTS = [s for s in range(-15, 16) if abs(s) >= 6]   # past the 5-minute window, close enough to keep the
                                                            # place in the call
MIN_EVENTS = 5       # fewer events than this in a call say nothing about its timing
MIN_NULL = 10        # fewer null placements than this cannot give a z
MIN_COVER = 0.25     # share of call minutes with a trade: under it most 5-minute windows read stale prices
MIN_REG_MIN = 20     # regular-session call minutes needed for the calibration
DRAWS = 20000
BOOT = 4000
NULLS = ("local", "cross", "rotate")
PERM_OK = ("local", "cross")   # nulls whose permutation p holds its level on random events
OUTCOMES = {"move": "adj_ret", "volume": "vol_mult"}   # forward_window keys
FILLER = re.compile(r"\b(um+|uh+|you know|i mean|sort of|kind of)\b", re.IGNORECASE)
POWER_RUNS = 200
PLANT_SHARES = (0.02, 0.05, 0.1, 0.2)   # sensitivity check: shares of claims planted on big-move minutes
TOP_SHARE = 0.25     # a delivery family is the top quarter of a call's management turns on that measure


@dataclass
class Grid:
    """1 call on a 1-minute grid from its first to its last turn: x is each minute's outcome (NaN where the window
    touches the opening auction), qi the first Q&A minute (n without Q&A), ev the event minute indexes per family."""
    name: str
    day: str
    n: int
    qi: int
    x: np.ndarray
    ev: dict[str, np.ndarray]


def events(segments: list[Segment], claims: list[Claim]) -> dict[str, list[datetime]]:
    """Returns each family's event times for 1 call, all read from the transcript without the bars: every claim
    (dense: a median call has 61, in 41% of its minutes), guidance claims, novel claims, novel guidance claims, the
    starts of management answers, and 3 delivery families from turn timings and
    text alone: management turns in the call's top quarter by speaking rate (words a second) and by filler rate (um,
    uh, you know, per word), and answers in the top quarter by the pause before them (the gap after the analyst
    stops). Whisper drops most fillers and the diarizer sets the gaps, so delivery is rough: it tests whether
    delivery marks a move, not a speaker's state."""
    mgmt = [s for s in segments if is_management(s.speaker) and s.t1_ms - s.t0_ms >= 5000 and s.ts]
    ans = [(b, b.t0_ms - a.t1_ms) for a, b in pairwise(segments)
           if "Analyst" in a.speaker and is_management(b.speaker) and b.ts]

    def top(rows: list[tuple[Segment, float]]) -> list[datetime]:
        """Returns the start times of the top TOP_SHARE of rows by value."""
        keep = sorted(rows, key=lambda r: r[1], reverse=True)[:max(1, round(len(rows) * TOP_SHARE))] if rows else []
        return [s.ts for s, _ in keep]

    def words(s: Segment) -> int:
        """Returns the turn's word count, at least 1."""
        return max(1, len(s.text.split()))

    return {"claims": [c.ts for c in claims], "guidance": [c.ts for c in claims if c.kind == "guidance"],
            "novel": [c.ts for c in claims if c.novel],
            "new guidance": [c.ts for c in claims if c.novel and c.kind == "guidance"],   # the narrow ex-ante pick
            "answers": [s.ts for s, _ in ans],
            "fast talk": top([(s, words(s) / (s.t1_ms - s.t0_ms)) for s in mgmt]),
            "fillers": top([(s, len(FILLER.findall(s.text)) / words(s)) for s in mgmt]),
            "slow answer": top(ans)}


def grid(call: Path, benchmark: str, outcome: str = "move") -> Grid | None:
    """Loads 1 call onto its minute grid, None when it has under 30 minutes or a trade in under MIN_COVER of
    them."""
    segs, bars, claims = placebo_inputs(call, benchmark)
    t0, t1 = market.minute_of(segs[0].ts), market.minute_of(segs[-1].ts)
    n = int((t1 - t0).total_seconds() // 60) + 1
    if n < 30 or len(bars[(bars["ts"] >= t0) & (bars["ts"] <= t1)]) < MIN_COVER * n:
        return None
    mins = [t0 + timedelta(minutes=i) for i in range(n)]
    x = pd.Series([np.nan if touches_open(m) else abs(market.forward_window(bars, m)[OUTCOMES[outcome]])
                   for m in mins])
    qa_ms = qa_start_ms(segs)
    qa_ts = next((s.ts for s in segs if s.t0_ms >= qa_ms), None) if qa_ms is not None else None

    def at(ts: datetime) -> int:
        """Returns the grid index of the minute holding ts."""
        return int((market.minute_of(ts) - t0).total_seconds() // 60)

    return Grid(call.name, str(t0.astimezone(market.ET).date()), n, at(qa_ts) if qa_ts else n,
                x.rank(pct=True).to_numpy(), {k: np.array([at(t) for t in v if t], dtype=int)
                                              for k, v in events(segs, claims).items()})


def stat(g: Grid, idx: np.ndarray) -> float:
    """Returns the mean outcome at the event minutes inside the call, NaN when none has one."""
    x = g.x[idx[(idx >= 0) & (idx < g.n)]]
    return float(np.nanmean(x)) if np.isfinite(x).any() else np.nan


def sections(g: Grid) -> list[tuple[int, int]]:
    """Returns the minute spans of the prepared remarks and, when there is one, the Q&A."""
    return [(0, g.qi), (g.qi, g.n)] if 0 < g.qi < g.n else [(0, g.n)]


def placements(g: Grid, idx: np.ndarray, null: str, others: list[Grid], fam: str) -> list[np.ndarray]:
    """Returns the event minutes under every placement of 1 null, in an order that is the same for every family of
    the call (shift by shift, or other call by other call), so 1 draw can move all families together."""
    if null == "local":
        return [idx + s for s in LOCAL_SHIFTS]
    if null == "rotate":
        out = []
        for s in range(MIN_SHIFT_MIN, max(hi - lo for lo, hi in sections(g)) - MIN_SHIFT_MIN + 1):
            moved = idx.copy()
            for lo, hi in sections(g):
                m, span = (idx >= lo) & (idx < hi), hi - lo
                if span >= 2 * MIN_SHIFT_MIN:
                    moved[m] = lo + (idx[m] - lo + MIN_SHIFT_MIN + (s - MIN_SHIFT_MIN) % (span - 2 * MIN_SHIFT_MIN + 1)
                                     ) % span
            out.append(moved)
        return out
    out = []
    for o in others:   # cross: this call's count of events at the quantiles of the other call's positions
        src, dst, oi = sections(o), sections(g), np.sort(o.ev[fam])
        if o is g or len(src) != len(dst) or not len(oi):
            continue
        # a position is its section number plus how far through the section it is
        u = np.concatenate([k + (oi[(oi >= a) & (oi < b)] - a) / (b - a) for k, (a, b) in enumerate(src)])
        q = np.quantile(u, (np.arange(len(idx)) + 0.5) / len(idx))
        k = np.minimum(q.astype(int), len(dst) - 1)
        lo, hi = np.array(dst)[k].T
        out.append(np.minimum(hi - 1, lo + ((q - k) * (hi - lo)).astype(int)))
    return out


def pool(z: np.ndarray, null_z: list[np.ndarray], days: list[str], rng: np.random.Generator, draws: int,
         boot: int) -> dict:
    """Pools 1 family's per-call z (NaN for a skipped call): the mean, its 95% interval and 1-sided p (the share of
    resampled means at or under 0) from a bootstrap over call days, and the simulated means under the null with 1
    placement drawn per call."""
    ok = np.isfinite(z)
    zs, dd = z[ok], np.array(days)[ok]
    by_day = [np.flatnonzero(dd == d) for d in sorted(set(dd))]
    means = np.array([zs[np.concatenate([by_day[i] for i in rng.integers(len(by_day), size=len(by_day))])].mean()
                      for _ in range(boot)])
    sim = np.zeros(draws)
    for i in np.flatnonzero(ok):
        sim += null_z[i]
    sim /= ok.sum()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"n": int(ok.sum()), "mean": float(zs.mean()), "lo": float(lo), "hi": float(hi), "sim": sim,
            "wins": int((zs > 0).sum()), "p_boot": float(max(1, (means <= 0).sum()) / boot),
            "p": float((1 + (sim >= zs.mean()).sum()) / (1 + draws))}


def study(grids: list[Grid], null: str, seed: int = 0, draws: int = DRAWS, boot: int = BOOT) -> dict[str, dict]:
    """Runs 1 null over every family. Returns per family the pooled result of `pool` plus p_max, the p-value of its
    mean z against the largest standardized mean any family reaches in the same replicate."""
    rng = np.random.default_rng(seed)
    fams = list(grids[0].ev)
    z = {f: np.full(len(grids), np.nan) for f in fams}
    null_z = {f: [np.zeros(draws)] * len(grids) for f in fams}
    for i, g in enumerate(grids):
        pick = None   # the replicate's placement for this call, shared by its families
        for f in fams:
            idx = g.ev[f]
            fake = np.array([stat(g, p) for p in placements(g, idx, null, grids, f)]) if len(idx) >= MIN_EVENTS \
                else np.array([])
            if not len(fake):
                continue
            pick = rng.integers(len(fake), size=draws) if pick is None or pick.max() >= len(fake) else pick
            real, good = stat(g, idx), fake[np.isfinite(fake)]
            if np.isnan(real) or len(good) < MIN_NULL or good.std() == 0:
                continue
            z[f][i] = (real - good.mean()) / good.std()
            null_z[f][i] = np.nan_to_num((fake[pick] - good.mean()) / good.std())
    out = {f: pool(z[f], null_z[f], [g.day for g in grids], rng, draws, boot)
           for f in fams if np.isfinite(z[f]).sum() > 1}
    if not out:
        return out
    top = np.max([r["sim"] / r["sim"].std() for r in out.values()], axis=0)
    for r in out.values():
        r["p_max"] = float((1 + (top >= r["mean"] / r["sim"].std()).sum()) / (1 + draws))
    return out


def calibration(call: Path) -> dict | None:
    """Returns 1 call's flagged and total regular-session minutes in 3 places: inside the call, in the rest of the
    same day (the release is out, the call is not on), and over the call's clock minutes in the sessions on file
    before the call day (stock against its ETF from context.csv.gz, the same flag rule). None when the call has
    under MIN_REG_MIN regular minutes or no earlier sessions."""
    meta = json.loads((call / "call.json").read_text())
    segs, bars, _ = placebo_inputs(call, "etf")
    if not (call / "context.csv.gz").exists():
        return None
    reg = bars[market.session_key(bars["ts"]).str.endswith("-1")]
    inside = (reg["ts"] >= segs[0].ts) & (reg["ts"] <= segs[-1].ts)
    ctx = pd.read_csv(call / "context.csv.gz", parse_dates=["ts"])
    ctx = ctx[ctx["ts"] < market.day_start(segs[0].ts)]
    stock, etf = (ctx[ctx["sym"] == meta[k]][["ts", "px", "vol"]] for k in ("ticker", "etf"))
    if inside.sum() < MIN_REG_MIN or stock.empty or etf.empty:
        return None
    before = market.flag_abnormal(market.adjusted_returns(market.join_stock_etf(stock, etf)))
    hhmm = [pd.to_datetime(d["ts"]).dt.tz_convert(market.ET).dt.strftime("%H:%M") for d in (before, reg[inside])]
    parts = {"call": reg[inside], "day": reg[~inside], "prior": before[hhmm[0].isin(set(hhmm[1]))]}
    return {f"{k}_{c}": int(d["flagged"].sum()) if c == "k" else len(d) for k, d in parts.items() for c in "kn"}


def planted(g: Grid, share: float, rng: np.random.Generator) -> Grid:
    """Returns g with `share` of its claims moved onto the call's top-tenth outcome minutes: a known signal of a
    known size, to show what the test can detect. The planted claims sit exactly on the move, so this is the best
    case for that share."""
    idx, top = g.ev["claims"].copy(), np.flatnonzero(g.x >= 0.9)
    k = round(share * len(idx))
    if k and len(top):
        idx[rng.choice(len(idx), k, replace=False)] = rng.choice(top, k)
    return replace(g, ev=g.ev | {"claims": idx})


def main(argv=None):
    """Prints the pooled timing table for each null, the planted-signal sensitivity of the local null, then the
    flag-rate calibration. With --calibrate it prints each null's false-positive rate on random events instead."""
    ap = argparse.ArgumentParser()
    ap.add_argument("calls", type=Path, nargs="+", help="call folders")
    ap.add_argument("--benchmark", choices=market.BENCHMARKS, default=market.BENCHMARK)
    ap.add_argument("--outcome", choices=list(OUTCOMES), default="move",
                    help="rank minutes by the |adjusted return| or the peak volume multiple of the next 5 minutes")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--power", type=int, metavar="K",
                    help="how often K calls resampled from these detect each planted share (local null, boot p)")
    ap.add_argument("--calibrate", type=int, metavar="N",
                    help="N runs with each call's claims at uniformly random minutes: how often each p is under 0.05")
    args = ap.parse_args(argv)
    calls = [c for c in args.calls if (c / "call.json").exists()]
    grids = [g for g in (grid(c, args.benchmark, args.outcome) for c in calls) if g]
    if len(grids) < 2:
        ap.error("pooling needs 2 or more calls with bars inside the call")
    print(f"{len(grids)} of {len(calls)} calls on the grid, benchmark {args.benchmark}, outcome = within-call "
          f"percentile rank of the next 5 minutes' {args.outcome}, seed {args.seed}")
    rng = np.random.default_rng(args.seed)
    if args.calibrate:
        hits = {(null, k): 0 for null in NULLS for k in ("p", "p_boot")}
        for i in range(args.calibrate):
            fake = [replace(g, ev={"claims": rng.integers(g.n, size=len(g.ev["claims"]))}) for g in grids]
            for null in NULLS:
                r = study(fake, null, args.seed + i, draws=2000, boot=1000)["claims"]
                for k in ("p", "p_boot"):
                    hits[null, k] += r[k] < 0.05
        for null in NULLS:
            print(f"random events, null {null}: permutation p < 0.05 in {hits[null, 'p']}/{args.calibrate} runs, "
                  f"bootstrap p < 0.05 in {hits[null, 'p_boot']}/{args.calibrate}")
        return
    for null in NULLS:
        print(f"null {null}:")
        for fam, r in study(grids, null, args.seed).items():
            perm = f"  perm p {r['p']:.4f}  max-stat p {r['p_max']:.4f}" if null in PERM_OK else ""
            print(f"  {fam:<12} n={r['n']:<3} mean z {r['mean']:+.3f}  95% CI [{r['lo']:+.3f}, {r['hi']:+.3f}]  "
                  f"boot p {r['p_boot']:.4f}{perm}  z>0 in {r['wins']}/{r['n']}")
    for share in PLANT_SHARES:
        r = study([planted(g, share, rng) for g in grids], "local", args.seed)["claims"]
        print(f"planted: {share:.0%} of each call's claims moved onto its top-tenth minutes -> local mean z "
              f"{r['mean']:+.3f}  95% CI [{r['lo']:+.3f}, {r['hi']:+.3f}]  boot p {r['p_boot']:.4f}")
    if args.power:
        for share in (0.0, *PLANT_SHARES):   # 0% is the real timing, so its rate is the test's level here
            hit = 0
            for i in range(POWER_RUNS):
                some = [planted(grids[j], share, rng) for j in rng.integers(len(grids), size=args.power)]
                some = [replace(g, day=f"{g.day}-{j}") for j, g in enumerate(some)]   # a call drawn twice is 2 days
                hit += study(some, "local", args.seed + i, draws=200, boot=1000)["claims"]["p_boot"] < 0.05
            print(f"power with {args.power} calls, {share:.0%} planted: boot p < 0.05 in {hit}/{POWER_RUNS} resamples")
    cal = pd.DataFrame([r for r in map(calibration, calls) if r])
    if len(cal) > 1:
        tot = cal.sum()
        print(f"calibration, {len(cal)} calls with {MIN_REG_MIN}+ regular-session call minutes, flagged minutes: "
              + ", ".join(f"{name} {tot[k + '_k']}/{tot[k + '_n']} ({tot[k + '_k'] / tot[k + '_n']:.2%})" for k, name
                          in (("call", "in the call"), ("day", "rest of the call day"), ("prior", "earlier sessions"))))
        for k, name in (("day", "the rest of its day"), ("prior", "earlier sessions")):
            def ratio(d: pd.DataFrame, k: str = k) -> float:
                """Returns the pooled in-call flagged share over the control's."""
                t = d.sum()
                return (t["call_k"] / t["call_n"]) / (t[k + "_k"] / t[k + "_n"]) if t[k + "_k"] else np.inf
            boots = [ratio(cal.sample(len(cal), replace=True, random_state=int(rng.integers(1 << 31))))
                     for _ in range(BOOT)]
            lo, hi = np.percentile(boots, [2.5, 97.5])
            print(f"  the call vs {name}: ratio {ratio(cal):.1f} (95% CI {lo:.1f} to {hi:.1f}, calls resampled)")


if __name__ == "__main__":
    main()
