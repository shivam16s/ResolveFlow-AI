from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.db import reset_to_initial_state  # noqa: E402
from backend.db.init_db import initialize_database  # noqa: E402
from backend.db.validation import assert_foundation_ready  # noqa: E402


def assert_reset_restores_seed_baseline() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-reset-")) / "resolveflow.db"
    first = reset_to_initial_state(db_path)
    if first["seeded_customers"] != 20 or first["seeded_invoices"] != 20:
        raise AssertionError(f"baseline seed counts wrong: {first}")
    if first["seeded_payments"] < 20 or first["seeded_outages"] != 10:
        raise AssertionError(f"baseline seed counts wrong: {first}")

    _dirty_database(db_path)
    dirty_counts = _counts(db_path)
    if dirty_counts["tickets"] != 1 or dirty_counts["audit_logs"] != 1:
        raise AssertionError(f"test dirty setup failed: {dirty_counts}")
    if dirty_counts["customers"] != 21 or dirty_counts["payments"] != 22:
        raise AssertionError(f"test seed mutation failed: {dirty_counts}")

    second = reset_to_initial_state(db_path)
    expected_counts = {
        "plans": 5,
        "customers": 20,
        "payments": 21,
        "invoices": 20,
        "outages": 10,
        "tickets": 0,
        "policies": 8,
        "diagnostics": 0,
        "credits": 0,
        "audit_logs": 0,
        "human_handoff_queue": 0,
        "memory_store": 0,
        "conversations": 0,
        "telemetry": 0,
    }
    if second["table_counts"] != expected_counts:
        raise AssertionError(f"reset counts wrong: {second}")
    if _customer_plan(db_path, "CUST-1001") != "fiber_plus_200":
        raise AssertionError("reset did not restore mutated customer baseline")
    if _counts(db_path) != expected_counts:
        raise AssertionError(f"stored table counts wrong after reset: {_counts(db_path)}")

    report = assert_foundation_ready(db_path=db_path)
    if not report.ok:
        raise AssertionError(f"foundation should be ready after reset: {report}")


def assert_reset_is_idempotent() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-reset-idempotent-")) / "resolveflow.db"
    first = reset_to_initial_state(db_path)
    second = reset_to_initial_state(db_path)
    if first["table_counts"] != second["table_counts"]:
        raise AssertionError(f"reset should be idempotent: {first} {second}")


def assert_database_setup_uses_crash_safe_journal_mode() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-journal-")) / "resolveflow.db"
    initialize_database(db_path)
    if _journal_mode(db_path) != "wal":
        raise AssertionError(f"initialize_database should use WAL, got {_journal_mode(db_path)!r}")

    reset_to_initial_state(db_path)
    if _journal_mode(db_path) != "wal":
        raise AssertionError(f"reset/seed should preserve WAL, got {_journal_mode(db_path)!r}")


def _dirty_database(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO customers(customer_id, name, email, location, plan_id, risk_level, preferred_language, account_status, churn_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "CUST-9999",
                "Temporary Test Customer",
                "temp.customer@example.com",
                "Test Zone",
                "fiber_plus_200",
                "low",
                "en",
                "active",
                0.1,
            ),
        )
        connection.execute(
            "UPDATE customers SET plan_id = ?, account_status = ? WHERE customer_id = ?",
            ("fiber_work_500", "pending_cancellation", "CUST-1001"),
        )
        connection.execute(
            """
            INSERT INTO payments(payment_id, customer_id, amount, date, method, duplicate_flag)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("PAY-TEMP-9999", "CUST-9999", 499, "2026-05-24T10:00:00", "upi", 0),
        )
        connection.execute(
            """
            INSERT INTO conversations(session_id, customer_id, messages, intents, slots, tools_called)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("sess-reset-001", "CUST-1001", "[]", "[]", "{}", "[]"),
        )
        connection.execute(
            """
            INSERT INTO tickets(ticket_id, customer_id, issue_type, status, priority)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("TKT-RESET-001", "CUST-1001", "billing", "open", "high"),
        )
        connection.execute(
            """
            INSERT INTO diagnostics(customer_id, router_status, signal_strength, last_checked, recommendation)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("CUST-1001", "offline", 12, "2026-05-24T10:02:00", "reset router"),
        )
        connection.execute(
            """
            INSERT INTO memory_store(memory_id, customer_id, memory_type, content, entity_tags, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("mem-reset-001", "CUST-1001", "session", "Temporary memory", json.dumps(["billing"]), "sess-reset-001"),
        )
        connection.execute(
            """
            INSERT INTO credits(credit_id, customer_id, amount, reason, applied_to_invoice)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("CR-RESET-001", "CUST-1001", 100, "Temporary credit", "INV-8821"),
        )
        connection.execute(
            """
            INSERT INTO audit_logs(case_id, customer_id, session_id, tools_called, evidence_used, action_taken, policy_dag_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("case-reset-001", "CUST-1001", "sess-reset-001", "[]", "[]", "[]", "[]"),
        )
        connection.execute(
            """
            INSERT INTO human_handoff_queue(handoff_id, case_id, customer_id, context_card, handoff_reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("HND-RESET-001", "case-reset-001", "CUST-1001", "{}", "Temporary handoff"),
        )


def _counts(db_path: Path) -> dict[str, int]:
    tables = [
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
    with sqlite3.connect(db_path) as connection:
        return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}


def _customer_plan(db_path: Path, customer_id: str) -> str:
    with sqlite3.connect(db_path) as connection:
        return connection.execute(
            "SELECT plan_id FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()[0]


def _journal_mode(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()


def main() -> None:
    assert_reset_restores_seed_baseline()
    assert_reset_is_idempotent()
    assert_database_setup_uses_crash_safe_journal_mode()
    print("db reset tests passed")


if __name__ == "__main__":
    main()
