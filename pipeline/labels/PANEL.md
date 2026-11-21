# Analysis plan: the price-only panel

Written 2026-11-20 and committed before any panel bar was fetched (`out/panel/bars/` did not exist). `git log` on this file is the proof of order. Code: `callmoves/panel.py` at the commit that adds this file.

## Why

The library's 88 calls cannot say when in a call prices move, how big the call's move is next to the release's, or whether it lasts. Those need a call's start time and minute bars, not audio, so the panel takes every earnings release of the last 2 years for the library's tickers and their peers (161 tickers), about 10 times the library.

## What was seen before this plan

On the 74 development calls of the library (scratch run, sector-ETF benchmark, not stamped), the same quantities at small scale: the call's absolute move was a median 41% of release-plus-call, the EPS surprise tracked the pre-call move (rank correlation 0.43) and not the in-call move (-0.02), and the in-call move's sign matched the pre-call move's in 30 of 73 calls. The panel's statistics were chosen after seeing these, so the panel is where they are tested, and library calls are also reported apart (`not in library`).

Published numbers to compare with: Brochet, Kolev and Lerman (23,728 calls) put the first call hour's absolute return at a mean 1.3% (median 0.8%) against 3.8% (2.4%) for the announcement window. Ma et al. (552 calls) put prior close to call start at 3.43 points and call start to end at 0.62. Ozer (41,246 calls, 14.3M sentences) finds sentence tone explains 0.002% of minute returns.

## Sample

- Event: an 8-K with Item 2.02 accepted by EDGAR since 2024-12-02 whose release text states a call time (`panel.call_time`). No stated time means dropped, never guessed. The call's day comes from the text (a date, "today", "tomorrow") or, failing that, from a rule that is reported apart (`day stated` column).
- Call-time accuracy is measured against the library's hand-set starts (`panel check`) and reported. That checks the schedule, not when the first word was spoken.
- Bars: Massive 1-minute trades, stock against SPY. A bar is stamped at its minute's start, so the price known at time t is the bar that started before t, and it gives no return when it is over 10 minutes old. The rule is the same on call days and control days. An event is dropped when its call hour is stale, under 25% of its minutes trade, or under 5 of its 10 control hours pass the same rule. Drops are reported by session.

## Statistics (all in `panel.describe`)

Windows: `pre` = last regular close before the call to its start (it holds the release, and is not the release's own window), `call` = the 60 minutes from the start, `fwd` = from the first regular-session bar 5 or more minutes after that hour to the regular close of the following session, so `call` and `fwd` share no price. Controls = the same clock windows on the 10 sessions before the call day.

Primary, the 1 test this panel is for:

- **In-call lift.** Ordinary-day controls show that an earnings day is unusual, not that the call is: prices are still digesting the release when the call starts. So each 10-minute bin from the release to the end of the call hour is scored log(1 + |return| / the controls' mean for that clock bin), and bins are compared inside cells of equal release age (10-minute steps, up to 4 hours) and session, where some events are already in their call and others are still waiting for theirs. The lift is the in-call bins' mean score minus the waiting bins'. Null 0. It uses only events whose 8-K reached EDGAR before the call.

Secondary:

1. Elevation against ordinary days: `abs x` (mean |call| over the controls' mean, null 1) and `rank` (share of its control hours a call hour beats, null 0.5). This is release plus call and is worded that way.
2. Surprise: the rank correlation of the EPS surprise with `pre` minus its correlation with `call`, null 0.
3. Persistence: the call's coefficient when `fwd`'s rank is regressed on the ranks of `call` and `pre`, null 0. An association over 2 sessions, not a claim of permanence.

Descriptive, no test and no claim: median |pre| and |call|, the call's share |call| / (|pre| + |call|) next to the same share on control days (a 1-hour window beside a 17-hour one is small on any day), volume and flagged-minute ratios (after hours these count trades as much as moves), the 10-minute profile, and every cut by session, surprise size, year, filing order and release-to-call gap.

Intervals: the wider of 2 bootstraps, over call days and over tickers, 1,000 draws each, seed 0.

## What counts as a finding

Tickers split in 2 halves by the parity of a hash of the ticker, fixed before any bar. The primary and the 3 secondary tests go in the README as findings only when the 95% interval of all events excludes the null and both halves have the same sign with intervals that exclude it too. Otherwise the number is reported with its interval and the words "not shown". 4 tests, no correction: the halves rule is the guard, and the primary is named.

## Predictions

- In-call lift: no prediction. The library's flagged-minute rate was 1.9 times the rest of the call day, which mixes the call with the release's tail, and this is the test that splits them.
- Elevation above 1 in both halves.
- Surprise tracks `pre` more than `call`.
- Persistence: no prediction. A call move that is noise reverses (negative), one that is news holds (zero) or drifts (positive).

## Limits known in advance

- The stated call time is the scheduled one, and `call` is a fixed hour, not the real length (the library's median call runs about 55 minutes).
- EDGAR's acceptance time is when the 8-K arrived, which can trail the newswire by minutes. An 8-K filed after the call began is left out of the primary test.
- The in-call lift compares different companies at the same release age. A company that schedules its call sooner may differ in ways the controls do not remove, so the lift is an association with being in the call.
- After-hours and pre-market bars are thin. The freshness rule drops more of those events, and the drop counts say how many.
- 2 years, large caps, 1 market regime. No claim carries to small caps.
- SPY is the benchmark here and the sector ETF in the library, so levels differ between the 2.

## Amendment, same day, before any bar was fetched

The first version of this plan (`b0eea3b`) was reviewed by 2 models before the fetch. They found that a bar stamped at its minute's start put the call's first minute in the window before it, that a stale after-hours price could carry an earlier move into the call hour, that empty control hours read as quiet ones, that `call` and `fwd` shared a price (which fakes a reversal), that a morning call whose 8-K is filed that evening was placed on the next day, and that elevation against ordinary days cannot fail and does not isolate the call. This version fixes each, adds the in-call lift as the primary test, and moves the size and share numbers to descriptive. `out/panel/bars/` still did not exist at this commit.

## Event table, before any bar

`panel events` gives 1,094 events on 138 tickers (log `callmoves-panel-events3.log`, not stamped: it reads SEC and yfinance live). 153 releases stated no call time and were dropped. Against the library's hand-set starts the first parser was within 5 minutes on 74 of 77 calls. The 3 misses were 2 parser bugs (a release filed the evening before its date, a later investor event's time picked over the call's), fixed before the fetch, after which it matches 76 of 76. That score is in-sample, since the fixes came from the same check.
