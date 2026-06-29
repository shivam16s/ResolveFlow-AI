from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from .init_db import DEFAULT_DB_PATH


EXPECTED_TABLES = {
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
}


@dataclass(frozen=True)
class FoundationValidationReport:
    ok: bool
    table_count: int
    row_counts: dict[str, int]
    policy_doc_count: int
    scenario_count: int
    verified_outage_count: int
    unverified_outage_count: int
    duplicate_charge_customer_ids: list[str]
    problems: list[str]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def validate_foundation_assets(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    policies_dir: Path | None = None,
    scenarios_dir: Path | None = None,
) -> FoundationValidationReport:
    repo_root = Path(__file__).resolve().parents[2]
    policies_dir = policies_dir or repo_root / "docs" / "policies"
    scenarios_dir = scenarios_dir or repo_root / "docs" / "scenarios"
    problems: list[str] = []

    if not db_path.exists():
        problems.append(f"database does not exist: {db_path}")
        table_names: set[str] = set()
        row_counts = {}
        verified_count = 0
        unverified_count = 0
        duplicate_customers = []
    else:
        with sqlite3.connect(db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            missing_tables = sorted(EXPECTED_TABLES - table_names)
            if missing_tables:
                problems.append(f"missing tables: {missing_tables}")

            row_counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in sorted(EXPECTED_TABLES & table_names)
            }
            verified_count = _count_where(
                connection, "outages", "verified = 1", table_names)
            unverified_count = _count_where(
                connection, "outages", "verified = 0", table_names)
            duplicate_customers = _duplicate_charge_customers(
                connection, table_names)

    policy_doc_count = len(list(policies_dir.glob("*.md"))
                           ) if policies_dir.exists() else 0
    scenario_count = len(list(scenarios_dir.glob(
        "case_*.json"))) if scenarios_dir.exists() else 0
    _expect_count(row_counts, "customers", 20, problems)
    _expect_count(row_counts, "invoices", 20, problems)
    if row_counts.get("payments", 0) < 20:
        problems.append("expected at least 20 payments")
    _expect_count(row_counts, "outages", 10, problems)
    if verified_count < 1 or unverified_count < 1:
        problems.append("expected both verified and unverified outage records")
    if not duplicate_customers:
        problems.append("expected at least one duplicate charge customer")
    if policy_doc_count != 8:
        problems.append(f"expected 8 policy docs, found {policy_doc_count}")
    if scenario_count != 20:
        problems.append(
            f"expected 20 scenario scripts, found {scenario_count}")

    return FoundationValidationReport(
        ok=not problems,
        table_count=len(table_names),
        row_counts=row_counts,
        policy_doc_count=policy_doc_count,
        scenario_count=scenario_count,
        verified_outage_count=verified_count,
        unverified_outage_count=unverified_count,
        duplicate_charge_customer_ids=duplicate_customers,
        problems=problems,
    )


def assert_foundation_ready(**kwargs) -> FoundationValidationReport:
    report = validate_foundation_assets(**kwargs)
    if not report.ok:
        raise AssertionError("; ".join(report.problems))
    return report


def _count_where(connection: sqlite3.Connection, table: str, where: str, table_names: set[str]) -> int:
    if table not in table_names:
        return 0
    return connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


def _duplicate_charge_customers(connection: sqlite3.Connection, table_names: set[str]) -> list[str]:
    if "payments" not in table_names:
        return []
    rows = connection.execute(
        """
        SELECT customer_id
        FROM payments
        WHERE duplicate_flag = 1
        GROUP BY customer_id
        HAVING COUNT(*) >= 2
        ORDER BY customer_id
        """
    ).fetchall()
    return [row[0] for row in rows]


def _expect_count(row_counts: dict[str, int], table: str, expected: int, problems: list[str]) -> None:
    actual = row_counts.get(table, 0)
    if actual != expected:
        problems.append(f"expected {expected} {table}, found {actual}")
