from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from callmoves.models import CallMeta, Claim, Segment

ET = timezone(timedelta(hours=-4))
T0 = datetime(2026, 10, 13, 10, 0, tzinfo=ET)


def meta(**kw) -> CallMeta:
    """Returns a CallMeta for ticker X starting at T0, with fields overridden by kw."""
    return CallMeta(**{"ticker": "X", "quarter": "Q1", "call_start": T0, "etf": "IGV", **kw})


def segs(rows) -> list[Segment]:
    """Builds Segments from (minute after T0, speaker, text) rows, numbered in order and already aligned."""
    return [Segment(seg_id=i, t0_ms=m * 60_000, t1_ms=0, speaker=sp, text=tx, ts=T0 + timedelta(minutes=m))
            for i, (m, sp, tx) in enumerate(rows)]


def claim(i, seg: Segment, **kw) -> Claim:
    """Returns a novel guidance Claim quoting seg, with fields overridden by kw."""
    return Claim(**{"claim_id": i, "seg_id": seg.seg_id, "speaker": seg.speaker, "quote": seg.text, "metric": "m",
                    "kind": "guidance", "ts": seg.ts, "novel": True, **kw})


def make_bars(n=120, stock_bump=None, etf_bump=None, vol_bump=None, seed=1):
    """Flat-ish synthetic bars. bumps are {index: value}."""
    rng = np.random.default_rng(seed)
    stock = rng.normal(0, 0.0003, n)
    etf = rng.normal(0, 0.0003, n)
    vol = np.full(n, 10000.0)
    for i, v in (stock_bump or {}).items(): stock[i] += v
    for i, v in (etf_bump or {}).items(): etf[i] += v
    for i, v in (vol_bump or {}).items(): vol[i] *= v
    return pd.DataFrame({
        "ts": [T0 + timedelta(minutes=i) for i in range(n)],
        "px": 100 * np.cumprod(1 + stock), "etf_px": 50 * np.cumprod(1 + etf), "vol": vol,
    })
