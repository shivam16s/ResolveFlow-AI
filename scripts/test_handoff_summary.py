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

from fastapi.testclient import TestClient  # noqa: E402

from backend.api import create_app  # noqa: E402
from backend.tools import generate_handoff_summary  # noqa: E402
from seed_customers import seed_customers  # noqa: E402


def build_handoff_db() -> Path:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-handoff-")) / "resolveflow.db"
    seed_customers(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO conversations(
                session_id,
                customer_id,
                messages,
                intents,
                slots,
                tools_called,
                health_scores,
                final_status,
                relationship_score_start,
                relationship_score_end,
                relationship_delta
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sess-handoff-001",
                "CUST-1001",
                json.dumps(
                    [
                        {
                            "role": "user",
                            "content": "I was charged twice and my internet is still down. Get me a human.",
                        },
                        {
                            "role": "assistant",
                            "content": "I verified the duplicate charge and checked outage details.",
                        },
                    ]
                ),
                json.dumps(["duplicate_charge", "service_outage", "human_handoff"]),
                json.dumps(
                    {
                        "customer_id": "CUST-1001",
                        "invoice_id": "INV-8821",
                        "location": "Chennai Zone-04",
                    }
                ),
                json.dumps(
                    [
                        "lookup_customer",
                        {
                            "tool_name": "retrieve_policy",
                            "result": {"policy_id": "service_credit_policy"},
                        },
                        {
                            "tool_name": "check_outage_status",
                            "result": {"verified": True, "duration_hours": 7},
                        },
                    ]
                ),
                json.dumps([72, 48, 24]),
                "escalated",
                61,
                24,
                -37,
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
                ujcs,
                policy_status,
                health_score,
                handoff_required
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "case-handoff-001",
                "CUST-1001",
                "sess-handoff-001",
                json.dumps(["check_duplicate_charge", "retrieve_policy"]),
                json.dumps(["INV-8821 duplicate payment evidence", "OUT-CHN-04-20260520 verified outage"]),
                json.dumps(
                    [
                        {"intent": "duplicate_charge", "status": "resolved", "action": "refund_review_ticket"},
                        {"intent": "service_outage", "status": "pending", "action": "human_handoff"},
                    ]
                ),
                json.dumps(["check_outage_verified", "manual_review_credit"]),
                0.33,
                "needs_review",
                24,
                1,
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_store(memory_id, customer_id, memory_type, content, entity_tags, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "mem-handoff-001",
                "CUST-1001",
                "session",
                "Customer was charged twice for invoice INV-8821 and reported outage in Chennai Zone-04.",
                json.dumps(["INV-8821", "Chennai Zone-04"]),
                "sess-handoff-001",
            ),
        )
    return db_path


def assert_generates_context_card_from_conversation() -> None:
    db_path = build_handoff_db()
    result = generate_handoff_summary(
        "sess-handoff-001",
        handoff_reason="Customer explicitly requested a human after policy review.",
        db_path=db_path,
    )

    if result is None:
        raise AssertionError("expected handoff summary")
    if not result["handoff_summary_id"].startswith("HND-SUM-"):
        raise AssertionError(f"summary id not generated: {result}")
    if result["session_id"] != "sess-handoff-001" or result["customer_id"] != "CUST-1001":
        raise AssertionError(f"wrong session/customer: {result}")
    if result["reason_for_escalation"] != "Customer explicitly requested a human after policy review.":
        raise AssertionError(f"handoff reason not preserved: {result}")
    customer = result["customer"]
    if customer["name"] != "Rahul Sharma" or customer["plan_name"] != "Fiber Plus 200":
        raise AssertionError(f"customer context wrong: {customer}")
    if result["emotion"] != "frustrated" or result["urgency"] != "high":
        raise AssertionError(f"emotion/urgency should reflect low health score: {result}")
    if result["slots_collected"]["invoice_id"] != "INV-8821":
        raise AssertionError(f"slots missing: {result}")
    if "service_credit_policy" not in result["policies_retrieved"]:
        raise AssertionError(f"retrieved policy missing: {result}")
    if result["policy_nodes_visited"] != ["check_outage_verified", "manual_review_credit"]:
        raise AssertionError(f"policy path missing: {result}")
    if "OUT-CHN-04-20260520 verified outage" not in result["evidence_used"]:
        raise AssertionError(f"evidence missing: {result}")
    if not any(issue["intent"] == "duplicate_charge" and issue["status"] == "resolved" for issue in result["issues_resolved"]):
        raise AssertionError(f"resolved issue missing: {result}")
    if not any(issue["intent"] == "service_outage" for issue in result["issues_remaining"]):
        raise AssertionError(f"remaining issue missing: {result}")
    if result["memory_context"][0]["memory_id"] != "mem-handoff-001":
        raise AssertionError(f"memory context missing: {result}")
    if "do not have to repeat" not in result["recommended_opening"]:
        raise AssertionError(f"recommended opening should avoid repetition: {result}")
    context_card = result["context_card"]
    if context_card["last_customer_message"] != "I was charged twice and my internet is still down. Get me a human.":
        raise AssertionError(f"last customer message missing: {context_card}")
    if context_card["customer"]["customer_id"] != "CUST-1001":
        raise AssertionError(f"context card customer missing: {context_card}")

    with sqlite3.connect(db_path) as connection:
        handoff_rows = connection.execute("SELECT COUNT(*) FROM human_handoff_queue").fetchone()[0]
    if handoff_rows != 0:
        raise AssertionError("generate_handoff_summary should not insert into human_handoff_queue yet")


def assert_missing_and_invalid_inputs() -> None:
    db_path = build_handoff_db()
    if generate_handoff_summary("missing-session", db_path=db_path) is not None:
        raise AssertionError("missing conversation should return None")

    bad_calls = (
        {"conversation_id": "   ", "db_path": db_path},
        {"conversation_id": "sess-handoff-001", "handoff_reason": "   ", "db_path": db_path},
    )
    for kwargs in bad_calls:
        try:
            generate_handoff_summary(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad generate_handoff_summary inputs were accepted: {kwargs}")


def assert_generate_handoff_summary_api_endpoint() -> None:
    db_path = build_handoff_db()
    with TestClient(create_app(db_path=db_path)) as client:
        response = client.post(
            "/api/tools/generate_handoff_summary",
            json={
                "conversation_id": "sess-handoff-001",
                "handoff_reason": "Explicit human handoff requested.",
            },
        )
        if response.status_code != 200:
            raise AssertionError(f"handoff summary endpoint failed: {response.status_code} {response.text}")
        payload = response.json()
        if payload["tool_name"] != "generate_handoff_summary" or payload["ok"] is not True:
            raise AssertionError(f"wrong tool envelope: {payload}")
        if payload["result"]["context_card"]["customer"]["customer_id"] != "CUST-1001":
            raise AssertionError(f"wrong endpoint payload: {payload}")

        missing = client.post(
            "/api/tools/generate_handoff_summary",
            json={"conversation_id": "missing-session"},
        )
        if missing.status_code != 404:
            raise AssertionError(f"missing conversation should return 404: {missing.status_code} {missing.text}")

        invalid = client.post(
            "/api/tools/generate_handoff_summary",
            json={"conversation_id": ""},
        )
        if invalid.status_code != 422:
            raise AssertionError(f"empty conversation should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_generates_context_card_from_conversation()
    assert_missing_and_invalid_inputs()
    assert_generate_handoff_summary_api_endpoint()
    print("handoff summary tests passed")


if __name__ == "__main__":
    main()
