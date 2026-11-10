# CallMoves

Shows which statements in an earnings call the market reacted to. It finds the abnormal minutes in the stock's 1-minute prices (adjusted for its sector ETF), reads what was said just before each one, and ranks the likely drivers with their evidence and a confidence grade. Transcript, audio and price chart replay on 1 cursor. Live at https://callmoves.vercel.app, and anyone can submit a call at `/submit`.

## How a call is processed

Price first, transcript second: summarizing the call would surface what sounds important, not what changed expectations.

1. Align the transcript to wall-clock time (`audio_offset_ms` is the audio position of the call start).
2. Join the stock's 1-minute bars with its sector ETF's. Adjusted return = stock return minus ETF return. Flag minutes where it is far outside the trailing baseline, or volume spikes with a real move.
3. Keep the turns spoken in the 5 minutes before a flagged minute.
4. Extract claims (metric, value, period, kind, verbatim quote) with Claude, or rules without a key. A quote that is not in the transcript is dropped.
5. Judge novelty against the release and the prior quarter's materials. A claim already there cannot explain a move, unless it is an EPS or revenue figure more than 1% off analyst consensus. Only a consensus frozen at the report (`at_report`) or dated before the call (`before_call`) counts, an `after_call` one is shown but never scores.
6. Score = novelty x kind x |adjusted return over the next 5 min| x volume multiple x closeness to the flagged minute. The top 5 become moments graded high, medium or low. The wording is "associated with", never "caused".

`callmoves.ingest` builds the call folder from audio (faster-whisper on CPU), the release and prior documents (PDF or HTML) and bars. `run --publish` or `callmoves.publish` upserts the result into the Supabase table `callmoves_calls` (`db/schema.sql`). The GitHub Actions workflow `worker.yml` processes submissions from the queue, 1 per run. Without Supabase keys the web app reads `web/data/*.json`.

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

- `ANTHROPIC_API_KEY`: Claude extracts claims and judges novelty for a new call, otherwise the rules run.
- `POLYGON_API_KEY`: 1-minute bars from Massive (formerly Polygon), free plan. Required to ingest a call.
- `ALPHA_VANTAGE_KEY`: analyst consensus (25 requests a day), otherwise no consensus.
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`: audio upload (`ingest --upload`), publishing, the worker.

## Eval

Precision at 3 and 5 against hand-labeled move times:

```bash
.venv/bin/python -m callmoves.eval <call folder or JSON> --labels <labels.json>
```

Placebo check without labels: the real statement timing against the same claims shifted 15 to 60 minutes (`.venv/bin/python -m callmoves.eval data/*/ --placebo`). A call with no timing signal does not beat the shifts, and over several calls a sign test asks whether the real timing beats the median shift more often than half the time. News coverage almost always attributes the move to the release, not to a statement on the call (19 of 20 calls checked), so news-drafted labels exist only for Apple FY26 Q4.
