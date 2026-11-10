"""Publishes processed calls to the Supabase table callmoves_calls (schema in db/schema.sql): the full CallOutput
the replay page reads, plus the summary the library lists, which run() computes once.
Needs SUPABASE_URL and SUPABASE_SERVICE_KEY in pipeline/.env.

    python -m callmoves.publish ../web/data/*.json
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from .models import CallOutput

TABLE = "callmoves_calls"


def publish(out: CallOutput) -> str:
    """Upserts 1 call by its id and returns the id."""
    from supabase import create_client

    db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    data = json.loads(out.model_dump_json())
    db.table(TABLE).upsert({
        "id": out.call.call_id, "ticker": out.call.ticker, "quarter": out.call.quarter, "company": out.call.company,
        "call_start": out.call.call_start.isoformat(), "summary": data["summary"], "data": data,
        "updated_at": datetime.now(UTC).isoformat(),
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
