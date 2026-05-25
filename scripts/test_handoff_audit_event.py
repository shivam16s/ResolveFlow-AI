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

from backend.agent import (  # noqa: E402
    HandoffAuditEvent,
    detect_handoff_triggers,
    generate_handoff_customer_message,
    insert_human_handoff_queue,
    log_handoff_event_to_audit,
)
from seed_customers import seed_customers  # noqa: E402


def build_handoff_audit_db() -> Path:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-handoff-audit-")) / "resolveflow.db"
    seed_customers(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO conversations(session_id, customer_id, messages, intents, slots, tools_called, final_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sess-handoff-audit-001",
                "CUST-1001",
                json.dumps([{"role": "user", "content": "I need a human agent for this refund."}]),
                json.dumps(["refund"]),
                json.dumps({"customer_id": "CUST-1001", "refund_amount": 750}),
                json.dumps(["lookup_customer", "retrieve_policy"]),
                "escalated",
            ),
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
                "case-handoff-audit-001",
                "CUST-1001",
                "sess-handoff-audit-001",
                json.dumps(["lookup_customer", "retrieve_policy"]),
                json.dumps(["refund policy threshold"]),
                json.dumps([{"action": "manual_refund_review"}]),
                json.dumps(["check_refund_amount"]),
                "needs_review",
                24,
                0,
            ),
        )
    return db_path


def build_queue_entry(db_path: Path):
    return insert_human_handoff_queue(
        case_id="case-handoff-audit-001",
        customer_id="CUST-1001",
        context_card={
            "customer": {"customer_id": "CUST-1001", "name": "Rahul Sharma"},
            "issues_remaining": [{"intent": "refund", "label": "Refund exception"}],
            "last_customer_message": "Please get me a human agent.",
        },
        handoff_reason="Refund amount exceeds the automatic approval limit and customer requested a human.",
        db_path=db_path,
    )


def stored_audit_row(db_path: Path) -> sqlite3.Row:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT tools_called, evidence_used, action_taken, policy_dag_path, policy_status, health_score, handoff_required
            FROM audit_logs
            WHERE case_id = ?
            """,
            ("case-handoff-audit-001",),
        ).fetchone()
    if row is None:
        raise AssertionError("audit row not found")
    return row


def assert_logs_handoff_event_to_audit_trail() -> None:
    db_path = build_handoff_audit_db()
    queue_entry = build_queue_entry(db_path)
    detection = detect_handoff_triggers(
        health_score=24,
        refund_amount=750,
        user_message="Please get me a human agent.",
    )
    customer_message = generate_handoff_customer_message(queue_entry=queue_entry, trigger_detection=detection)

    result = log_handoff_event_to_audit(
        case_id="case-handoff-audit-001",
        customer_id="CUST-1001",
        session_id="sess-handoff-audit-001",
        queue_entry=queue_entry,
        customer_message=customer_message,
        trigger_detection=detection,
        db_path=db_path,
    )

    if not isinstance(result, HandoffAuditEvent):
        raise AssertionError(f"wrong handoff audit event type: {result}")
    if result.handoff_id != queue_entry.handoff_id or result.queue_status != "waiting":
        raise AssertionError(f"queue metadata not preserved: {result.to_dict()}")
    if sorted(result.trigger_codes) != ["explicit_request", "refund_over_500", "score_below_30"]:
        raise AssertionError(f"trigger codes missing: {result.to_dict()}")
    if result.audit_log["handoff_required"] is not True:
        raise AssertionError(f"audit log should require handoff: {result.audit_log}")
    if result.audit_log["policy_status"] != "needs_review" or result.audit_log["health_score"] != 24.0:
        raise AssertionError(f"existing audit compliance fields should be preserved: {result.audit_log}")

    row = stored_audit_row(db_path)
    tools = json.loads(row["tools_called"])
    evidence = json.loads(row["evidence_used"])
    actions = json.loads(row["action_taken"])
    if "lookup_customer" not in tools:
        raise AssertionError(f"existing tools were not preserved: {tools}")
    if not any(isinstance(tool, dict) and tool.get("tool_name") == "human_handoff" for tool in tools):
        raise AssertionError(f"handoff tool event missing: {tools}")
    if not any(isinstance(item, dict) and item.get("type") == "handoff_event" for item in evidence):
        raise AssertionError(f"handoff evidence missing: {evidence}")
    handoff_actions = [item for item in actions if isinstance(item, dict) and item.get("action") == "human_handoff"]
    if not handoff_actions:
        raise AssertionError(f"handoff action missing: {actions}")
    if handoff_actions[0]["handoff_id"] != queue_entry.handoff_id:
        raise AssertionError(f"handoff action id mismatch: {handoff_actions[0]}")
    if row["handoff_required"] != 1:
        raise AssertionError(f"stored handoff flag should be true: {dict(row)}")


def assert_generates_message_when_not_provided() -> None:
    db_path = build_handoff_audit_db()
    queue_entry = build_queue_entry(db_path)

    result = log_handoff_event_to_audit(
        case_id="case-handoff-audit-001",
        customer_id="CUST-1001",
        session_id="sess-handoff-audit-001",
        queue_entry=queue_entry,
        db_path=db_path,
    )

    if "connecting you to a specialist" not in result.customer_message:
        raise AssertionError(f"default customer message missing: {result.to_dict()}")
    actions = result.audit_log["raw_json"]["action_taken"]
    handoff_action = next(item for item in actions if isinstance(item, dict) and item.get("action") == "human_handoff")
    if "connecting you to a specialist" not in handoff_action["customer_message"]:
        raise AssertionError(f"generated message not persisted: {handoff_action}")


def assert_rejects_invalid_handoff_audit_inputs() -> None:
    db_path = build_handoff_audit_db()
    queue_entry = build_queue_entry(db_path)
    bad_calls = (
        {"case_id": "", "customer_id": "CUST-1001", "session_id": "sess-handoff-audit-001", "queue_entry": queue_entry},
        {"case_id": "case-handoff-audit-001", "customer_id": "", "session_id": "sess-handoff-audit-001", "queue_entry": queue_entry},
        {"case_id": "case-handoff-audit-001", "customer_id": "CUST-1001", "session_id": "", "queue_entry": queue_entry},
        {"case_id": "case-handoff-audit-001", "customer_id": "CUST-1001", "session_id": "missing-session", "queue_entry": queue_entry},
        {
            "case_id": "case-handoff-audit-001",
            "customer_id": "CUST-1001",
            "session_id": "sess-handoff-audit-001",
            "queue_entry": {"status": "waiting", "handoff_reason": "reason"},
        },
        {
            "case_id": "case-handoff-audit-001",
            "customer_id": "CUST-1001",
            "session_id": "sess-handoff-audit-001",
            "queue_entry": queue_entry,
            "tools_called": "human_handoff",
        },
    )
    for kwargs in bad_calls:
        try:
            log_handoff_event_to_audit(**kwargs, db_path=db_path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad handoff audit inputs were accepted: {kwargs}")


def main() -> None:
    assert_logs_handoff_event_to_audit_trail()
    assert_generates_message_when_not_provided()
    assert_rejects_invalid_handoff_audit_inputs()
    print("handoff audit event tests passed")


if __name__ == "__main__":
    main()
