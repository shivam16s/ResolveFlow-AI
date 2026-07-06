from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.db.init_db import initialize_database  # noqa: E402
from backend.db.seed_policies import DEFAULT_POLICY_DIR  # noqa: E402


def test_sql_policies_table_seeded_from_markdown_docs() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-policy-sql-")) / "resolveflow.db"
    initialize_database(db_path)
    expected = {path.stem for path in DEFAULT_POLICY_DIR.glob("*.md")}

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT policy_id, policy_name, policy_text, effective_date, version FROM policies"
        ).fetchall()

    assert len(rows) == len(expected)
    assert {row[0] for row in rows} == expected
    assert all(row[1] and row[2] and row[3] and int(row[4]) > 0 for row in rows)


if __name__ == "__main__":
    test_sql_policies_table_seeded_from_markdown_docs()
    print("policy SQL seed tests passed")
