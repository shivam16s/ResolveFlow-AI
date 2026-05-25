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
from backend.tools import apply_credit  # noqa: E402
from seed_billing import seed_billing  # noqa: E402


TEMP_DIRS: list[tempfile.TemporaryDirectory] = []


VALID_SERVICE_CREDIT_CONTEXT = {
    "check_outage_status": {
        "verified": True,
        "duration_hours": 7,
    },
    "get_invoice_history": {
        "credit_this_cycle": False,
    },
}


def build_seeded_billing_db() -> Path:
    tmpdir = tempfile.TemporaryDirectory()
    TEMP_DIRS.append(tmpdir)
    db_path = Path(tmpdir.name) / "resolveflow.db"
    seed_billing(db_path)
    return db_path


def assert_applies_credit_after_policy_gate() -> None:
    db_path = build_seeded_billing_db()
    result = apply_credit(
        "CUST-1001",
        300,
        " Verified outage OUT-CHN-04-20260520 lasted 7 hours. ",
        policy_context=VALID_SERVICE_CREDIT_CONTEXT,
        applied_to_invoice="INV-8821",
        db_path=db_path,
    )

    if not result["credit_id"].startswith("CR-"):
        raise AssertionError(f"credit id not generated: {result}")
    if result["customer_id"] != "CUST-1001" or result["amount"] != 300:
        raise AssertionError(f"wrong credit payload: {result}")
    if result["reason"] != "Verified outage OUT-CHN-04-20260520 lasted 7 hours.":
        raise AssertionError(f"reason should be normalized: {result}")
    if result["applied_to_invoice"] != "INV-8821":
        raise AssertionError(f"invoice link missing: {result}")
    if result["policy_name"] != "service_credit_dag" or result["policy_action"] != "apply_credit":
        raise AssertionError(f"policy validation metadata missing: {result}")
    if result["policy_action_args"].get("max_amount") != 500:
        raise AssertionError(f"policy cap missing: {result}")
    if result["policy_path"] != [
        "check_outage_verified",
        "check_outage_duration",
        "check_prior_credit",
        "auto_apply_credit",
    ]:
        raise AssertionError(f"wrong policy traversal path: {result}")
    if result["ujcs"] != 0.6667 or result["policy_status"] != "compliant":
        raise AssertionError(f"wrong policy status metadata: {result}")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT credit_id, customer_id, amount, reason, policy_id, applied_at, applied_to_invoice
            FROM credits
            WHERE credit_id = ?
            """,
            (result["credit_id"],),
        ).fetchone()

    if row is None:
        raise AssertionError("credit was not inserted")
    if row["customer_id"] != "CUST-1001" or float(row["amount"]) != 300:
        raise AssertionError(f"stored credit wrong: {dict(row)}")
    if row["policy_id"] is not None:
        raise AssertionError(f"policy_id should stay null until policy rows are seeded: {dict(row)}")
    if row["applied_to_invoice"] != "INV-8821":
        raise AssertionError(f"stored invoice link wrong: {dict(row)}")


def assert_blocks_credits_when_policy_prerequisites_fail() -> None:
    db_path = build_seeded_billing_db()
    blocked_context = {
        "check_outage_status": {
            "verified": False,
            "duration_hours": 7,
        },
        "get_invoice_history": {
            "credit_this_cycle": False,
        },
    }
    try:
        apply_credit(
            "CUST-1001",
            300,
            "Unverified outage request",
            policy_context=blocked_context,
            applied_to_invoice="INV-8821",
            db_path=db_path,
        )
    except PolicyActionBlocked as exc:
        if "handoff_human" not in str(exc):
            raise AssertionError(f"blocked reason should include reached action: {exc}") from exc
    else:
        raise AssertionError("unverified outage credit should be blocked")

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM credits").fetchone()[0]
    if count != 0:
        raise AssertionError(f"blocked credit should not write rows; got {count}")


def assert_enforces_policy_amount_cap() -> None:
    db_path = build_seeded_billing_db()
    try:
        apply_credit(
            "CUST-1001",
            600,
            "Credit exceeds automatic outage cap",
            policy_context=VALID_SERVICE_CREDIT_CONTEXT,
            applied_to_invoice="INV-8821",
            db_path=db_path,
        )
    except PolicyActionBlocked as exc:
        if "exceeds policy cap" not in str(exc):
            raise AssertionError(f"wrong cap block reason: {exc}") from exc
    else:
        raise AssertionError("amount above policy cap should be blocked")

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM credits").fetchone()[0]
    if count != 0:
        raise AssertionError(f"cap-blocked credit should not write rows; got {count}")


def assert_applies_partial_credit_path() -> None:
    db_path = build_seeded_billing_db()
    short_outage_context = {
        "check_outage_status": {
            "verified": True,
            "duration_hours": 2.5,
        },
        "get_invoice_history": {
            "credit_this_cycle": False,
        },
    }
    result = apply_credit(
        "CUST-1003",
        75,
        "Short verified outage goodwill credit",
        policy_context=short_outage_context,
        applied_to_invoice="INV-1003",
        db_path=db_path,
    )
    if result["policy_path"][-1] != "apply_partial_credit":
        raise AssertionError(f"short outage should use partial credit path: {result}")
    if result["policy_action_args"].get("max_amount") != 100:
        raise AssertionError(f"partial credit cap wrong: {result}")


def assert_validates_customer_invoice_and_inputs() -> None:
    db_path = build_seeded_billing_db()
    bad_calls = (
        {
            "customer_id": "   ",
            "amount": 50,
            "reason": "valid reason",
            "policy_context": VALID_SERVICE_CREDIT_CONTEXT,
            "db_path": db_path,
        },
        {
            "customer_id": "CUST-1001",
            "amount": 0,
            "reason": "valid reason",
            "policy_context": VALID_SERVICE_CREDIT_CONTEXT,
            "db_path": db_path,
        },
        {
            "customer_id": "CUST-1001",
            "amount": 50,
            "reason": "   ",
            "policy_context": VALID_SERVICE_CREDIT_CONTEXT,
            "db_path": db_path,
        },
        {
            "customer_id": "CUST-1001",
            "amount": 50,
            "reason": "valid reason",
            "policy_context": [],
            "db_path": db_path,
        },
        {
            "customer_id": "CUST-9999",
            "amount": 50,
            "reason": "valid reason",
            "policy_context": VALID_SERVICE_CREDIT_CONTEXT,
            "db_path": db_path,
        },
        {
            "customer_id": "CUST-1001",
            "amount": 50,
            "reason": "valid reason",
            "policy_context": VALID_SERVICE_CREDIT_CONTEXT,
            "applied_to_invoice": "INV-1002",
            "db_path": db_path,
        },
    )
    for kwargs in bad_calls:
        try:
            apply_credit(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad apply_credit inputs were accepted: {kwargs}")


def assert_apply_credit_api_endpoint() -> None:
    db_path = build_seeded_billing_db()
    client = TestClient(create_app(db_path=db_path))

    response = client.post(
        "/api/tools/apply_credit",
        json={
            "customer_id": "CUST-1001",
            "amount": 250,
            "reason": "Verified outage service credit",
            "policy_context": VALID_SERVICE_CREDIT_CONTEXT,
            "applied_to_invoice": "INV-8821",
        },
    )
    if response.status_code != 200:
        raise AssertionError(f"apply credit endpoint failed: {response.status_code} {response.text}")
    payload = response.json()
    if payload["tool_name"] != "apply_credit" or payload["ok"] is not True:
        raise AssertionError(f"wrong tool envelope: {payload}")
    if payload["result"]["policy_path"][-1] != "auto_apply_credit":
        raise AssertionError(f"endpoint policy path wrong: {payload}")

    blocked = client.post(
        "/api/tools/apply_credit",
        json={
            "customer_id": "CUST-1001",
            "amount": 250,
            "reason": "Unverified outage service credit",
            "policy_context": {
                "check_outage_status": {"verified": False, "duration_hours": 7},
                "get_invoice_history": {"credit_this_cycle": False},
            },
            "applied_to_invoice": "INV-8821",
        },
    )
    if blocked.status_code != 409:
        raise AssertionError(f"blocked policy path should return 409: {blocked.status_code} {blocked.text}")

    invalid = client.post(
        "/api/tools/apply_credit",
        json={
            "customer_id": "CUST-1001",
            "amount": 250,
            "reason": "Missing policy context",
            "policy_context": {},
        },
    )
    if invalid.status_code != 422:
        raise AssertionError(f"missing context should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_applies_credit_after_policy_gate()
    assert_blocks_credits_when_policy_prerequisites_fail()
    assert_enforces_policy_amount_cap()
    assert_applies_partial_credit_path()
    assert_validates_customer_invoice_and_inputs()
    assert_apply_credit_api_endpoint()
    print("apply credit tests passed")


if __name__ == "__main__":
    main()
