from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from .init_db import DEFAULT_DB_PATH, initialize_database
from .seed_billing import seed_billing
from .seed_outages import seed_outages


RESET_TABLE_ORDER = [
    "telemetry",
    "human_handoff_queue",
    "audit_logs",
    "credits",
    "tickets",
    "diagnostics",
    "memory_store",
    "conversations",
    "invoices",
    "payments",
    "outages",
    "policies",
    "customers",
    "plans",
]

BASELINE_TABLES = [
    "plans",
    "customers",
    "payments",
    "invoices",
    "outages",
    "tickets",
    "policies",
    "diagnostics",
    "credits",
    "audit_logs",
    "human_handoff_queue",
    "memory_store",
    "conversations",
    "telemetry",
]


@dataclass(frozen=True)
class DatabaseResetResult:
    db_path: str
    table_counts: dict[str, int]
    seeded_customers: int
    seeded_invoices: int
    seeded_payments: int
    seeded_outages: int

    def to_dict(self) -> dict:
        return asdict(self)


def reset_to_initial_state(db_path: Path = DEFAULT_DB_PATH) -> dict:
    """Restore the SQLite database to the deterministic seed baseline."""
    normalized_path = Path(db_path)
    initialize_database(normalized_path)
    _clear_tables(normalized_path)
    seed_billing(normalized_path)
    seed_outages(normalized_path)
    counts = _table_counts(normalized_path)

    return DatabaseResetResult(
        db_path=str(normalized_path),
        table_counts=counts,
        seeded_customers=counts.get("customers", 0),
        seeded_invoices=counts.get("invoices", 0),
        seeded_payments=counts.get("payments", 0),
        seeded_outages=counts.get("outages", 0),
    ).to_dict()


def _clear_tables(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        for table_name in RESET_TABLE_ORDER:
            connection.execute(f"DELETE FROM {table_name}")


def _table_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as connection:
        return {
            table_name: connection.execute(
                f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            for table_name in BASELINE_TABLES
        }
