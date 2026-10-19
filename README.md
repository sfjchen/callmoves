# CallMoves

Which statements in an earnings call the market reacted to. Give it a timestamped transcript, the earnings release, last quarter's materials and 1-minute prices for the stock and its sector ETF. It finds the abnormal minutes, reads what was said just before each one, and ranks the likely drivers with their evidence and a confidence grade. Transcript, audio and chart replay on 1 cursor.

Prototype status: the pipeline runs end to end on a fictional fixture, the web app shows it. Transcription (WhisperX on Modal), Supabase and real audio come next. See `../spec/` for the plan.

## Run it

```bash
cd pipeline
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/python -m callmoves.run fixtures/acme_q3_2026 --out ../web/data

cd ../web
npm install
npm run dev        # http://localhost:3000
```

Set `ANTHROPIC_API_KEY` to use Claude for claim extraction and novelty instead of the rule-based fallback. Set `POLYGON_API_KEY` to pull real 1-minute bars with `market.fetch_polygon_bars`.

## How a call is processed

Price first, transcript second. Summarizing the call would surface what sounds important, not what changed expectations.

1. Load the transcript (segments with ms offsets and speakers) and map audio t=0 to the call start, so every sentence has a wall-clock time.
2. Join the stock's 1-minute bars with the sector ETF's. Adjusted return = stock return minus ETF return. Volume multiple = volume over the trailing 20-minute median. Flag minutes where the adjusted return is far outside the trailing baseline or volume spikes with a real move.
3. Keep only the turns spoken in the 5 minutes before a flagged minute.
4. Extract claims from those turns (metric, value, period, kind, verbatim quote). A quote that is not in the transcript is dropped.
5. Judge novelty against the release and the prior quarter's materials. A claim already in the release cannot explain a move during the call.
6. Score = novelty × kind × |adjusted return over the next 5 min| × volume multiple × closeness to the flagged minute. Top 5 become moments, each graded high, medium or low confidence. The wording is "associated with", never "caused".

The fixture plants a guidance raise at 10:12 (+1.8% on 4x volume), a sector-wide move at 10:20 that the adjustment cancels, and a gross margin warning at 10:31 (-1.2%). The ranker finds the first and third and ignores the second.
