from __future__ import annotations

import sys
import tempfile
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api import create_app  # noqa: E402
from backend.db.seed_demo_dashboard import seed_demo_dashboard  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_agent_desk_queue_reads_seeded_handoffs() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-agent-desk-")) / "resolveflow.db"
    seed_demo_dashboard(db_path)

    with TestClient(create_app(db_path=db_path)) as client:
        response = client.get("/api/agent-desk/queue")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] >= 1
    first = payload["queue"][0]
    assert first["handoff_id"].startswith("HANDOFF-")
    assert first["case_id"]
    assert first["customer_id"].startswith("CUST-")
    assert first["customer_name"]
    assert first["status"] in {"waiting", "assigned", "resolved"}
    assert isinstance(first["context_card"], dict)
    assert first["recommended_opening_line"]
    assert "handoff_reason" in first


def test_agent_desk_handoff_detail_includes_context_and_transcript() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-agent-desk-detail-")) / "resolveflow.db"
    seed_demo_dashboard(db_path)

    with TestClient(create_app(db_path=db_path)) as client:
        queue_response = client.get("/api/agent-desk/queue")
        handoff_id = queue_response.json()["queue"][0]["handoff_id"]
        response = client.get(f"/api/agent-desk/handoffs/{handoff_id}")

    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["handoff_id"] == handoff_id
    assert isinstance(detail["context_card"], dict)
    assert isinstance(detail["transcript"], list)
    assert detail["transcript"], detail
    assert isinstance(detail["opening_line"], dict)
    assert detail["opening_line"]["opening_line"]
    assert isinstance(detail["policy_dag_path"], list)
    assert isinstance(detail["copilot_suggestions"], list)
    assert detail["copilot_suggestions"]
    first_suggestion = detail["copilot_suggestions"][0]
    assert first_suggestion["reply"]
    assert first_suggestion["evidence"]
    assert any(
        item["source"] in {"tool", "context_card", "policy_dag"}
        for item in first_suggestion["evidence"]
    )


def test_agent_reply_posts_into_customer_thread() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-agent-desk-reply-")) / "resolveflow.db"
    seed_demo_dashboard(db_path)

    with TestClient(create_app(db_path=db_path)) as client:
        queue_response = client.get("/api/agent-desk/queue")
        handoff = queue_response.json()["queue"][0]
        handoff_id = handoff["handoff_id"]
        reply_response = client.post(
            f"/api/agent-desk/handoffs/{handoff_id}/reply",
            json={
                "agent_name": "Priya Specialist",
                "message": "I have the duplicate charge evidence and will take it from here.",
            },
        )
        assert reply_response.status_code == 200, reply_response.text
        reply_payload = reply_response.json()
        assert reply_payload["reply"]["role"] == "human_agent"
        assert reply_payload["reply"]["agent_name"] == "Priya Specialist"
        assert reply_payload["already_replied"] is False

        retry_response = client.post(
            f"/api/agent-desk/handoffs/{handoff_id}/reply",
            json={
                "agent_name": "Priya Specialist",
                "message": "I have the duplicate charge evidence and will take it from here.",
            },
        )
        assert retry_response.status_code == 200, retry_response.text
        assert retry_response.json()["already_replied"] is True

        detail_response = client.get(f"/api/agent-desk/handoffs/{handoff_id}")
        transcript = detail_response.json()["transcript"]
        assert any(
            turn.get("role") == "human_agent"
            and "duplicate charge evidence" in turn.get("content", "")
            for turn in transcript
        )
        assert sum(
            1
            for turn in transcript
            if turn.get("role") == "human_agent"
            and turn.get("agent_name") == "Priya Specialist"
            and "duplicate charge evidence" in turn.get("content", "")
        ) == 1

        messages_response = client.get(
            "/api/chat/session/messages",
            params={
                "customer_id": reply_payload["customer_id"],
                "session_id": reply_payload["session_id"],
            },
        )
        assert messages_response.status_code == 200, messages_response.text
        session_messages = messages_response.json()["messages"]
        assert any(
            turn.get("role") == "human_agent"
            and turn.get("agent_name") == "Priya Specialist"
            for turn in session_messages
        )
        assert sum(
            1
            for turn in session_messages
            if turn.get("role") == "human_agent"
            and turn.get("agent_name") == "Priya Specialist"
            and "duplicate charge evidence" in turn.get("content", "")
        ) == 1


def test_resolve_handoff_updates_queue_and_audit_log() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-agent-desk-resolve-")) / "resolveflow.db"
    seed_demo_dashboard(db_path)

    with TestClient(create_app(db_path=db_path)) as client:
        queue_response = client.get("/api/agent-desk/queue")
        handoff = queue_response.json()["queue"][0]
        handoff_id = handoff["handoff_id"]
        response = client.post(
            f"/api/agent-desk/handoffs/{handoff_id}/resolve",
            json={
                "agent_name": "Priya Specialist",
                "resolution_note": "Customer confirmed the human takeover is complete.",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "resolved"
    assert payload["audit_action"]["action"] == "human_handoff_resolved"

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        handoff_row = connection.execute(
            "SELECT status, assigned_to FROM human_handoff_queue WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
        conversation_row = connection.execute(
            "SELECT final_status, completed_at FROM conversations WHERE session_id = ?",
            (payload["session_id"],),
        ).fetchone()
        audit_row = connection.execute(
            "SELECT action_taken FROM audit_logs WHERE case_id = ?",
            (payload["case_id"],),
        ).fetchone()

    assert handoff_row["status"] == "resolved"
    assert handoff_row["assigned_to"] == "Priya Specialist"
    assert conversation_row["final_status"] == "resolved"
    assert conversation_row["completed_at"]
    actions = json.loads(audit_row["action_taken"])
    assert any(
        action.get("action") == "human_handoff_resolved"
        and action.get("resolution_note") == "Customer confirmed the human takeover is complete."
        for action in actions
    )


if __name__ == "__main__":
    test_agent_desk_queue_reads_seeded_handoffs()
    test_agent_desk_handoff_detail_includes_context_and_transcript()
    test_agent_reply_posts_into_customer_thread()
    test_resolve_handoff_updates_queue_and_audit_log()
    print("agent desk queue tests passed")
