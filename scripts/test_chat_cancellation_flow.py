from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api.chat_routes import (  # noqa: E402
    _cancellation_confirmation_response,
    _cancellation_options_response,
    _check_pending_credits,
    _create_cancellation_request,
    _get_cancellation_policy,
    _get_subscription_status,
    _mark_cancellation_pending,
    _normalize_chat_intent,
    _state_for,
)


def _make_db() -> Path:
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    db_path = Path(temp.name)
    temp.close()
    schema = (ROOT / "backend" / "db" / "schema.sql").read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as connection:
        connection.executescript(schema)
        connection.execute(
            """
            INSERT INTO plans (plan_id, plan_name, monthly_price, speed_mbps, benefits, cancellation_fee)
            VALUES ('fiber_pro_200', 'Fiber Pro 200Mbps', 1199, 200, '[]', 0)
            """
        )
        connection.execute(
            """
            INSERT INTO customers (
                customer_id, name, email, location, plan_id, risk_level,
                preferred_language, account_status, churn_score
            )
            VALUES (
                'CUST-TEST', 'Rahul Sharma', 'rahul.test@example.com',
                'Chennai Zone-04', 'fiber_pro_200', 'high', 'en', 'active', 0.76
            )
            """
        )
        connection.execute(
            """
            INSERT INTO payments (payment_id, customer_id, amount, date, method, duplicate_flag)
            VALUES
              ('PAY-1', 'CUST-TEST', 1199, '2026-05-25T09:00:00', 'upi', 1),
              ('PAY-2', 'CUST-TEST', 1199, '2026-05-25T09:02:00', 'upi', 1)
            """
        )
        connection.execute(
            """
            INSERT INTO invoices (invoice_id, customer_id, amount, date, status, payment_id)
            VALUES ('INV-TEST', 'CUST-TEST', 1199, '2026-05-25', 'paid', 'PAY-1')
            """
        )
        connection.execute(
            """
            INSERT INTO outages (
                outage_id, location, start_time, end_time, duration_hours, verified, affected_customers
            )
            VALUES (
                'OUT-TEST', 'Chennai Zone-04', '2026-05-24T01:00:00',
                '2026-05-24T08:00:00', 7, 1, '["CUST-TEST"]'
            )
            """
        )
    return db_path


def test_cancellation_request_checks_tools_and_offers_choice() -> None:
    db_path = _make_db()
    customer = {
        "customer_id": "CUST-TEST",
        "name": "Rahul Sharma",
        "location": "Chennai Zone-04",
        "risk_level": "high",
    }
    subscription = _get_subscription_status("CUST-TEST", db_path)
    policy = _get_cancellation_policy(customer, subscription)
    pending = _check_pending_credits("CUST-TEST", customer, db_path)
    tools = [
        {"tool_name": "get_subscription_status", "result": subscription},
        {"tool_name": "get_cancellation_policy", "result": policy},
        {"tool_name": "check_pending_credits", "result": pending},
    ]
    reply = _cancellation_options_response(customer, tools)

    assert subscription["cancellation_allowed"] is True
    assert policy["pending_credit_notice_required"] is True
    assert pending["pending_credit_amount"] == 500
    assert pending["duplicate_charge_refund_pending"] is True
    assert "cancellation is allowed" in reply
    assert "cancel now" in reply
    assert "I will check" not in reply


def test_second_cancel_becomes_confirmation_and_creates_request_once() -> None:
    db_path = _make_db()
    state = _state_for("CUST-TEST-LOCAL")
    state.clear()
    state.update(
        {
            "customer_id": "CUST-TEST",
            "active_flow": "none",
            "last_confirmed_intent": None,
            "pending_customer_choice": None,
            "open_issues": [],
            "completed_tools": [],
            "last_bot_offer": [],
            "cancellation_notice_shown": False,
            "cancellation_request": None,
        }
    )
    _mark_cancellation_pending(state, ["cancellation_intent"], {"pending_credit_amount": 500})

    assert _normalize_chat_intent("cancel", state) == "cancellation_confirmation"
    first = _create_cancellation_request("CUST-TEST", "Customer confirmed cancellation", db_path)
    second = _create_cancellation_request("CUST-TEST", "Customer repeated cancellation", db_path)
    reply = _cancellation_confirmation_response({"name": "Rahul Sharma"}, [{"tool_name": "create_cancellation_request", "result": first}])

    assert first["mode"] == "created"
    assert first["cancellation_request_id"].startswith("CAN-")
    assert second["mode"] == "already_taken"
    assert second["cancellation_request_id"] == first["cancellation_request_id"]
    assert "created successfully" in reply
    assert "I will check" not in reply


if __name__ == "__main__":
    test_cancellation_request_checks_tools_and_offers_choice()
    test_second_cancel_becomes_confirmation_and_creates_request_once()
    print("chat cancellation flow tests passed")
