from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PACKAGE = ROOT / "backend" / "db"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DB_PACKAGE))

from backend.agent import HandoffQueueEntry, insert_human_handoff_queue  # noqa: E402
from seed_customers import seed_customers  # noqa: E402


def build_queue_db() -> Path:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-handoff-queue-")) / "resolveflow.db"
    seed_customers(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO conversations(session_id, customer_id, messages, final_status)
            VALUES (?, ?, ?, ?)
            """,
            ("sess-handoff-queue-001", "CUST-1001", "[]", "escalated"),
        )
        connection.execute(
            """
            INSERT INTO audit_logs(
                case_id,
                customer_id,
                session_id,
                tools_called,
                evidence_used,
                action_taken,
                policy_dag_path,
                policy_status,
                health_score,
                handoff_required
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "case-handoff-queue-001",
                "CUST-1001",
                "sess-handoff-queue-001",
                json.dumps(["retrieve_policy"]),
                json.dumps(["refund policy amount cap"]),
                json.dumps([{"action": "handoff_human"}]),
                json.dumps(["check_refund_amount", "manual_refund_exception_review"]),
                "needs_review",
                24,
                1,
            ),
        )
    return db_path


def assert_inserts_handoff_queue_row() -> None:
    db_path = build_queue_db()
    context_card = {
        "customer": {"customer_id": "CUST-1001", "name": "Rahul Sharma"},
        "trigger_codes": ["refund_over_500", "explicit_request"],
        "last_customer_message": "Please get me a human.",
    }
    result = insert_human_handoff_queue(
        case_id="case-handoff-queue-001",
        customer_id="CUST-1001",
        context_card=context_card,
        handoff_reason="Customer requested a human for an over-cap refund.",
        db_path=db_path,
    )

    if not isinstance(result, HandoffQueueEntry):
        raise AssertionError(f"wrong queue entry type: {result}")
    if not result.inserted:
        raise AssertionError(f"first insert should be marked inserted: {result.to_dict()}")
    if not result.handoff_id.startswith("HND-"):
        raise AssertionError(f"handoff id should be generated: {result.to_dict()}")
    if result.status != "waiting" or result.assigned_to is not None:
        raise AssertionError(f"new handoff should be waiting/unassigned: {result.to_dict()}")
    if result.context_card != context_card:
        raise AssertionError(f"context card round trip failed: {result.to_dict()}")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT handoff_id, case_id, customer_id, context_card, handoff_reason, status, assigned_to
            FROM human_handoff_queue
            WHERE case_id = ?
            """,
            ("case-handoff-queue-001",),
        ).fetchone()
    if row is None:
        raise AssertionError("handoff queue row was not inserted")
    if json.loads(row["context_card"]) != context_card:
        raise AssertionError(f"stored context card wrong: {dict(row)}")


def assert_insert_is_idempotent_per_case() -> None:
    db_path = build_queue_db()
    first = insert_human_handoff_queue(
        case_id="case-handoff-queue-001",
        customer_id="CUST-1001",
        context_card={"first": True},
        handoff_reason="First reason.",
        db_path=db_path,
    )
    second = insert_human_handoff_queue(
        case_id="case-handoff-queue-001",
        customer_id="CUST-1001",
        context_card={"second": True},
        handoff_reason="Second reason should not duplicate.",
        db_path=db_path,
    )

    if not first.inserted or second.inserted:
        raise AssertionError(f"idempotency flags wrong: {first.to_dict()} {second.to_dict()}")
    if first.handoff_id != second.handoff_id:
        raise AssertionError(f"idempotent insert should return existing row: {first.to_dict()} {second.to_dict()}")
    if second.context_card != {"first": True}:
        raise AssertionError(f"existing context should be preserved: {second.to_dict()}")
    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM human_handoff_queue").fetchone()[0]
    if count != 1:
        raise AssertionError(f"handoff queue should contain one row, got {count}")


def assert_rejects_invalid_queue_inputs() -> None:
    db_path = build_queue_db()
    bad_calls = (
        {"case_id": "", "customer_id": "CUST-1001", "context_card": {}, "handoff_reason": "reason"},
        {"case_id": "case-handoff-queue-001", "customer_id": "", "context_card": {}, "handoff_reason": "reason"},
        {"case_id": "case-handoff-queue-001", "customer_id": "CUST-1001", "context_card": [], "handoff_reason": "reason"},
        {"case_id": "case-handoff-queue-001", "customer_id": "CUST-1001", "context_card": {}, "handoff_reason": ""},
        {"case_id": "missing-case", "customer_id": "CUST-1001", "context_card": {}, "handoff_reason": "reason"},
        {"case_id": "case-handoff-queue-001", "customer_id": "CUST-9999", "context_card": {}, "handoff_reason": "reason"},
    )
    for kwargs in bad_calls:
        try:
            insert_human_handoff_queue(**kwargs, db_path=db_path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad handoff queue input was accepted: {kwargs}")


def main() -> None:
    assert_inserts_handoff_queue_row()
    assert_insert_is_idempotent_per_case()
    assert_rejects_invalid_queue_inputs()
    print("handoff queue insertion tests passed")


if __name__ == "__main__":
    main()
