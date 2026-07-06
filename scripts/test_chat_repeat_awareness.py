from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

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


def _final_response(
    client: TestClient,
    customer_id: str,
    message: str,
    session_id: str = "default",
) -> dict:
    final: dict = {}
    with client.stream(
        "GET",
        "/api/chat/message/stream",
        params={"customer_id": customer_id, "session_id": session_id, "message": message},
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
    chat_routes._CHAT_STATES.pop(("CUST-RPT", "default"), None)
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
        stored = chat_routes._read_state_row("CUST-RPT", "default", db_path)
        assert stored is not None
        assert "duplicate:INV-RPT" in stored.get("presented_findings", [])
        assert stored.get("turn_count", 0) >= 1

        # Simulate a server restart: the in-memory cache is gone.
        chat_routes._CHAT_STATES.pop(("CUST-RPT", "default"), None)
        assert ("CUST-RPT", "default") not in chat_routes._CHAT_STATES

        # Second process rehydrates from the database and still knows it was handled.
        with TestClient(create_app(db_path=db_path)) as client:
            after = _final_response(client, "CUST-RPT", "I was charged twice and want a refund")
        assert "already been over" in after["text"]
        assert "duplicate:INV-RPT" in (after.get("conversation_state") or {}).get("presented_findings", [])
        # turn_count continued from the persisted value rather than resetting to 1.
        assert (after.get("conversation_state") or {}).get("turn_count", 0) >= 2
    finally:
        chat_routes._safe_llm_client = original
        chat_routes._CHAT_STATES.pop(("CUST-RPT", "default"), None)


def test_concurrent_streams_for_same_customer_preserve_both_turns() -> None:
    db_path = _make_db()
    original = chat_routes._safe_llm_client
    chat_routes._safe_llm_client = lambda: None
    chat_routes._CHAT_STATES.pop(("CUST-RPT", "default"), None)
    chat_routes._CHAT_STATE_LOCKS.pop(("CUST-RPT", "default"), None)
    app = create_app(db_path=db_path)

    def send(message: str) -> dict:
        with TestClient(app) as client:
            return _final_response(client, "CUST-RPT", message)

    messages = [
        "I was charged twice and want a refund",
        "Please check my duplicate charge again",
    ]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(send, messages))

        assert all(result.get("text") for result in results)
        stored = chat_routes._read_state_row("CUST-RPT", "default", db_path)
        assert stored is not None
        assert stored.get("turn_count") == 2
        history = stored.get("history") or []
        user_messages = [turn.get("content") for turn in history if turn.get("role") == "user"]
        for message in messages:
            assert message in user_messages, stored
    finally:
        chat_routes._safe_llm_client = original
        chat_routes._CHAT_STATES.pop(("CUST-RPT", "default"), None)
        chat_routes._CHAT_STATE_LOCKS.pop(("CUST-RPT", "default"), None)


def test_same_session_stream_sends_first_bytes_while_prior_turn_is_slow() -> None:
    db_path = _make_db()
    original_llm = chat_routes._safe_llm_client
    original_classifier = chat_routes._safe_classifier_client
    slow_reply_started = threading.Event()
    chat_routes._CHAT_STATES.pop(("CUST-RPT", "default"), None)
    chat_routes._CHAT_STATE_LOCKS.pop(("CUST-RPT", "default"), None)
    app = create_app(db_path=db_path)

    class SlowReplyLLM:
        def generate(self, prompt, **_kwargs):  # noqa: ANN001 - mirrors LLMClient.generate.
            if "Customer Message:" in str(prompt):
                slow_reply_started.set()
                time.sleep(0.6)
            return "Rahul, I checked the account evidence and will keep this grounded."

    chat_routes._safe_llm_client = lambda: SlowReplyLLM()
    chat_routes._safe_classifier_client = lambda: None

    async def consume_all(message: str) -> None:
        response = chat_routes.chat_message_stream(
            SimpleNamespace(app=app),
            customer_id="CUST-RPT",
            session_id="default",
            message=message,
        )
        async for _chunk in response.body_iterator:
            pass

    async def first_event_while_prior_turn_is_slow() -> tuple[dict, float]:
        slow_task = asyncio.create_task(consume_all("Can you explain my current bill?"))
        await asyncio.wait_for(asyncio.to_thread(slow_reply_started.wait), timeout=5)
        response = chat_routes.chat_message_stream(
            SimpleNamespace(app=app),
            customer_id="CUST-RPT",
            session_id="default",
            message="Please check the duplicate charge too",
        )
        started = time.perf_counter()
        chunk = await asyncio.wait_for(anext(response.body_iterator), timeout=0.4)
        elapsed = time.perf_counter() - started
        if hasattr(response.body_iterator, "aclose"):
            await response.body_iterator.aclose()
        await asyncio.wait_for(slow_task, timeout=5)
        return json.loads(chunk.removeprefix("data: ")), elapsed

    try:
        first_event, elapsed = asyncio.run(first_event_while_prior_turn_is_slow())
    finally:
        chat_routes._safe_llm_client = original_llm
        chat_routes._safe_classifier_client = original_classifier
        chat_routes._CHAT_STATES.pop(("CUST-RPT", "default"), None)
        chat_routes._CHAT_STATE_LOCKS.pop(("CUST-RPT", "default"), None)

    assert first_event is not None
    assert first_event["step"] == "intent"
    assert first_event["status"] == "running"
    assert elapsed < 0.4, f"second stream waited silently for {elapsed:.3f}s"


def test_two_sessions_for_same_customer_do_not_clobber_each_other() -> None:
    db_path = _make_db()
    original = chat_routes._safe_llm_client
    chat_routes._safe_llm_client = lambda: None
    for key in [("CUST-RPT", "tab-a"), ("CUST-RPT", "tab-b")]:
        chat_routes._CHAT_STATES.pop(key, None)
        chat_routes._CHAT_STATE_LOCKS.pop(key, None)
    app = create_app(db_path=db_path)

    def send(args: tuple[str, str]) -> dict:
        session_id, message = args
        with TestClient(app) as client:
            return _final_response(client, "CUST-RPT", message, session_id=session_id)

    turns = [
        ("tab-a", "I was charged twice and want a refund"),
        ("tab-b", "I want to cancel my subscription"),
    ]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(send, turns))

        assert all(result.get("text") for result in results)
        state_a = chat_routes._read_state_row("CUST-RPT", "tab-a", db_path)
        state_b = chat_routes._read_state_row("CUST-RPT", "tab-b", db_path)
        assert state_a is not None
        assert state_b is not None
        assert state_a.get("session_id") == "tab-a"
        assert state_b.get("session_id") == "tab-b"
        assert state_a.get("active_flow") != "cancellation", state_a
        assert state_b.get("active_flow") == "cancellation", state_b
        history_a = [turn.get("content") for turn in (state_a.get("history") or [])]
        history_b = [turn.get("content") for turn in (state_b.get("history") or [])]
        assert turns[0][1] in history_a
        assert turns[1][1] not in history_a
        assert turns[1][1] in history_b
        assert turns[0][1] not in history_b
    finally:
        chat_routes._safe_llm_client = original
        for key in [("CUST-RPT", "tab-a"), ("CUST-RPT", "tab-b")]:
            chat_routes._CHAT_STATES.pop(key, None)
            chat_routes._CHAT_STATE_LOCKS.pop(key, None)


def test_session_state_sqlite_helpers_run_off_event_loop() -> None:
    db_path = _make_db()
    original_load = chat_routes._load_session_state
    original_save = chat_routes._save_session_state
    original_abort = chat_routes._abort_cancellation
    original_llm = chat_routes._safe_llm_client
    calls: list[str] = []

    def assert_not_on_event_loop(name: str) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            calls.append(name)
            return
        raise AssertionError(f"{name} ran synchronous SQLite work on the SSE event loop")

    def wrapped_load(customer_id, session_id, db_path_arg):
        assert_not_on_event_loop("load")
        return original_load(customer_id, session_id, db_path_arg)

    def wrapped_save(customer_id, session_id, state, db_path_arg):
        assert_not_on_event_loop("save")
        return original_save(customer_id, session_id, state, db_path_arg)

    def wrapped_abort(state, customer_id=None, session_id="default", db_path_arg=None):
        assert_not_on_event_loop("abort")
        return original_abort(state, customer_id, session_id, db_path_arg)

    chat_routes._load_session_state = wrapped_load
    chat_routes._save_session_state = wrapped_save
    chat_routes._abort_cancellation = wrapped_abort
    chat_routes._safe_llm_client = lambda: None
    chat_routes._CHAT_STATES.pop(("CUST-RPT", "default"), None)
    try:
        with TestClient(create_app(db_path=db_path)) as client:
            _final_response(client, "CUST-RPT", "I want to cancel my subscription")
            _final_response(client, "CUST-RPT", "never mind, do not cancel")
    finally:
        chat_routes._load_session_state = original_load
        chat_routes._save_session_state = original_save
        chat_routes._abort_cancellation = original_abort
        chat_routes._safe_llm_client = original_llm
        chat_routes._CHAT_STATES.pop(("CUST-RPT", "default"), None)

    for expected in ("load", "save", "abort"):
        if expected not in calls:
            raise AssertionError(f"{expected} helper did not run during the chat flow: {calls}")


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
    test_concurrent_streams_for_same_customer_preserve_both_turns()
    test_same_session_stream_sends_first_bytes_while_prior_turn_is_slow()
    test_two_sessions_for_same_customer_do_not_clobber_each_other()
    test_session_state_sqlite_helpers_run_off_event_loop()
    test_overclaim_guard_blocks_unbacked_refund_promises()
    test_recancel_after_request_created_reconfirms_instead_of_restarting()
    print("chat repeat awareness tests passed")
