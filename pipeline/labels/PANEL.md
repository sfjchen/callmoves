# Analysis plan: the price-only panel

Written 2026-11-20 and committed before any panel bar was fetched (`out/panel/bars/` did not exist). `git log` on this file is the proof of order. Code: `callmoves/panel.py` at the commit that adds this file.

## Why

The library's 88 calls cannot say when in a call prices move, how big the call's move is next to the release's, or whether it lasts. Those need a call's start time and minute bars, not audio, so the panel takes every earnings release of the last 2 years for the library's tickers and their peers (161 tickers), about 10 times the library.

## What was seen before this plan

On the 74 development calls of the library (scratch run, sector-ETF benchmark, not stamped), the same quantities at small scale: the call's absolute move was a median 41% of release-plus-call, the EPS surprise tracked the pre-call move (rank correlation 0.43) and not the in-call move (-0.02), and the in-call move's sign matched the pre-call move's in 30 of 73 calls. The panel's statistics were chosen after seeing these, so the panel is where they are tested, and library calls are also reported apart (`not in library`).

Published numbers to compare with: Brochet, Kolev and Lerman (23,728 calls) put the first call hour's absolute return at a mean 1.3% (median 0.8%) against 3.8% (2.4%) for the announcement window. Ma et al. (552 calls) put prior close to call start at 3.43 points and call start to end at 0.62. Ozer (41,246 calls, 14.3M sentences) finds sentence tone explains 0.002% of minute returns.

## Sample

- Event: an 8-K with Item 2.02 accepted by EDGAR since 2024-12-02 whose release text states a call time (`panel.call_time`). No stated time means dropped, never guessed.
- Call-time accuracy is measured against the library's hand-set starts (`panel check`) and reported.
- Bars: Massive 1-minute trades, stock against SPY. An event is dropped when under 25% of the call hour's minutes trade or under 5 of its 10 control sessions exist.

## Statistics (all in `panel.describe`)

Windows: `ann` = last regular close before the call to its start, `call` = the 60 minutes from the start, `fwd` = end of that hour over the next 2 regular closes. Controls = the same clock hour on the 10 sessions before the call day.

1. Elevation: call-hour summed |1-minute return|, volume and flagged-minute rate over the controls'.
2. Size: median |ann|, median |call|, median call share |call| / (|ann| + |call|).
3. Surprise: rank correlation of the EPS surprise with `ann` and with `call`.
4. Persistence: rank correlation of `call` with `fwd`, and the call's coefficient with `ann` held fixed (`call|ann`).
5. Profile: activity over controls in 10-minute bins from 30 minutes before the start to 90 after.

Intervals: bootstrap over call days, 2,000 draws, seed 0.

## What counts as a finding

Tickers split in 2 halves by the parity of a hash of the ticker, fixed before any bar. A statement goes in the README as a finding only when its 95% interval excludes the null value (1 for a ratio, 0 for a correlation) with the same sign in both halves. Anything else is reported as a number with its interval and no claim. The cuts by session, surprise size, year and filing order are descriptive.

## Predictions

- Elevation above 1 in both halves, larger for regular-session calls than the library's 1.9x against the rest of the day, since controls here are ordinary days.
- Call share well under half, in line with the 2 papers.
- Surprise tracks `ann` and not `call`.
- Persistence: no prediction. A call move that is noise reverses (negative), one that is news holds (zero) or drifts (positive). Daily drift after earnings is a known effect, so only `call|ann` speaks to the call itself.

## Limits known in advance

- The stated call time is the scheduled one, and `call` is a fixed hour, not the real length (the library's median call runs about 55 minutes).
- After-hours and pre-market bars are thin, so their activity ratios are noisier and the flag rule fires on fewer minutes.
- 2 years, large caps, 1 market regime. No claim carries to small caps.
- SPY is the benchmark here and the sector ETF in the library, so levels differ between the 2.
