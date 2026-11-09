# CallMoves

Which statements in an earnings call the market reacted to. Give it a timestamped transcript, the earnings release, last quarter's materials and 1-minute prices for the stock and its sector ETF. It finds the abnormal minutes, reads what was said just before each one, and ranks the likely drivers with their evidence and a confidence grade. Transcript, audio and chart replay on 1 cursor.

Status: the pipeline runs end to end on a fictional fixture and on real calls (WFC Q3 2026 is deployed at callmoves.vercel.app). `callmoves.ingest` builds a call folder from audio (faster-whisper on CPU), the release and prior-quarter documents (PDF or HTML) and 1-minute bars including pre and post market. Player audio lives in a public Supabase Storage bucket, the call JSON still ships in `web/data/`.

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

The fixture and the WFC call ship with cached Claude output (`llm_claims.json`) and bars, WFC also with consensus (`estimates.json`), so neither needs Claude to rerun. Keys go in `pipeline/.env`, each optional:

- `ANTHROPIC_API_KEY`: Claude extracts claims for a new call, otherwise the rule-based fallback runs.
- `POLYGON_API_KEY`: 1-minute bars from Massive (formerly Polygon), free plan, otherwise yfinance.
- `ALPHA_VANTAGE_KEY`: analyst consensus from Alpha Vantage (25 requests a day), otherwise yfinance.
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`: `ingest --upload` transcodes the audio to mono 48 kbps MP3 and uploads it.

Precision at 3 and 5 against hand-labeled move times: `.venv/bin/python -m callmoves.eval <call folder or JSON> --labels <labels.json>`.

## How a call is processed

Price first, transcript second. Summarizing the call would surface what sounds important, not what changed expectations.

1. Load the transcript (segments with ms offsets and speakers) and map the audio position `audio_offset_ms` to the call start, so every sentence has a wall-clock time.
2. Join the stock's 1-minute bars with the sector ETF's. Adjusted return = stock return minus ETF return. Volume multiple = volume over the trailing 20-minute median. Flag minutes where the adjusted return is far outside the trailing baseline or volume spikes with a real move.
3. Keep only the turns spoken in the 5 minutes before a flagged minute.
4. Extract claims from those turns (metric, value, period, kind, verbatim quote). A quote that is not in the transcript is dropped.
5. Judge novelty against the release and the prior quarter's materials. A claim already in the release cannot explain a move during the call, unless it is an EPS or revenue figure more than 1% off analyst consensus. Only a consensus frozen at the report (`at_report`) or dated before the call (`before_call`) counts, one fetched after the call (`after_call`) is shown but never scores.
6. Score = novelty × kind × |adjusted return over the next 5 min| × volume multiple × closeness to the flagged minute. Top 5 become moments, each graded high, medium or low confidence. The wording is "associated with", never "caused".

The fixture plants a guidance raise at 10:12 (+1.8% on 4x volume), a sector-wide move at 10:20 that the adjustment cancels, and a gross margin warning at 10:31 (-1.2%). The ranker finds the guidance raise and ignores the sector move. The 10:31 drop stays under the z-score threshold since the price-first change, so it is not flagged.
