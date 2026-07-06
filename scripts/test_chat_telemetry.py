from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api import create_app  # noqa: E402
from backend.api.chat_routes import _health_score_for  # noqa: E402
from backend.agent.health import compute_health_score  # noqa: E402
from backend.db.seed_demo_dashboard import seed_demo_dashboard  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_chat_stream_records_turn_telemetry() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-chat-telemetry-")) / "resolveflow.db"
    seed_demo_dashboard(db_path)

    with TestClient(create_app(db_path=db_path)) as client:
        with client.stream(
            "GET",
            "/api/chat/message/stream",
            params={
                "customer_id": "CUST-1001",
                "session_id": "telemetry-test",
                "message": "Hi, can you check my account?",
            },
        ) as response:
            assert response.status_code == 200, response.text
            events = [
                line.removeprefix("data: ")
                for line in response.iter_lines()
                if line.startswith("data: ")
            ]
        summary_response = client.get("/api/telemetry/summary")

    parsed = [json.loads(event) for event in events]
    assert any(
        event["step"] == "response" and event["status"] == "done"
        for event in parsed
    )

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT session_id, customer_id, latency_ms, input_tokens, output_tokens,
                   total_tokens, stage_breakdown
            FROM telemetry
            WHERE session_id = ?
            """,
            ("telemetry-test",),
        ).fetchone()

    assert row is not None
    assert row["customer_id"] == "CUST-1001"
    assert row["latency_ms"] >= 0
    assert row["input_tokens"] > 0
    assert row["output_tokens"] > 0
    assert row["total_tokens"] == row["input_tokens"] + row["output_tokens"]
    stage_breakdown = json.loads(row["stage_breakdown"])
    for stage in ("intent", "memory", "policy", "tools", "dag", "response"):
        assert stage in stage_breakdown
        assert stage_breakdown[stage] >= 0
    assert summary_response.status_code == 200, summary_response.text
    summary = summary_response.json()
    assert summary["turns"] >= 1
    assert summary["p50_latency_ms"] >= 0
    assert summary["p95_latency_ms"] >= summary["p50_latency_ms"]
    assert summary["avg_tokens_per_resolution"] > 0
    assert summary["estimated_cost_inr"] > 0


def test_live_chat_health_uses_weighted_formula() -> None:
    score = _health_score_for(
        "neutral",
        ["service_outage"],
        customer={
            "customer_id": "CUST-1001",
            "location": "Chennai Zone-04",
            "plan_id": "fiber_plus_200",
        },
        message="My internet is down?",
        chat_state={"history": [{"role": "user", "content": "My internet is down?"}]},
        tool_results=[{"tool_name": "check_outage_status", "ok": True}],
        policy_results=[{"confidence": 0.9}],
        intent_confidence=0.8,
    )
    expected = compute_health_score(
        intent_confidence=0.8,
        missing_info_risk=0.0,
        sentiment_score=0.65,
        loop_penalty=0.5,
        knowledge_coverage=0.95,
    ).score
    assert score == round(expected)


if __name__ == "__main__":
    test_chat_stream_records_turn_telemetry()
    test_live_chat_health_uses_weighted_formula()
    print("chat telemetry tests passed")
