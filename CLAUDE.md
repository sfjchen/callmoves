# CallMoves

Earnings call market-reaction dashboard. Spec: `~/Desktop/CS 146S/project/spec/`. Owner: Jonathan Chen (sfjchen). Current state, keys, data provenance and backlog: `CLAUDE.local.md` (gitignored, read it first).

## Layout
- `pipeline/` Python 3.12. `callmoves/` has 1 module per step: `transcript.py` (load, align, ASR), `market.py` (Massive bars, adjusted returns, abnormal minutes), `claims.py`, `novelty.py`, `llm.py` (shared Claude call), `score.py`, `estimates.py` (analyst consensus, claim vs consensus), `run.py` (CLI, fills `CallOutput.summary`), `ingest.py` (audio + release + bars -> call folder), `audio.py` (MP3 transcode, Supabase upload), `gpu.py` (Modal GPU transcription and diarization), `docs.py` (PDF/HTML to text), `eval.py` (precision@k vs hand labels). Real calls live in `data/<ticker>_<quarter>/`. `fixtures/acme_q3_2026/` is a fictional call with planted moves, `fixtures/make_bars.py` regenerates its bars. Tests in `tests/`.
- `web/` Next.js 16, TypeScript, Tailwind, Recharts. `src/lib/data.ts` is the only call data access: Supabase `callmoves_calls` when `SUPABASE_URL` and `SUPABASE_ANON_KEY` are set (Vercel), else `web/data/*.json`. `src/lib/submit.ts` queues submissions and `src/lib/study.ts` stores study trials (service key, IP rate limit, shared helpers in `request.ts`). `study-state.ts` is the browser-side study state, `gold.ts` the study's gold minute. Pages: `/` library, `/calls/[id]` replay view, `/submit`, `/study`, `/study/results`. API: `/api/calls` (GET list, POST submit), `/api/calls/[id]`, `/api/calls/[id]/moments`, `/api/study` (POST trial).
- `db/schema.sql` Supabase tables (prefix `callmoves_`, the project holds unrelated tables). `pipeline/callmoves/worker.py` + `.github/workflows/worker.yml` process the submission queue on GitHub Actions, keys are repo secrets. `publish.py` upserts a call.

## Commands
```bash
cd pipeline && .venv/bin/python -m pytest -q                                   # offline, under 1 s
cd pipeline && .venv/bin/python -m ruff check callmoves tests
cd pipeline && .venv/bin/python -m callmoves.run fixtures/acme_q3_2026 --out ../web/data
cd web && npm run dev                                                           # localhost:3000
cd web && npm run type-check && npm run lint
```

## Hard rules
- Raw audio and PDFs go in `pipeline/raw/` (gitignored). Player audio is mono 48 kbps MP3 in the public Supabase bucket `callmoves-audio`, uploaded by `ingest --upload`, never committed or deployed.
- Never commit `.env`, audio files or `pipeline/out/`. Do not weaken `.gitignore`.
- No paid services. Keys live in `pipeline/.env` (list in `README.md`). Never print their values.
- No look-ahead: an `after_call` consensus is display only, never scores a claim.
- A claim must quote the transcript verbatim or it is dropped. Keep that check in both extractors.
- Analysis steps (align, flags, claims, novelty, score) take and return the Pydantic models in `models.py`, with no I/O except the Claude call in `llm.py`. Fetching and file I/O stay in `market.py` fetchers, `estimates.py`, `ingest.py`, `run.py`. Web types in `src/lib/types.ts` are the subset of the models the web reads: rename or remove a model field there too.

## Conventions
- Python: type hints, Pydantic v2 models, pandas for the math, no notebooks, ruff clean. Tests on synthetic series, never on API calls.
- Web: strict TypeScript, `@/` imports, server components read data, client components are `replay.tsx`, `study-bar.tsx` and `study-home.tsx`.
- Numbers in docs and UI as numerals.
