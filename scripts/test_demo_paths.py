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
from backend.agent.guided_action import GuidedActionCoordinator  # noqa: E402
from backend.api import create_app  # noqa: E402
from backend.db.seed_demo_dashboard import (  # noqa: E402
    DEMO_CUSTOMER_ID,
    DEMO_SESSION_ID,
    seed_demo_dashboard,
)
from fastapi.testclient import TestClient  # noqa: E402


def _seeded_db() -> Path:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-demo-paths-")) / "resolveflow.db"
    seed_demo_dashboard(db_path)
    return db_path


def _stream_events(client: TestClient, message: str, session_id: str) -> list[dict]:
    events: list[dict] = []
    with client.stream(
        "GET",
        "/api/chat/message/stream",
        params={
            "customer_id": DEMO_CUSTOMER_ID,
            "session_id": session_id,
            "message": message,
        },
    ) as response:
        assert response.status_code == 200, response.text
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


def _done(events: list[dict], step: str) -> dict:
    for event in events:
        if event["step"] == step and event["status"] == "done":
            return event["result"]
    raise AssertionError(f"missing done event for {step}: {events}")


def test_easy_demo_path_bill_tool_call_response() -> None:
    db_path = _seeded_db()
    original_llm = chat_routes._safe_llm_client
    original_classifier = chat_routes._safe_classifier_client
    chat_routes._safe_llm_client = lambda: None
    chat_routes._safe_classifier_client = lambda: None
    try:
        with TestClient(create_app(db_path=db_path)) as client:
            events = _stream_events(client, "Can you show my latest bill?", "easy-demo")
    finally:
        chat_routes._safe_llm_client = original_llm
        chat_routes._safe_classifier_client = original_classifier

    intent = _done(events, "intent")
    assert "billing_dispute" in intent["intents"]

    tool_names = [tool["tool_name"] for tool in _done(events, "tools")["tools"]]
    assert "get_invoice_history" in tool_names
    assert "check_duplicate_charge" in tool_names

    response = _done(events, "response")
    assert "INV-8821" in response["text"]
    assert "duplicate payment evidence" in response["text"]


def test_hard_demo_path_three_intents_and_cancellation_tools() -> None:
    db_path = _seeded_db()
    original_llm = chat_routes._safe_llm_client
    original_classifier = chat_routes._safe_classifier_client
    chat_routes._safe_llm_client = lambda: None
    chat_routes._safe_classifier_client = lambda: None
    try:
        with TestClient(create_app(db_path=db_path)) as client:
            events = _stream_events(
                client,
                "I was charged twice this month, my internet is still down, and I want to cancel.",
                "hard-demo",
            )
    finally:
        chat_routes._safe_llm_client = original_llm
        chat_routes._safe_classifier_client = original_classifier

    intents = set(_done(events, "intent")["intents"])
    assert {"duplicate_charge", "service_outage", "cancellation_intent"} <= intents

    tool_names = [tool["tool_name"] for tool in _done(events, "tools")["tools"]]
    for expected in (
        "get_invoice_history",
        "check_duplicate_charge",
        "check_outage_status",
        "get_subscription_status",
        "get_cancellation_policy",
        "check_pending_credits",
    ):
        assert expected in tool_names

    response = _done(events, "response")
    text = response["text"].lower()
    assert "duplicate charge" in text or "duplicate" in text
    assert "outage" in text
    assert "cancel" in text
    assert response["acknowledgment"]


def test_router_reset_demo_moment_reaches_183_mbps() -> None:
    coordinator = GuidedActionCoordinator("router_reset", DEMO_CUSTOMER_ID)
    instruction = coordinator.instruct()
    assert instruction.state == "WAITING"

    verification = coordinator.handle_user_report(
        "done",
        lambda customer_id: {"customer_id": customer_id, "download_mbps": 183},
        tool_name="speed_test",
        success_evaluator=lambda result: result["download_mbps"] >= 100,
    )
    assert verification.state == "RESOLVED"
    history = coordinator.to_dict()["transition_history"]
    assert [event["to_state"] for event in history] == ["WAITING", "VERIFYING", "RESOLVED"]
    assert history[-1]["metadata"]["tool_result"]["download_mbps"] == 183


def test_seeded_hard_demo_relationship_arc() -> None:
    db_path = _seeded_db()
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT relationship_score_start, relationship_score_end,
                   relationship_delta, health_scores
            FROM conversations
            WHERE session_id = ?
            """,
            (DEMO_SESSION_ID,),
        ).fetchone()

    assert row is not None
    assert row["relationship_score_start"] == 29
    assert row["relationship_score_end"] == 58
    assert row["relationship_delta"] == 29
    health_scores = json.loads(row["health_scores"])
    assert any("WAITING" in point["label"] for point in health_scores)


if __name__ == "__main__":
    test_easy_demo_path_bill_tool_call_response()
    test_hard_demo_path_three_intents_and_cancellation_tools()
    test_router_reset_demo_moment_reaches_183_mbps()
    test_seeded_hard_demo_relationship_arc()
    print("demo path tests passed")
