# CallMoves

Replays an earnings call on the stock's 1-minute prices: when the stock moved, and what was being said. It finds the abnormal minutes (adjusted for the sector ETF), reads what was said just before each one, and ranks the candidate statements with their evidence and a confidence grade. Transcript, audio and price chart replay on 1 cursor. Live at https://callmoves.sfjc.dev (also https://callmoves.vercel.app). Anyone can submit a call at `/submit`, and `/guess` plays a call up to its biggest moment and asks which way the stock went next (the page gets no data from after that moment until you answer).

## How a call is processed

Price first, transcript second: summarizing the call would surface what sounds important, not what changed expectations.

1. Align the transcript to wall-clock time (`audio_offset_ms` is the audio position of the call start). Speaker roles come from pyannote voices plus the call's shape (operator, prepared remarks, Q&A). Names come from the call's own introductions (`speakers.py`): Claude maps each voice to the person the operator or IR introduced, a name is kept only with a verbatim introducing quote and a close match to the spoken name, and an analyst's follow-up that the shape rules gave to management is relabeled and drops out of the claims.
2. Join the stock's 1-minute bars with its sector ETF's. Adjusted return = stock return minus ETF return. Flag minutes where it is far outside the trailing baseline (a return after minutes with no trade is scaled to 1 minute first), or volume spikes with a real move.
3. Keep the turns spoken in the 5 minutes before a flagged minute.
4. Extract claims (metric, value, period, kind, verbatim quote) with Claude, or rules without a key. A quote that is not in the transcript is dropped.
5. Judge novelty against the release and the prior quarter's materials. A claim already there cannot explain a move, unless it is an EPS or revenue figure more than 1% off analyst consensus. Only a consensus frozen at the report (`at_report`) or dated before the call (`before_call`) counts, an `after_call` one is shown but never scores.
6. Score = novelty x kind x |adjusted return over the statement's minute and the 4 after it| x volume multiple x closeness to the flagged minute. The top 5 become moments graded high, medium or low. The wording is "associated with", never "caused".

`callmoves.ingest` builds the call folder from audio (faster-whisper large-v3 and pyannote on a Modal GPU, `pipeline/callmoves/gpu.py`, or medium on the CPU when Modal is unset, or fails on a GitHub runner), the release and prior documents (PDF or HTML) and bars. `run --publish` or `callmoves.publish` upserts the result into the Supabase table `callmoves_calls` (`db/schema.sql`). The GitHub Actions workflow `worker.yml` processes submissions from the queue, up to 4 in parallel per run. Calls I add myself skip the queue: `CALLMOVES_LLM=cli python -m callmoves.worker --batch calls.json` runs the same steps locally and sends the Claude steps through the logged-in Claude Code CLI (`claude -p --json-schema`) instead of the API key. Player audio lives in the public Hugging Face dataset `jchen04/callmoves-audio` (the first 45 calls moved there byte for byte from Supabase Storage on 2026-11-17). Without Supabase keys the web app reads `web/data/*.json`.

## Run it

Needs Python 3.12+ and Node 22+.

```bash
cd pipeline
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/python -m callmoves.run fixtures/acme_q3_2026 --out ../web/data

cd ../web
npm install
npm run dev        # http://localhost:3000
```

Every call folder in `pipeline/data/` and the fixture ship with bars and cached Claude output (`llm_claims.json`), so a rerun needs no key. Keys go in `pipeline/.env`, each optional:

- `ANTHROPIC_API_KEY`: Claude extracts claims and judges novelty for a new call, otherwise the rules run. `CALLMOVES_LLM=auto` uses the key until its credits run out, then the Claude Code login, `CALLMOVES_LLM=cli` only the login. In both a failed Claude step stops the call.
- `POLYGON_API_KEY`: 1-minute bars from Massive (formerly Polygon), free plan. Required to ingest a call.
- `ALPHA_VANTAGE_KEY`: analyst consensus (25 requests a day), otherwise no consensus.
- `HF_TOKEN`: audio upload (`ingest --upload`) and the pyannote speaker model.
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`: publishing, the worker.

## Eval

What the tests support: the call itself moves prices, and nothing here shows which sentence did it. So a moment is a move plus what was being said, graded, never a cause.

### The call moves prices (1,090 calls, prices only)

`python -m callmoves.panel stats` needs no audio: every earnings release since 2024-12-02 of the library's tickers and their peers whose release states a call time (1,094 events on 138 tickers, `pipeline/panel/events.csv`), with 1-minute bars against SPY and the same clock hours of the 10 sessions before as controls. The plan (`pipeline/labels/PANEL.md`) was committed before any bar was fetched: 1 primary test, 3 secondary, and a result counts as a finding only when the 95% interval of all events and of both halves of the tickers excludes the null. 1,090 events have bars and 459 pass the rule that a price is under 10 minutes old (dropped: 321 of 422 pre-market calls, 5 of 164 in the regular session, 305 of 504 after hours). Log `callmoves-panel-stats-e9b4c23-20261122-2133.log`:

| Test (null) | All, 459 | Half A, 289 | Half B, 170 | Verdict |
| --- | --- | --- | --- | --- |
| In-call lift, primary (0) | 0.24 (0.16, 0.31) | 0.25 (0.15, 0.35) | 0.26 (0.10, 0.41) | finding |
| Call hour's move over an ordinary hour's (1) | 4.97 (4.20, 5.94) | 5.06 (4.05, 6.37) | 4.81 (3.78, 6.13) | finding |
| Share of control hours the call hour beats (0.5) | 0.84 (0.81, 0.87) | 0.82 (0.79, 0.86) | 0.87 (0.83, 0.91) | finding |
| EPS surprise tracks the pre-call move more than the in-call move (0) | 0.28 (0.13, 0.43) | 0.35 (0.16, 0.54) | 0.16 (-0.04, 0.34) | not shown |
| In-call move persists over the next session (0) | 0.12 (0.02, 0.23) | 0.08 (-0.04, 0.20) | 0.21 (0.04, 0.39) | not shown |

The in-call lift is the test that separates the call from the release: 10-minute bins are scored log(1 + |return| / the controls' mean) and compared at equal time since the release and equal session, between companies already in their call and companies still waiting for theirs (432 events, 2,127 in-call bins, 2,420 waiting). Prices move more once the call starts. It is an association with being in the call, since companies that hold their call sooner may differ, and by session only the regular session's interval excludes 0 (0.25, 0.14 to 0.39, against 0.22, -0.00 to 0.44 pre-market and 0.17, -0.03 to 0.36 after hours). Descriptive, no test: the median move is 3.39% from the prior close to the call start and 1.41% in the call hour, and the EPS surprise's rank correlation is 0.30 with the first and 0.02 with the second. Most of the earnings move is the release, and the call adds a smaller one of its own.

### No statement-level timing signal (91 calls)

`python -m callmoves.timing data/*/` takes event minutes from the transcript alone and asks whether the next 5 minutes' move ranks higher after a claim than after the same claims placed elsewhere: shifted 6 to 15 minutes (`local`), laid at another call's positions (`cross`), or rotated inside their section (`rotate`). Mean per-call z with a 95% interval from a bootstrap over call days:

| Calls | local | cross | rotate | Log |
| --- | --- | --- | --- | --- |
| Development, 73 of 77 on the grid | +0.01 (-0.19, +0.23) | +0.03 (-0.21, +0.24) | +0.28 (-0.27, +0.74) | `callmoves-timing-dev-e9b4c23-20261122-2139.log` |
| Held out, 14, pre-registered | +0.27 (-0.51, +0.92) | +0.13 (-0.40, +0.71) | -0.44 (-2.08, +0.95) | `callmoves-timing-heldout-e9b4c23-20261122-2141.log` |
| All 91, mixes both sets | +0.05 (-0.18, +0.28) | +0.02 (-0.21, +0.22) | +0.17 (-0.32, +0.64) | `callmoves-timing-all-e9b4c23-20261122-2138.log` |

The held-out test (`pipeline/labels/PREREG.md`, committed before those 14 calls were downloaded) predicted no effect and found none. What the test could see: with 5% of each call's claims planted on its top-tenth minutes the development set gives +0.34 (+0.13, +0.56), with 2% it gives +0.15 (-0.06, +0.36), and 14 calls reject in 71 of 200 resamples at 5% and 157 of 200 at 10%. So fewer than about 1 claim in 20 lining up with a move cannot be ruled out. On claims at random minutes the nulls reject in 5 (local), 2 (cross) and 14 (rotate) of 100 runs at p < 0.05 (`callmoves-timing-calibrate-e9b4c23-20261122-2138.log`), which is why rotate is shown only for continuity: the old headline, a sign test on it (46 of 75, p = 0.032, before (d67a079)), read the shape every call shares. With volume as the outcome the local null gives +0.18 (-0.06, +0.41) over all calls (`callmoves-timing-volume-all-e9b4c23-20261122-2142.log`) and +0.42 (-0.13, +0.83) held out (`callmoves-timing-heldout-volume-e9b4c23-20261122-2141.log`). Of the 6 other event families, analyst answers came out negative on the held-out calls (-0.73, -1.04 to -0.42) after +0.13 on the development set, so no family is claimed.

The price side is real: in 24 calls with regular-session minutes, 3.37% of call minutes are flagged against 1.82% in the rest of the call day (ratio 1.9, 95% CI 1.1 to 2.8) and 0.52% over the same clock minutes of earlier sessions (ratio 6.4, 3.2 to 13.4, same development log). News coverage almost always attributes the move to the release, not to a statement on the call (19 of 20 calls checked), so news-drafted labels exist only for Apple FY26 Q4.

### Benchmarks

`market.py` can measure the stock against its sector ETF (`etf`, shipped), a market model fitted on the 3 sessions before the call (betas on SPY and the ETF, `market`), or the same plus an equal-weight peer basket (`peers`). `pipeline/data/<call>/context.csv.gz` holds the 1-minute bars of SPY, the peers and the cross-asset ETFs (TLT, UUP, HYG, GLD, USO) for every call, so all 3 run offline. Claims against the local null over all 91 calls (`timing data/*/ --benchmark <b>`):

| Benchmark | Mean z (95% CI) | Log |
| --- | --- | --- |
| etf | +0.05 (-0.18, +0.28) | `callmoves-timing-all-e9b4c23-20261122-2138.log` |
| market | +0.08 (-0.16, +0.32) | `callmoves-timing-market-all-e9b4c23-20261122-2141.log` |
| peers | +0.12 (-0.16, +0.39) | `callmoves-timing-peers-all-e9b4c23-20261122-2142.log` |

No benchmark changes the answer, so the ETF stays: it is the one a reader can check on any chart.

### Attribution labels

`pipeline/labels/PROTOCOL.md` defines the unit (123 flagged price events in 41 calls, frozen in `flags.json`) and 3 raters who never see CallMoves' moments or scores: `opus-blind` (Claude Opus, price table and 5 min of transcript), `agent-protocol` (Claude Sonnet agents following the protocol on richer packets, `labels packets`, answers in `agent_protocol.json`) and me on `/label`. Current report (`labels report`, log `callmoves-labels-report-e9b4c23-20261122-2132.log`):

| Tier | Labeled flags | Precision at 3 | Precision at 5 |
| --- | --- | --- | --- |
| gold (squawk headline quotes a sentence within 2 min) | 2 | 0/2 | 0/2 |
| silver (both model raters agree) | 55 | 0.59 (26/44) | 0.56 (29/52) |
| human | 0 so far | | |

Cohen's kappa between the 2 model raters is 0.41 on all 123 flags. Silver labels are model labels, so these precisions say the shipped ranking agrees with 2 independent model readings, not that it is right.

`labels align` checks the clock against 28 squawk headlines on X that quote a call sentence: in 12 calls they post at least 1.5 min after the sentence (median 4.0, none before it, log `callmoves-align-64f11f5-20261111-1612.log`), so no call's timing is off by more than the posting lag.

`eval data/*/ --predict` asks whether claim features predict the next 5 minutes: a ridge model, left out 1 call at a time. Over 87 calls its mean Spearman correlation with the realized move is +0.010, against -0.045 for trailing volatility and -0.051 for the score's ex-ante part (log `callmoves-predict-e9b4c23-20261122-2138.log`). Nothing predicts the move before it happens, so no prediction ships, and the moments stay a price-first reading.

### Study

The time-to-insight study (`/study`, 2 timed trials, replay with and without moments) is live with no testers yet. It runs at the Final Project Expo, where "Next participant on this device" resets the shared laptop between visitors, and `/study/results` shows the aggregates.

## Cost and speed

Ingest and run write `stats.json` per call: GPU model and seconds (priced at Modal's list rate plus the container's memory and CPU), Claude tokens (priced at Sonnet 5.5 list rates) and wall time. Over the 73 of 91 calls with both GPU and Claude cost recorded (`eval data/*/ --stats`, log `callmoves-stats-e9b4c23-20261122-2138.log`):

| Per call | Median | Range |
| --- | --- | --- |
| Audio | 60.6 min | 37.4 to 87.9 |
| Processing (transcription, diarization, bars, claims) | 7.3 min | 3.8 to 100.6 |
| GPU (49 on an A10, 3 A10G, 20 L4, 1 T4) | $0.13 | $0.06 to $0.20 |
| Claude | $0.13 | $0.05 to $2.42 |
| Total | $0.27 | $0.12 to $2.59 |

The spec's target was a 60-minute call in under 15 minutes for under $2. The 73 calls cost $35.43 together at list prices. Calls I added through the Claude Code CLI were not billed, and their Claude figure is the list-price equivalent of the tokens, which the CLI's own context inflates. A whole worker job, with the runner's setup, the downloads and the audio upload, took 10.2 min at the median (6.6 to 13.0, log `callmoves-worker-jobs-3564117-20261110-1837.log`). The call page shows its own line under "How it's scored".

## Limits

- Audio is used for transcription, voices and names only. CallMoves does not score tone of voice (pitch, pace, hesitation), and it does not read news or macro releases: SPY, rates, dollar, credit, gold and oil bars sit beside each call as context, not as inputs to the score. Nothing is trained, so more calls add test power, not accuracy.
- The timing test is exploratory on the 77 development calls (thresholds, speaker rules and the nulls were tuned there) and confirmatory only on the 14 held-out ones. Calls that share a day are resampled together, calls that share only a sector are not.
- The panel uses the scheduled call time and a fixed 60 minutes, not the real start and length, covers 2 years of large caps, and loses 58% of its events to the price-freshness rule, nearly all outside the regular session. Its in-call lift compares different companies at the same time since the release.
- The call start is the operator's opening line ("welcome", "being recorded", hold messages skipped, `transcript.call_start_ms`), and `ingest --audio-offset` overrides it. All 45 Quartr recordings open on that line within 4.5 s, so `audio_offset_ms` is 0 for 42 calls, 110 for META, 180 for PNC and 4460 for DHR. No source gives the wall-clock time the operator started: stockanalysis.com has only the event date and releases give the scheduled time. The Quartr file name carries its upload time, for the first 20 calls 2.2 to 25.5 min (median 4.2) after the scheduled start plus the recording length, so an operator who started late started at most that late. A late start shifts every statement by that much.
- Before 9:30 and after 16:00 ET a sector ETF trades rarely, so its last price can be minutes old and its catch-up shows up as a stock-minus-ETF move in a later minute. Many calls start at 8:00 to 9:00 ET, so their first half runs in these thin pre-market bars.
- Semis and AI hardware calls use SMH as the sector ETF, which holds several of the same stocks (NVDA, TSM, AVGO among them), so part of a large holding's own move is subtracted out. Most of these calls run after the close, in thin extended-hours bars, so they count for no regular-session placebo windows. TSM and Kioxia (KXIAY, an OTC ADR) talk while the US market is closed, so their reaction is the next open.
- Calls are volatile: up to 40% of in-call minutes are flagged (HON, mostly pre-market), about 8% for the median call, so a statement can land near a flag by chance. That is why moments carry a grade and the wording is "associated with".
