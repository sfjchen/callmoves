# Pre-registration: statement timing on 14 held-out calls

Written 2026-11-20 and committed before any of the 14 calls below was downloaded, transcribed or priced. `git log` on this file is the proof of order.

## What was seen first (development set)

`python -m callmoves.timing data/*/ --power 14` at `21285d5` on the 77 calls then in the repo, 73 on the grid (log `callmoves-timing-21285d5-20261120-1927.log`). Every event family, outcome and null was looked at there and the nulls were revised after 2 reviews, so these numbers are exploratory:

- Claims, local null: mean z -0.03 (95% CI -0.23 to +0.19, 73 calls). Cross null: -0.04 (-0.24 to +0.16, 73 calls). Rotate null: +0.18 (-0.36 to +0.64, 67 calls).
- The old headline (sign test on the rotate null, 46 of 75, p = 0.032) reads the shape of a call, not its statements: claims and volatility both crowd the first 40% of a call, and the 2 nulls that keep a statement's place in the call show nothing.
- Planted signal, best case (planted claims sit exactly on the move): 5% of each call's claims on its top-tenth minutes gives mean z +0.30 (CI +0.10 to +0.50), 2% gives +0.07 (CI -0.14 to +0.28). The development set could see 1 claim in 20 lining up, not 1 in 50.
- Level on random events (claims at uniformly random minutes, 100 runs, p < 0.05): cross 2 permutation and 4 bootstrap (log `callmoves-timing-calibrate-4777296-20261120-1925.log`, cross unchanged since), local 0 and 1 (scratch run of the final local null), rotate 13 and 11. So local is conservative, cross is at its level and rotate runs hot.
- Price side: 42 of 1,245 regular-session call minutes flagged (3.37%) against 47 of 2,588 in the rest of the call day (1.82%, ratio 1.9, CI 1.1 to 2.7) and 32 of 6,112 over the same clock minutes of earlier sessions (0.52%, ratio 6.4, CI 3.2 to 13.2), 24 calls.

## Held-out set

The most recent quarterly call of PLTR, NOW, CRWV, NET, DDOG, TSLA, IBM, NFLX, PANW, CSCO, SHOP, UBER, APP and INTU (held 2026-10-20 to 2026-11-19, sources in `~/dev/logs/callmoves-batch-ai.json`), folders `data/<ticker>_<quarter>`. Chosen as AI-exposed large caps missing from the library, before any of their bars were fetched. A call that fails ingest or falls under the grid's floors (30 minutes, a trade in 25% of them) is reported as dropped, not replaced.

## Test

- Command: `python -m callmoves.timing <the 14 folders>`, benchmark `etf`, outcome `move`, seed 0, with `timing.py`, `claims.py`, `novelty.py`, the flag rule in `market.py` and `score.touches_open` as of `21285d5`.
- Primary: family `claims`, null `local`, mean per-call z with its day-cluster bootstrap 95% CI and 1-sided bootstrap p, alpha 0.05. A positive result counts only if the cross null's CI also excludes 0, because a peak shared by claims and volatility can pass the local null.
- Secondary, reported in full whatever they show: the other 7 families, the cross and rotate nulls, the volume outcome, the max-statistic p.
- Prediction: no effect. I expect the CI to include 0.
- Power (same log): 14 calls resampled from the development set reject in 12 of 200 runs with nothing planted (6% against 5%), 57 of 200 with 5% planted, 143 of 200 with 10% and 200 of 200 with 20%. This set alone can confirm 1 claim in 10 lining up, not less. Its job is to show the pipeline was not tuned to the calls it is tested on. The pooled number over all calls is reported next to it and labeled as mixing both sets.
- 12 of the 14 calls run after the close and 2 before the open, so the flag-rate calibration (regular-session minutes only) is not re-tested here.

## What changes between the sets

Transcription changed before the held-out calls (`e7bbf05`: beam 5, no conditioning on previous text, a second pass without the voice filter when a transcript has a minute of thin time), because the filter had dropped speech in development calls, and the 10 worst of those are transcribed again in the same batch. The claim extractor, the outcome, the nulls and the pooling did not change. If a bug in `timing.py` is found after this commit, both the frozen and the fixed result are reported.

## Amendment, same day, before the batch left the job queue

The first version of this file (`e9073c7`) froze `timing.py` at `4777296`, whose local null used only events 15+ minutes from either end of the call. A check on random events then showed that version rejecting 13 to 21 times in 100, so `21285d5` went back to every event (0 to 1 in 100). The numbers above are from `21285d5`. No held-out call had been downloaded: `~/dev/logs/callmoves-ai-batch.log` shows the batch still waiting for a job slot when this was committed.
