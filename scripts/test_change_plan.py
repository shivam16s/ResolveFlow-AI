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
from backend.tools import change_plan  # noqa: E402
from seed_customers import seed_customers  # noqa: E402


VALID_DOWNGRADE_CONTEXT = {
    "lookup_customer": {
        "account_active": True,
    },
    "has_overdue_invoice": False,
    "requested_plan_available": True,
    "price_speed_confirmed": True,
    "promo_lockin": False,
}


def build_seeded_customer_db() -> Path:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-change-plan-")) / "resolveflow.db"
    seed_customers(db_path)
    return db_path


def customer_plan_id(db_path: Path, customer_id: str) -> str:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT plan_id FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
    if row is None:
        raise AssertionError(f"customer missing: {customer_id}")
    return row[0]


def customer_plan_state(db_path: Path, customer_id: str) -> dict:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT plan_id, pending_plan_id, pending_plan_effective_date, pending_plan_requested_at
            FROM customers
            WHERE customer_id = ?
            """,
            (customer_id,),
        ).fetchone()
    if row is None:
        raise AssertionError(f"customer missing: {customer_id}")
    return dict(row)


def assert_changes_plan_after_policy_gate() -> None:
    db_path = build_seeded_customer_db()
    result = change_plan(
        "CUST-1001",
        "fiber_starter_100",
        policy_context=VALID_DOWNGRADE_CONTEXT,
        effective_date="2026-06-01",
        db_path=db_path,
    )

    expected = {
        "customer_id": "CUST-1001",
        "previous_plan_id": "fiber_plus_200",
        "previous_plan_name": "Fiber Plus 200",
        "new_plan_id": "fiber_starter_100",
        "new_plan_name": "Fiber Starter 100",
        "monthly_price_before": 1199.0,
        "monthly_price_after": 799.0,
        "speed_mbps_before": 200,
        "speed_mbps_after": 100,
        "change_type": "downgrade",
        "effective_date": "2026-06-01",
        "fee_disclosure_required": False,
        "cancellation_fee": 0.0,
        "policy_name": "plan_downgrade_dag",
        "policy_action": "change_plan",
        "policy_status": "compliant",
    }
    for key, value in expected.items():
        if result.get(key) != value:
            raise AssertionError(f"wrong plan-change field {key}: {result}")
    if result["policy_path"] != [
        "check_account_active",
        "check_overdue_invoice",
        "check_plan_available",
        "check_price_speed_confirmed",
        "check_promo_lockin",
        "schedule_plan_downgrade",
    ]:
        raise AssertionError(f"wrong downgrade path: {result}")
    if result["policy_action_args"].get("effective") != "next_billing_cycle":
        raise AssertionError(f"effective policy missing: {result}")
    if result["ujcs"] != round(6 / 8, 4):
        raise AssertionError(f"wrong UJCS: {result}")
    plan_state = customer_plan_state(db_path, "CUST-1001")
    if plan_state["plan_id"] != "fiber_plus_200":
        raise AssertionError(f"scheduled downgrade should not update active plan: {plan_state}")
    if plan_state["pending_plan_id"] != "fiber_starter_100":
        raise AssertionError(f"scheduled downgrade should persist pending plan: {plan_state}")
    if plan_state["pending_plan_effective_date"] != "2026-06-01":
        raise AssertionError(f"scheduled downgrade should persist effective date: {plan_state}")
    if not plan_state["pending_plan_requested_at"]:
        raise AssertionError(f"scheduled downgrade should persist request timestamp: {plan_state}")


def assert_changes_lockin_downgrade_with_fee_disclosure() -> None:
    db_path = build_seeded_customer_db()
    context = dict(VALID_DOWNGRADE_CONTEXT)
    context["promo_lockin"] = True
    result = change_plan(
        "CUST-1001",
        "fiber_starter_100",
        policy_context=context,
        effective_date="2026-06-01",
        db_path=db_path,
    )

    if result["policy_path"][-1] != "disclose_fee_and_schedule":
        raise AssertionError(f"lock-in downgrade should use disclosure path: {result}")
    if result["fee_disclosure_required"] is not True:
        raise AssertionError(f"fee disclosure should be required: {result}")
    if result["cancellation_fee"] != 499.0:
        raise AssertionError(f"current-plan cancellation fee should be disclosed: {result}")
    if result["policy_action_args"].get("fee_disclosure_required") is not True:
        raise AssertionError(f"policy args should include disclosure requirement: {result}")
    plan_state = customer_plan_state(db_path, "CUST-1001")
    if plan_state["plan_id"] != "fiber_plus_200" or plan_state["pending_plan_id"] != "fiber_starter_100":
        raise AssertionError(f"lock-in downgrade should be scheduled, not active immediately: {plan_state}")


def assert_blocks_when_policy_prerequisites_fail() -> None:
    db_path = build_seeded_customer_db()
    blocked_context = dict(VALID_DOWNGRADE_CONTEXT)
    blocked_context["has_overdue_invoice"] = True
    try:
        change_plan(
            "CUST-1001",
            "fiber_starter_100",
            policy_context=blocked_context,
            effective_date="2026-06-01",
            db_path=db_path,
        )
    except PolicyActionBlocked as exc:
        if "handoff_human" not in str(exc):
            raise AssertionError(f"blocked reason should include reached action: {exc}") from exc
    else:
        raise AssertionError("overdue invoice should block plan change")
    if customer_plan_id(db_path, "CUST-1001") != "fiber_plus_200":
        raise AssertionError("blocked plan change should not update customer")


def assert_blocks_upgrade_through_downgrade_dag() -> None:
    db_path = build_seeded_customer_db()
    try:
        change_plan(
            "CUST-1010",
            "fiber_plus_200",
            policy_context=VALID_DOWNGRADE_CONTEXT,
            effective_date="2026-06-01",
            db_path=db_path,
        )
    except PolicyActionBlocked as exc:
        if "DAG requires 'downgrade'" not in str(exc):
            raise AssertionError(f"wrong change-type block reason: {exc}") from exc
    else:
        raise AssertionError("upgrade should not pass through plan_downgrade_dag")
    if customer_plan_id(db_path, "CUST-1010") != "fiber_starter_100":
        raise AssertionError("blocked upgrade should not update customer")


def assert_validates_inputs() -> None:
    db_path = build_seeded_customer_db()
    bad_calls = (
        {"customer_id": "   ", "new_plan_id": "fiber_starter_100", "policy_context": VALID_DOWNGRADE_CONTEXT},
        {"customer_id": "CUST-1001", "new_plan_id": "   ", "policy_context": VALID_DOWNGRADE_CONTEXT},
        {"customer_id": "CUST-1001", "new_plan_id": "fiber_starter_100", "policy_context": []},
        {
            "customer_id": "CUST-1001",
            "new_plan_id": "fiber_starter_100",
            "policy_context": VALID_DOWNGRADE_CONTEXT,
            "policy_name": "   ",
        },
        {
            "customer_id": "CUST-9999",
            "new_plan_id": "fiber_starter_100",
            "policy_context": VALID_DOWNGRADE_CONTEXT,
        },
        {
            "customer_id": "CUST-1001",
            "new_plan_id": "missing_plan",
            "policy_context": VALID_DOWNGRADE_CONTEXT,
        },
        {
            "customer_id": "CUST-1001",
            "new_plan_id": "fiber_plus_200",
            "policy_context": VALID_DOWNGRADE_CONTEXT,
        },
        {
            "customer_id": "CUST-1001",
            "new_plan_id": "fiber_starter_100",
            "policy_context": VALID_DOWNGRADE_CONTEXT,
            "effective_date": "   ",
        },
    )
    for kwargs in bad_calls:
        try:
            change_plan(**kwargs, db_path=db_path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad change_plan inputs were accepted: {kwargs}")


def assert_change_plan_api_endpoint() -> None:
    db_path = build_seeded_customer_db()
    with TestClient(create_app(db_path=db_path)) as client:
        response = client.post(
            "/api/tools/change_plan",
            json={
                "customer_id": "CUST-1001",
                "new_plan_id": "fiber_starter_100",
                "policy_context": VALID_DOWNGRADE_CONTEXT,
                "effective_date": "2026-06-01",
            },
        )
        if response.status_code != 200:
            raise AssertionError(f"change plan endpoint failed: {response.status_code} {response.text}")
        payload = response.json()
        if payload["tool_name"] != "change_plan" or payload["ok"] is not True:
            raise AssertionError(f"wrong tool envelope: {payload}")
        if payload["result"]["new_plan_id"] != "fiber_starter_100":
            raise AssertionError(f"endpoint plan result wrong: {payload}")
        plan_state = customer_plan_state(db_path, "CUST-1001")
        if plan_state["plan_id"] != "fiber_plus_200":
            raise AssertionError(f"endpoint should not update active plan immediately: {plan_state}")
        if plan_state["pending_plan_id"] != "fiber_starter_100":
            raise AssertionError(f"endpoint should persist pending plan: {plan_state}")
        if plan_state["pending_plan_effective_date"] != "2026-06-01":
            raise AssertionError(f"endpoint should persist effective date: {plan_state}")

        blocked = client.post(
            "/api/tools/change_plan",
            json={
                "customer_id": "CUST-1010",
                "new_plan_id": "fiber_plus_200",
                "policy_context": VALID_DOWNGRADE_CONTEXT,
                "effective_date": "2026-06-01",
            },
        )
        if blocked.status_code != 409:
            raise AssertionError(f"blocked change should return 409: {blocked.status_code} {blocked.text}")

        invalid = client.post(
            "/api/tools/change_plan",
            json={
                "customer_id": "CUST-1001",
                "new_plan_id": "",
                "policy_context": VALID_DOWNGRADE_CONTEXT,
            },
        )
        if invalid.status_code != 422:
            raise AssertionError(f"empty target plan should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_changes_plan_after_policy_gate()
    assert_changes_lockin_downgrade_with_fee_disclosure()
    assert_blocks_when_policy_prerequisites_fail()
    assert_blocks_upgrade_through_downgrade_dag()
    assert_validates_inputs()
    assert_change_plan_api_endpoint()
    print("change plan tests passed")


if __name__ == "__main__":
    main()
