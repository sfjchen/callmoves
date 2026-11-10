"""Publishes processed calls to the Supabase table callmoves_calls (schema in db/schema.sql): the full CallOutput
the replay page reads, plus the summary the library lists, computed once here instead of on every page view.
Needs SUPABASE_URL and SUPABASE_SERVICE_KEY in pipeline/.env.

    python -m callmoves.publish ../web/data/*.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from .models import CallOutput

TABLE = "callmoves_calls"
SPARK_POINTS = 60   # library sparkline resolution


def summarize(out: CallOutput) -> dict:
    """Builds the library row, the shape of CallSummary in web/src/lib/types.ts: counts, the top moment and the
    cumulative adjusted return over the call window downsampled to about SPARK_POINTS points."""
    cum = [b.cum_adj_ret for b in out.bars if out.call.call_start <= b.ts <= out.call_end]
    step = max(1, math.ceil(len(cum) / SPARK_POINTS))
    top = out.moments[0] if out.moments else None
    return {
        "id": out.call.call_id, "ticker": out.call.ticker, "quarter": out.call.quarter, "company": out.call.company,
        "call_start": out.call.call_start.isoformat(), "n_moments": len(out.moments),
        "n_flagged": sum(b.flagged for b in out.bars),
        "spark": [c for i, c in enumerate(cum) if i % step == 0 or i == len(cum) - 1],
        "top_moment": {"ts": top.ts.isoformat(), "quote": top.quote, "adj_ret_5m": top.adj_ret_5m,
                       "speaker": top.speaker} if top else None,
    }


def publish(out: CallOutput) -> str:
    """Upserts 1 call by its id and returns the id."""
    from supabase import create_client

    db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    db.table(TABLE).upsert({
        "id": out.call.call_id, "ticker": out.call.ticker, "quarter": out.call.quarter, "company": out.call.company,
        "call_start": out.call.call_start.isoformat(), "summary": summarize(out),
        "data": json.loads(out.model_dump_json()), "updated_at": datetime.now(UTC).isoformat(),
    }).execute()
    return out.call.call_id


def main(argv=None):
    """Publishes each JSON written by callmoves.run."""
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("files", type=Path, nargs="+", help="JSON files written by callmoves.run")
    for f in ap.parse_args(argv).files:
        print(f"published {publish(CallOutput.model_validate_json(f.read_text()))}")


if __name__ == "__main__":
    main()
