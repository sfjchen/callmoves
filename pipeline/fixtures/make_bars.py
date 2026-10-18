"""Generate the synthetic 1-minute bars for the ACME fixture. Seeded, so the output is stable.

Planted events (ET, 2026-10-13):
  10:13-10:15  +1.8% on 4x volume   -> the FY guidance raise and buyback (call 12:10 in, segment at 10:12:10)
  10:20-10:21  +0.8% stock AND ETF   -> sector-wide move, adjusted return stays flat (should not score)
  10:31-10:33  -1.2% on 3x volume    -> the gross margin warning (segment at 10:31:00)
Run: python fixtures/make_bars.py
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(20261013)
ET = timezone(timedelta(hours=-4))
start = datetime(2026, 10, 13, 8, 30, tzinfo=ET)
n = 211  # 08:30 to 12:00
ts = [start + timedelta(minutes=i) for i in range(n)]

market = rng.normal(0, 0.0006, n)            # common factor
etf_ret = market + rng.normal(0, 0.0003, n)
stock_ret = 0.9 * market + rng.normal(0, 0.0007, n)
vol = rng.lognormal(mean=np.log(18000), sigma=0.25, size=n)

def idx(h, m): return (h - 8) * 60 + (m - 30)

# release gap at the open (09:30): stock +3% vs ETF, heavy volume
i = idx(9, 30); stock_ret[i] += 0.03; vol[i] *= 6
# guidance raise
for k, r, v in ((idx(10, 13), 0.009, 4.5), (idx(10, 14), 0.006, 4.0), (idx(10, 15), 0.003, 2.5)):
    stock_ret[k] += r; vol[k] *= v
# sector-wide move, both legs
for k in (idx(10, 20), idx(10, 21)):
    stock_ret[k] += 0.004; etf_ret[k] += 0.004; vol[k] *= 1.8
# margin warning
for k, r, v in ((idx(10, 31), -0.006, 3.2), (idx(10, 32), -0.004, 3.0), (idx(10, 33), -0.002, 2.0)):
    stock_ret[k] += r; vol[k] *= v

px = 148.0 * np.cumprod(1 + stock_ret)
etf_px = 96.0 * np.cumprod(1 + etf_ret)
df = pd.DataFrame({"ts": [t.isoformat() for t in ts], "px": px.round(3), "etf_px": etf_px.round(3), "vol": vol.round(0).astype(int)})
out = Path(__file__).parent / "acme_q3_2026" / "bars.csv"
df.to_csv(out, index=False)
print(f"wrote {out} ({len(df)} bars)")
