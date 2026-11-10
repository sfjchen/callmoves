# CallMoves

Earnings call market-reaction dashboard. Spec: `../spec/`. Owner: Jonathan Chen (sfjchen). Current state, keys, data provenance and backlog: `CLAUDE.local.md` (gitignored, read it first).

## Layout
- `pipeline/` Python 3.12. `callmoves/` has 1 module per step, each a pure function: `transcript.py` (load, align), `market.py` (bars, adjusted returns, abnormal minutes), `claims.py`, `novelty.py`, `score.py`, `estimates.py` (analyst consensus, claim vs consensus), `run.py` (CLI), `ingest.py` (audio + release + bars -> call folder), `audio.py` (MP3 transcode, Supabase upload), `docs.py` (PDF/HTML to text), `eval.py` (precision@k vs hand labels). Real calls live in `data/<ticker>_<quarter>/`. `fixtures/acme_q3_2026/` is a fictional call with planted moves, `fixtures/make_bars.py` regenerates its bars. Tests in `tests/`.
- `web/` Next.js 16, TypeScript, Tailwind, Recharts. `src/lib/data.ts` is the only call data access: Supabase `callmoves_calls` when `SUPABASE_URL` and `SUPABASE_ANON_KEY` are set (Vercel), else `web/data/*.json`. `src/lib/submit.ts` queues submissions (service key, IP rate limit). Pages: `/` library, `/calls/[id]` replay view, `/submit`. API: `/api/calls` (GET list, POST submit), `/api/calls/[id]`, `/api/calls/[id]/moments`.
- `db/schema.sql` Supabase tables (prefix `callmoves_`, the project holds unrelated tables). `pipeline/callmoves/worker.py` + `.github/workflows/worker.yml` process the submission queue on GitHub Actions, keys are repo secrets. `publish.py` upserts a call.

## Commands
```bash
cd pipeline && .venv/bin/python -m pytest -q                                   # offline, under 1 s
cd pipeline && .venv/bin/python -m callmoves.run fixtures/acme_q3_2026 --out ../web/data
cd web && npm run dev                                                           # localhost:3000
cd web && npm run type-check && npm run lint
```

## Hard rules
- Raw audio and PDFs go in `pipeline/raw/` (gitignored). Player audio is mono 48 kbps MP3 in the public Supabase bucket `callmoves-audio`, uploaded by `ingest --upload`, never committed or deployed.
- Never commit `.env`, audio files or `pipeline/out/`. Do not weaken `.gitignore`.
- No paid services. Keys in `pipeline/.env`, each optional: `ANTHROPIC_API_KEY`, `POLYGON_API_KEY` (Massive bars, else yfinance), `ALPHA_VANTAGE_KEY` (consensus, else yfinance, 25 requests a day), `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` (audio upload). Never print their values.
- No look-ahead: an `after_call` consensus is display only, never scores a claim.
- A claim must quote the transcript verbatim or it is dropped. Keep that check in both extractors.
- Steps stay pure functions with the Pydantic models in `models.py` as the contract. Web types in `src/lib/types.ts` mirror them, change both.

## Conventions
- Python: type hints, Pydantic v2 models, pandas for the math, no notebooks. Tests on synthetic series, never on API calls.
- Web: strict TypeScript, `@/` imports, server components read data, `replay.tsx` is the only client component.
- Numbers in docs and UI as numerals.
