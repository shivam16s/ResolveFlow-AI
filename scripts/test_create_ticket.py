from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PACKAGE = ROOT / "backend" / "db"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DB_PACKAGE))

from fastapi.testclient import TestClient  # noqa: E402

from backend.agent.policy_graph import PolicyActionBlocked  # noqa: E402
from backend.api import create_app  # noqa: E402
from backend.tools import create_ticket  # noqa: E402
from seed_billing import seed_billing  # noqa: E402


TEMP_DIRS: list[tempfile.TemporaryDirectory] = []


DUPLICATE_REFUND_CONTEXT = {
    "check_duplicate_charge": {
        "duplicate_confirmed": True,
    },
    "get_invoice_history": {
        "single_matching_invoice": True,
    },
    "payment_age_days": 12,
    "duplicate_amount": 499,
}


def build_seeded_billing_db() -> Path:
    tmpdir = tempfile.TemporaryDirectory()
    TEMP_DIRS.append(tmpdir)
    db_path = Path(tmpdir.name) / "resolveflow.db"
    seed_billing(db_path)
    return db_path


def assert_creates_plain_ticket() -> None:
    db_path = build_seeded_billing_db()
    result = create_ticket(
        "CUST-1001",
        "billing_question",
        priority="medium",
        db_path=db_path,
    )

    if not result["ticket_id"].startswith("TKT-"):
        raise AssertionError(f"ticket id not generated: {result}")
    if result["customer_id"] != "CUST-1001" or result["issue_type"] != "billing_question":
        raise AssertionError(f"wrong ticket payload: {result}")
    if result["status"] != "open" or result["priority"] != "medium":
        raise AssertionError(f"wrong default status/priority: {result}")
    if result["policy_name"] is not None or result["policy_status"] != "pending":
        raise AssertionError(f"plain ticket should not claim policy validation: {result}")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT ticket_id, customer_id, issue_type, status, priority, created_at
            FROM tickets
            WHERE ticket_id = ?
            """,
            (result["ticket_id"],),
        ).fetchone()

    if row is None:
        raise AssertionError("ticket was not inserted")
    if row["customer_id"] != "CUST-1001" or row["issue_type"] != "billing_question":
        raise AssertionError(f"stored ticket wrong: {dict(row)}")


def assert_creates_policy_validated_duplicate_refund_ticket() -> None:
    db_path = build_seeded_billing_db()
    result = create_ticket(
        "CUST-1001",
        "duplicate_charge_refund_review",
        priority="high",
        policy_name="duplicate_charge_refund_dag",
        policy_context=DUPLICATE_REFUND_CONTEXT,
        db_path=db_path,
    )

    if result["policy_name"] != "duplicate_charge_refund_dag":
        raise AssertionError(f"policy name missing: {result}")
    if result["policy_action"] != "create_ticket":
        raise AssertionError(f"wrong policy action: {result}")
    if result["policy_action_args"].get("ticket_type") != "duplicate_charge_refund_review":
        raise AssertionError(f"ticket type gate missing: {result}")
    if result["policy_path"][-1] != "create_refund_review_ticket":
        raise AssertionError(f"wrong policy path: {result}")
    if result["ujcs"] != round(5 / 7, 4) or result["policy_status"] != "compliant":
        raise AssertionError(f"wrong policy status: {result}")


def assert_creates_retention_ticket_from_policy_path() -> None:
    db_path = build_seeded_billing_db()
    result = create_ticket(
        "CUST-1001",
        "retention_unresolved_issue",
        priority="critical",
        policy_name="cancellation_retention_dag",
        policy_context={
            "lookup_customer": {"identity_verified": True},
            "has_open_issue": True,
            "churn_score": 0.8,
        },
        db_path=db_path,
    )
    if result["policy_path"] != ["check_identity_status", "check_open_issues", "create_retention_ticket"]:
        raise AssertionError(f"wrong retention path: {result}")
    if result["priority"] != "critical":
        raise AssertionError(f"priority not preserved: {result}")


def assert_blocks_ticket_when_policy_prerequisites_fail() -> None:
    db_path = build_seeded_billing_db()
    blocked_context = dict(DUPLICATE_REFUND_CONTEXT)
    blocked_context["duplicate_amount"] = 799
    try:
        create_ticket(
            "CUST-1001",
            "duplicate_charge_refund_review",
            priority="high",
            policy_name="duplicate_charge_refund_dag",
            policy_context=blocked_context,
            db_path=db_path,
        )
    except PolicyActionBlocked as exc:
        if "handoff_human" not in str(exc):
            raise AssertionError(f"blocked reason should include reached action: {exc}") from exc
    else:
        raise AssertionError("over-cap duplicate refund ticket should be blocked")

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    if count != 0:
        raise AssertionError(f"blocked ticket should not write rows; got {count}")


def assert_blocks_policy_ticket_type_mismatch() -> None:
    db_path = build_seeded_billing_db()
    try:
        create_ticket(
            "CUST-1001",
            "generic_billing_ticket",
            policy_name="duplicate_charge_refund_dag",
            policy_context=DUPLICATE_REFUND_CONTEXT,
            db_path=db_path,
        )
    except PolicyActionBlocked as exc:
        if "DAG requires" not in str(exc):
            raise AssertionError(f"wrong ticket type block reason: {exc}") from exc
    else:
        raise AssertionError("ticket type mismatch should be blocked")


def assert_validates_inputs() -> None:
    db_path = build_seeded_billing_db()
    bad_calls = (
        {"customer_id": "   ", "issue_type": "billing_question", "db_path": db_path},
        {"customer_id": "CUST-1001", "issue_type": "   ", "db_path": db_path},
        {"customer_id": "CUST-1001", "issue_type": "billing_question", "priority": "urgent", "db_path": db_path},
        {"customer_id": "CUST-1001", "issue_type": "billing_question", "status": "done", "db_path": db_path},
        {"customer_id": "CUST-9999", "issue_type": "billing_question", "db_path": db_path},
        {
            "customer_id": "CUST-1001",
            "issue_type": "duplicate_charge_refund_review",
            "policy_name": "   ",
            "policy_context": DUPLICATE_REFUND_CONTEXT,
            "db_path": db_path,
        },
        {
            "customer_id": "CUST-1001",
            "issue_type": "duplicate_charge_refund_review",
            "policy_name": "duplicate_charge_refund_dag",
            "db_path": db_path,
        },
        {
            "customer_id": "CUST-1001",
            "issue_type": "duplicate_charge_refund_review",
            "policy_name": "duplicate_charge_refund_dag",
            "policy_context": [],
            "db_path": db_path,
        },
    )
    for kwargs in bad_calls:
        try:
            create_ticket(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad create_ticket inputs were accepted: {kwargs}")


def assert_create_ticket_api_endpoint() -> None:
    db_path = build_seeded_billing_db()
    client = TestClient(create_app(db_path=db_path))

    response = client.post(
        "/api/tools/create_ticket",
        json={
            "customer_id": "CUST-1001",
            "issue_type": "duplicate_charge_refund_review",
            "priority": "high",
            "policy_name": "duplicate_charge_refund_dag",
            "policy_context": DUPLICATE_REFUND_CONTEXT,
        },
    )
    if response.status_code != 200:
        raise AssertionError(f"create ticket endpoint failed: {response.status_code} {response.text}")
    payload = response.json()
    if payload["tool_name"] != "create_ticket" or payload["ok"] is not True:
        raise AssertionError(f"wrong tool envelope: {payload}")
    if payload["result"]["policy_path"][-1] != "create_refund_review_ticket":
        raise AssertionError(f"endpoint policy path wrong: {payload}")

    blocked = client.post(
        "/api/tools/create_ticket",
        json={
            "customer_id": "CUST-1001",
            "issue_type": "duplicate_charge_refund_review",
            "priority": "high",
            "policy_name": "duplicate_charge_refund_dag",
            "policy_context": {
                "check_duplicate_charge": {"duplicate_confirmed": False},
                "get_invoice_history": {"single_matching_invoice": True},
                "payment_age_days": 12,
                "duplicate_amount": 299,
            },
        },
    )
    if blocked.status_code != 409:
        raise AssertionError(f"blocked policy path should return 409: {blocked.status_code} {blocked.text}")

    invalid = client.post(
        "/api/tools/create_ticket",
        json={
            "customer_id": "CUST-1001",
            "issue_type": "billing_question",
            "priority": "urgent",
        },
    )
    if invalid.status_code != 422:
        raise AssertionError(f"bad priority should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_creates_plain_ticket()
    assert_creates_policy_validated_duplicate_refund_ticket()
    assert_creates_retention_ticket_from_policy_path()
    assert_blocks_ticket_when_policy_prerequisites_fail()
    assert_blocks_policy_ticket_type_mismatch()
    assert_validates_inputs()
    assert_create_ticket_api_endpoint()
    print("create ticket tests passed")


if __name__ == "__main__":
    main()
