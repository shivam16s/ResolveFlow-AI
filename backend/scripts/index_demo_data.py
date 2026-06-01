from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from backend.db.init_db import DEFAULT_DB_PATH
from backend.agent.memory_manager import MemoryManager

def index_all(db_path: Path):
    manager = MemoryManager(db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT session_id, customer_id, messages, final_status FROM conversations")
        rows = cursor.fetchall()

    if not rows:
        print("No conversations found.")
        return

    print(f"Found {len(rows)} conversations. Indexing...")
    for idx, row in enumerate(rows, start=1):
        session_id = row["session_id"]
        customer_id = row["customer_id"]
        final_status = row["final_status"]
        messages_json = row["messages"]
        try:
            messages = json.loads(messages_json)
        except Exception:
            continue

        print(f"[{idx}/{len(rows)}] Indexing session {session_id} for {customer_id}...")
        try:
            summary = manager.index_session(
                session_transcript=messages,
                customer_id=customer_id,
                session_id=session_id,
                final_status=final_status,
                close_session=False,
            )
            print(f"  -> Indexed {summary.units_indexed} units, {summary.triples_indexed} triples.")
        except Exception as e:
            print(f"  -> Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    index_all(args.db_path)
