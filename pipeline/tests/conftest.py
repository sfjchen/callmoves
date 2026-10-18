from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

ET = timezone(timedelta(hours=-4))
T0 = datetime(2026, 10, 13, 10, 0, tzinfo=ET)


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


@pytest.fixture
def flat_bars():
    return make_bars()
