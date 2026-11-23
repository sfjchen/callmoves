# CallMoves

Shows which statements in an earnings call the market reacted to. It finds the abnormal minutes in the stock's 1-minute prices (adjusted for its sector ETF), reads what was said just before each one, and ranks the likely drivers with their evidence and a confidence grade. Transcript, audio and price chart replay on 1 cursor. Live at https://callmoves.sfjc.dev (also https://callmoves.vercel.app). Anyone can submit a call at `/submit`, and `/guess` plays a call up to its biggest moment and asks which way the stock went next (the page gets no data from after that moment until you answer).

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

Precision at 3 and 5 against hand-labeled move times:

```bash
.venv/bin/python -m callmoves.eval <call folder or JSON> --labels <labels.json>
```

Placebo check without labels (`.venv/bin/python -m callmoves.eval data/*/ --placebo`): the real statement timing against 2 nulls. `--null rotate` (default) rotates the claims inside their own section, prepared remarks or Q&A, by 10 minutes or more, so a shifted claim keeps its section and bars. `--null cross` keeps the call's claims and lays them at other calls' timings, which asks whether the real timing beats the usual shape of a call. A sign test asks whether the real timing beats the null's median in more than half the calls. At d67a079 over 75 calls, rotate gives 46 of 75 (p = 0.032, log `callmoves-placebo-etf-d67a079-20261118-0301.log`) and cross 41 of 75 (p = 0.24, log `callmoves-placebo-cross-d67a079-20261118-0302.log`). On the 28 calls added after both nulls were written, rotate gives 16 of 28 (p = 0.29, log `callmoves-placebo-heldout-d67a079-20261118-0306.log`) and cross 18 of 28 (p = 0.09, log `callmoves-placebo-heldout-cross-d67a079-20261118-0306.log`). So the timing signal is weak: some of it is the shape every call shares. Before (64f11f5) a wrap-around shift over 45 calls gave 29 of 45 (p = 0.036). News coverage almost always attributes the move to the release, not to a statement on the call (19 of 20 calls checked), so news-drafted labels exist only for Apple FY26 Q4.

### Benchmarks

`market.py` can measure the stock against its sector ETF (`etf`, shipped), a market model fitted on the 3 sessions before the call (betas on SPY and the ETF, `market`), or the same plus an equal-weight peer basket (`peers`). `pipeline/data/<call>/context.csv.gz` holds the 1-minute bars of SPY, the peers and the cross-asset ETFs (TLT, UUP, HYG, GLD, USO) for every call, so all 3 run offline. Placebo (rotate) at d67a079 over 75 calls (`eval data/*/ --placebo --benchmark <b>`):

| Benchmark | Real beats median shift | Sign test p | Log |
| --- | --- | --- | --- |
| etf | 46/75 | 0.032 | `callmoves-placebo-etf-d67a079-20261118-0301.log` |
| market | 46/75 | 0.032 | `callmoves-placebo-market-d67a079-20261118-0301.log` |
| peers | 42/75 | 0.18 | `callmoves-placebo-peers-d67a079-20261118-0302.log` |

The market model ties the ETF, so the ETF stays: it is the one a reader can check on any chart.

### Attribution labels

`pipeline/labels/PROTOCOL.md` defines the unit (123 flagged price events in 41 calls, frozen in `flags.json`) and 3 raters who never see CallMoves' moments or scores: `opus-blind` (Claude Opus, price table and 5 min of transcript), `agent-protocol` (Claude Sonnet agents following the protocol on richer packets, `labels packets`, answers in `agent_protocol.json`) and me on `/label`. At d67a079 (`labels report`, log `callmoves-labels-report-d67a079-20261118-0307.log`):

| Tier | Labeled flags | Precision at 3 | Precision at 5 |
| --- | --- | --- | --- |
| gold (squawk headline quotes a sentence within 2 min) | 1 | 0/1 | 0/1 |
| silver (both model raters agree) | 55 | 0.58 (26/45) | 0.55 (28/51) |
| human | 0 so far | | |

Cohen's kappa between the 2 model raters is 0.41 on all 123 flags. Silver labels are model labels, so these precisions say the shipped ranking agrees with 2 independent model readings, not that it is right.

`labels align` checks the clock against 28 squawk headlines on X that quote a call sentence: in 12 calls they post at least 1.5 min after the sentence (median 4.0, none before it, log `callmoves-align-64f11f5-20261111-1612.log`), so no call's timing is off by more than the posting lag.

`eval data/*/ --predict` asks whether claim features predict the next 5 minutes: a ridge model, left out 1 call at a time. Over 72 calls its mean Spearman correlation with the realized move is -0.005, against -0.065 for trailing volatility and -0.043 for the score's ex-ante part (log `callmoves-predict-d67a079-20261118-0307.log`). Nothing predicts the move before it happens, so no prediction ships, and the moments stay a price-first reading.

### Study

The time-to-insight study (`/study`, 2 timed trials, replay with and without moments) is live with no testers yet. It runs at the Final Project Expo, where "Next participant on this device" resets the shared laptop between visitors, and `/study/results` shows the aggregates.

## Cost and speed

Ingest and run write `stats.json` per call: GPU model and seconds (priced at Modal's list rate plus the container's memory and CPU), Claude tokens (priced at Sonnet 5.5 list rates) and wall time. Over the 54 of 75 calls with both GPU and Claude cost recorded (`eval data/*/ --stats`, log `callmoves-stats-d67a079-20261118-0305.log`):

| Per call | Median | Range |
| --- | --- | --- |
| Audio | 60.6 min | 38.3 to 78.0 |
| Processing (transcription, diarization, bars, claims) | 7.4 min | 3.0 to 36.5 |
| GPU (50 on an A10, 1 A10G, 2 L4, 1 T4) | $0.14 | $0.06 to $0.20 |
| Claude | $0.07 | $0.05 to $0.89 |
| Total | $0.22 | $0.14 to $1.03 |

The spec's target was a 60-minute call in under 15 minutes for under $2. The 54 calls cost $15.41 together. A whole worker job, with the runner's setup, the downloads and the audio upload, took 10.2 min at the median (6.6 to 13.0, log `callmoves-worker-jobs-3564117-20261110-1837.log`). The call page shows its own line under "How it's scored".

## Limits

- Audio is used for transcription, voices and names only. CallMoves does not score tone of voice (pitch, pace, hesitation), and it does not read news or macro releases: SPY, rates, dollar, credit, gold and oil bars sit beside each call as context, not as inputs to the score. Nothing is trained, so more calls add test power, not accuracy.
- The placebo is exploratory: thresholds and speaker rules were tuned on the first 20 calls, 10 calls share XLF (4 of them on 2026-10-13) and others share a sector and a day, so they are not independent, and it uses the offline rule extractor on every turn, not Claude on the price-first candidates the site shows.
- A 1-hour call allows only 5 to 12 within-call shifts, so no single call can reach p = 0.05.
- The call start is the operator's opening line ("welcome", "being recorded", hold messages skipped, `transcript.call_start_ms`), and `ingest --audio-offset` overrides it. All 45 Quartr recordings open on that line within 4.5 s, so `audio_offset_ms` is 0 for 42 calls, 110 for META, 180 for PNC and 4460 for DHR. No source gives the wall-clock time the operator started: stockanalysis.com has only the event date and releases give the scheduled time. The Quartr file name carries its upload time, for the first 20 calls 2.2 to 25.5 min (median 4.2) after the scheduled start plus the recording length, so an operator who started late started at most that late. A late start shifts every statement by that much.
- Before 9:30 and after 16:00 ET a sector ETF trades rarely, so its last price can be minutes old and its catch-up shows up as a stock-minus-ETF move in a later minute. Many calls start at 8:00 to 9:00 ET, so their first half runs in these thin pre-market bars.
- Semis and AI hardware calls use SMH as the sector ETF, which holds several of the same stocks (NVDA, TSM, AVGO among them), so part of a large holding's own move is subtracted out. Most of these calls run after the close, in thin extended-hours bars, so they count for no regular-session placebo windows. TSM and Kioxia (KXIAY, an OTC ADR) talk while the US market is closed, so their reaction is the next open.
- Calls are volatile: up to 40% of in-call minutes are flagged (HON, mostly pre-market), about 8% for the median call, so a statement can land near a flag by chance. That is why moments carry a grade and the wording is "associated with".
