from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path(__file__).resolve(
).parents[2] / "data" / "resolveflow.db"


def initialize_database(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = MEMORY")
        connection.executescript(schema_sql)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize the ResolveFlow SQLite database.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite database path. Defaults to {DEFAULT_DB_PATH}",
    )
    args = parser.parse_args()

    initialize_database(args.db_path)
    print(f"Initialized SQLite schema at {args.db_path}")


if __name__ == "__main__":
    main()
