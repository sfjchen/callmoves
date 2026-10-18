# CallMoves

Earnings call market-reaction dashboard. Spec: `../spec/`. Owner: Jonathan Chen (sfjchen).

## Layout
- `pipeline/` Python 3.12. `callmoves/` has 1 module per step, each a pure function: `transcript.py` (load, align), `market.py` (bars, adjusted returns, abnormal minutes), `claims.py`, `novelty.py`, `score.py`, `run.py` (CLI), `ingest.py` (audio + PDFs + bars -> call folder), `docs.py`. `fixtures/acme_q3_2026/` is a fictional call with planted moves, `fixtures/make_bars.py` regenerates its bars. Tests in `tests/`.
- `web/` Next.js 16, TypeScript, Tailwind, Recharts. Reads the pipeline's JSON from `web/data/`. `src/lib/data.ts` is the only data access, swap it for Supabase later. Pages: `/` library, `/calls/[id]` replay view. API: `/api/calls`, `/api/calls/[id]`, `/api/calls/[id]/moments`.

## Commands
```bash
cd pipeline && .venv/bin/python -m pytest -q                                   # 13 tests, offline, under 1 s
cd pipeline && .venv/bin/python -m callmoves.run fixtures/acme_q3_2026 --out ../web/data
cd web && npm run dev                                                           # localhost:3000
cd web && npm run type-check && npm run lint
```

## Hard rules
- Raw audio and PDFs go in `pipeline/raw/` and local audio for the player in `web/public/audio/`, both gitignored.
- Never commit `.env`, audio files or `pipeline/out/`. Do not weaken `.gitignore`.
- No paid services. Claude and Polygon are optional and only used when their key is set.
- A claim must quote the transcript verbatim or it is dropped. Keep that check in both extractors.
- Steps stay pure functions with the Pydantic models in `models.py` as the contract. Web types in `src/lib/types.ts` mirror them, change both.

## Conventions
- Python: type hints, Pydantic v2 models, pandas for the math, no notebooks. Tests on synthetic series, never on API calls.
- Web: strict TypeScript, `@/` imports, server components read data, `replay.tsx` is the only client component.
- Numbers in docs and UI as numerals.
