from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.api.chat_routes as chat_routes  # noqa: E402
from backend.api import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


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
                'CUST-RPT', 'Rahul Sharma', 'rahul.rpt@example.com',
                'Chennai Zone-04', 'fiber_pro_200', 'high', 'en', 'active', 0.5
            )
            """
        )
        connection.execute(
            """
            INSERT INTO payments (payment_id, customer_id, amount, date, method, duplicate_flag)
            VALUES
              ('PAY-R1', 'CUST-RPT', 1199, '2026-05-25T09:00:00', 'upi', 1),
              ('PAY-R2', 'CUST-RPT', 1199, '2026-05-25T09:02:00', 'upi', 1)
            """
        )
        connection.execute(
            """
            INSERT INTO invoices (invoice_id, customer_id, amount, date, status, payment_id)
            VALUES ('INV-RPT', 'CUST-RPT', 1199, '2026-05-25', 'disputed', 'PAY-R1')
            """
        )
    return db_path


def _final_response(client: TestClient, customer_id: str, message: str) -> dict:
    final: dict = {}
    with client.stream(
        "GET", "/api/chat/message/stream", params={"customer_id": customer_id, "message": message}
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line and line.startswith("data: "):
                event = json.loads(line[6:])
                if event["step"] == "response" and event["status"] == "done":
                    final = event["result"]
    return final


def test_finding_helpers_track_and_detect_repeats() -> None:
    tools = [
        {"tool_name": "check_duplicate_charge", "result": {"duplicate_confirmed": True, "invoice_id": "INV-RPT", "duplicate_amount": 1199}},
    ]
    keys = chat_routes._finding_keys(tools)
    assert keys == ["duplicate:INV-RPT"]

    state = chat_routes._state_for("local-helper-test")
    state["presented_findings"] = []
    chat_routes._remember_findings(state, keys)
    chat_routes._remember_findings(state, keys)  # idempotent, no duplicates
    assert state["presented_findings"] == ["duplicate:INV-RPT"]

    note = chat_routes._session_context_note(state)
    assert "INV-RPT" in note and "already" in note.lower()


def test_repeat_request_is_acknowledged_not_re_derived() -> None:
    db_path = _make_db()
    # Force the deterministic (offline) response path so the test does not depend
    # on a live LLM. Repeat-detection runs before the LLM call regardless.
    original = chat_routes._safe_llm_client
    chat_routes._safe_llm_client = lambda: None
    chat_routes._CHAT_STATES.pop("CUST-RPT", None)
    try:
        with TestClient(create_app(db_path=db_path)) as client:
            first = _final_response(client, "CUST-RPT", "I was charged twice and want a refund")
            second = _final_response(client, "CUST-RPT", "I was charged twice and want a refund")
            third = _final_response(client, "CUST-RPT", "please refund the double charge")
    finally:
        chat_routes._safe_llm_client = original
        chat_routes._CHAT_STATES.pop("CUST-RPT", None)

    first_text = first["text"]
    second_text = second["text"]

    # Turn 1 presents the evidence without claiming the credit was applied.
    assert "INV-RPT" in first_text
    assert "already been over" not in first_text
    assert "already applied" not in first_text.lower()

    # Later turns acknowledge it is already handled and offer a next step,
    # rather than re-deriving the same explanation.
    for repeat_text in (second_text, third["text"]):
        assert "already been over" in repeat_text
        assert "INV-RPT" in repeat_text
        assert ("escalate" in repeat_text.lower()) or ("specialist" in repeat_text.lower())

    # State remembers the finding across turns.
    assert "duplicate:INV-RPT" in (second.get("conversation_state") or {}).get("presented_findings", [])


def test_session_state_persists_across_restart() -> None:
    db_path = _make_db()
    original = chat_routes._safe_llm_client
    chat_routes._safe_llm_client = lambda: None
    chat_routes._CHAT_STATES.pop("CUST-RPT", None)
    try:
        # First process: present the finding, which should be saved to SQLite.
        with TestClient(create_app(db_path=db_path)) as client:
            _final_response(client, "CUST-RPT", "I was charged twice and want a refund")

        # State row exists and remembers the finding.
        stored = chat_routes._read_state_row("CUST-RPT", db_path)
        assert stored is not None
        assert "duplicate:INV-RPT" in stored.get("presented_findings", [])
        assert stored.get("turn_count", 0) >= 1

        # Simulate a server restart: the in-memory cache is gone.
        chat_routes._CHAT_STATES.pop("CUST-RPT", None)
        assert "CUST-RPT" not in chat_routes._CHAT_STATES

        # Second process rehydrates from the database and still knows it was handled.
        with TestClient(create_app(db_path=db_path)) as client:
            after = _final_response(client, "CUST-RPT", "I was charged twice and want a refund")
        assert "already been over" in after["text"]
        assert "duplicate:INV-RPT" in (after.get("conversation_state") or {}).get("presented_findings", [])
        # turn_count continued from the persisted value rather than resetting to 1.
        assert (after.get("conversation_state") or {}).get("turn_count", 0) >= 2
    finally:
        chat_routes._safe_llm_client = original
        chat_routes._CHAT_STATES.pop("CUST-RPT", None)


def test_overclaim_guard_blocks_unbacked_refund_promises() -> None:
    tools = [
        {"tool_name": "check_duplicate_charge", "result": {"duplicate_confirmed": True, "invoice_id": "INV-RPT", "duplicate_amount": 1199}},
    ]
    overclaims = [
        "Rahul, we're processing a credit of INR 1199 that will appear on your next statement.",
        "I have applied the refund and it will be credited shortly.",
        "We will now process a refund back to your account.",
    ]
    for text in overclaims:
        assert chat_routes._response_overclaims(text, tools) is True, text

    safe = "Rahul, there is duplicate payment evidence on invoice INV-RPT for INR 1199. The next action is ready for the policy gate."
    assert chat_routes._response_overclaims(safe, tools) is False


def test_recancel_after_request_created_reconfirms_instead_of_restarting() -> None:
    # Fresh request offered: "cancel" advances the pending choice to confirmation.
    pending_state = {
        "active_flow": "cancellation",
        "pending_customer_choice": "cancel_now_or_resolve_credit_first",
        "cancellation_request": None,
    }
    assert chat_routes._normalize_chat_intent("cancel", pending_state) == "cancellation_confirmation"

    # Request already created this session: re-raising it must re-confirm (so the
    # agent says it is already open) rather than restart the options flow.
    completed_state = {
        "active_flow": "cancellation",
        "pending_customer_choice": None,
        "cancellation_request": {"cancellation_request_id": "CAN-123456"},
    }
    assert chat_routes._normalize_chat_intent("cancel", completed_state) == "cancellation_confirmation"
    assert chat_routes._normalize_chat_intent("please cancel my plan", completed_state) == "cancellation_confirmation"

    # With no prior cancellation, a cancel message is a fresh request.
    fresh_state = {"active_flow": "none", "pending_customer_choice": None, "cancellation_request": None}
    assert chat_routes._normalize_chat_intent("I want to cancel", fresh_state) == "cancellation_request"


if __name__ == "__main__":
    test_finding_helpers_track_and_detect_repeats()
    test_repeat_request_is_acknowledged_not_re_derived()
    test_session_state_persists_across_restart()
    test_overclaim_guard_blocks_unbacked_refund_promises()
    test_recancel_after_request_created_reconfirms_instead_of_restarting()
    print("chat repeat awareness tests passed")
