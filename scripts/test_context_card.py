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
from backend.tools import generate_context_card, generate_handoff_summary, generate_opening_line  # noqa: E402
from seed_customers import seed_customers  # noqa: E402


def build_context_card_db() -> Path:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-context-card-")) / "resolveflow.db"
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
                "sess-context-card-001",
                "CUST-1001",
                json.dumps(
                    [
                        {"role": "assistant", "content": "I am checking your invoice and outage details."},
                        {"role": "user", "content": "I was charged twice and my router is still offline."},
                    ]
                ),
                json.dumps(["duplicate_charge", "service_outage"]),
                json.dumps({"customer_id": "CUST-1001", "invoice_id": "INV-8821", "location": "Chennai Zone-04"}),
                json.dumps(
                    [
                        "lookup_customer",
                        {"tool_name": "retrieve_policy", "result": {"policy_id": "duplicate_charge_refund_policy"}},
                        {"tool_name": "run_router_diagnostic", "result": {"router_status": "offline"}},
                    ]
                ),
                json.dumps([64, 38, 26]),
                "escalated",
                58,
                26,
                -32,
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
                "case-context-card-001",
                "CUST-1001",
                "sess-context-card-001",
                json.dumps(["check_duplicate_charge", "retrieve_policy"]),
                json.dumps(["INV-8821 duplicate payment evidence", "router diagnostic offline"]),
                json.dumps(
                    [
                        {"intent": "duplicate_charge", "status": "resolved", "action": "refund_review_ticket"},
                        {"intent": "service_outage", "status": "pending", "action": "diagnostic_escalation"},
                    ]
                ),
                json.dumps(["check_duplicate_confirmed", "create_refund_review_ticket"]),
                0.66,
                "needs_review",
                26,
                1,
            ),
        )
        connection.execute(
            """
            INSERT INTO human_handoff_queue(
                handoff_id,
                case_id,
                customer_id,
                context_card,
                handoff_reason,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "HND-CONTEXT-001",
                "case-context-card-001",
                "CUST-1001",
                "{}",
                "Customer needs specialist help after failed router checks.",
                "waiting",
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_store(memory_id, customer_id, memory_type, content, entity_tags, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "mem-context-001",
                "CUST-1001",
                "session",
                "Customer reported duplicate charge on INV-8821 and router offline in Chennai Zone-04.",
                json.dumps(["INV-8821", "router offline"]),
                "sess-context-card-001",
            ),
        )
    return db_path


def assert_generates_full_context_card() -> None:
    db_path = build_context_card_db()
    card = generate_context_card(
        "sess-context-card-001",
        handoff_reason="Router remains offline after verification.",
        db_path=db_path,
    )

    if card is None:
        raise AssertionError("expected context card")
    if not card["context_card_id"].startswith("CTX-") or card["source"] != "customer_context_card":
        raise AssertionError(f"context card identity missing: {card}")
    if card["session_id"] != "sess-context-card-001" or card["customer_id"] != "CUST-1001":
        raise AssertionError(f"session/customer missing: {card}")
    if card["case_id"] != "case-context-card-001":
        raise AssertionError(f"case id missing: {card}")
    if card["customer"]["plan_name"] != "Fiber Plus 200" or card["customer"]["location"] != "Chennai Zone-04":
        raise AssertionError(f"customer plan/location missing: {card['customer']}")
    if card["current_health_score"] != 26.0 or card["emotion"] != "frustrated" or card["urgency"] != "high":
        raise AssertionError(f"health/emotion/urgency wrong: {card}")
    if card["relationship"] != {"start": 58.0, "end": 26.0, "delta": -32.0}:
        raise AssertionError(f"relationship scores missing: {card['relationship']}")
    if card["slots_collected"]["invoice_id"] != "INV-8821":
        raise AssertionError(f"slots missing: {card}")
    if "duplicate_charge_refund_policy" not in card["policies_retrieved"]:
        raise AssertionError(f"policy retrieval missing: {card['policies_retrieved']}")
    if card["policy_nodes_visited"] != ["check_duplicate_confirmed", "create_refund_review_ticket"]:
        raise AssertionError(f"policy path missing: {card['policy_nodes_visited']}")
    expected_policy_path = {
        "nodes": ["check_duplicate_confirmed", "create_refund_review_ticket"],
        "node_count": 2,
        "current_node": "create_refund_review_ticket",
        "has_started": True,
        "is_complete": True,
        "path_text": "check_duplicate_confirmed -> create_refund_review_ticket",
        "policy_status": "needs_review",
        "ujcs": 0.66,
        "source": "audit_logs.policy_dag_path",
    }
    if card["policy_dag_path_so_far"] != expected_policy_path:
        raise AssertionError(f"policy DAG path block wrong: {card['policy_dag_path_so_far']}")
    if card["audit"]["handoff_required"] is not True or card["audit"]["policy_status"] != "needs_review":
        raise AssertionError(f"audit context missing: {card['audit']}")
    if card["handoff_queue"]["handoff_id"] != "HND-CONTEXT-001" or card["handoff_queue"]["status"] != "waiting":
        raise AssertionError(f"handoff queue context missing: {card['handoff_queue']}")
    if not any(issue["intent"] == "duplicate_charge" and issue["status"] == "resolved" for issue in card["issues_resolved"]):
        raise AssertionError(f"resolved issue missing: {card['issues_resolved']}")
    if not any(issue["intent"] == "service_outage" for issue in card["issues_remaining"]):
        raise AssertionError(f"remaining issue missing: {card['issues_remaining']}")
    expected_summary = {
        "total_count": 2,
        "resolved_count": 1,
        "remaining_count": 1,
        "all_resolved": False,
        "has_remaining": True,
        "resolved_labels": ["duplicate charge"],
        "remaining_labels": ["service outage"],
        "summary_text": "1 of 2 issue(s) resolved; 1 remain: service outage.",
    }
    if card["issues_summary"] != expected_summary:
        raise AssertionError(f"resolved/remaining summary wrong: {card['issues_summary']}")
    if card["memory_context"][0]["memory_id"] != "mem-context-001":
        raise AssertionError(f"memory context missing: {card['memory_context']}")
    if card["last_customer_message"] != "I was charged twice and my router is still offline.":
        raise AssertionError(f"last customer message missing: {card['last_customer_message']}")
    if card["reason_for_escalation"] != "Router remains offline after verification.":
        raise AssertionError(f"reason override missing: {card['reason_for_escalation']}")
    if card["recommended_opening"] != (
        "Hi Rahul Sharma, I have your service outage details and prior checks, so you do not have to repeat them."
    ):
        raise AssertionError(f"recommended opening line missing: {card['recommended_opening']}")


def assert_handoff_summary_uses_context_card_builder() -> None:
    db_path = build_context_card_db()
    summary = generate_handoff_summary("sess-context-card-001", db_path=db_path)

    if summary is None:
        raise AssertionError("expected handoff summary")
    card = summary["context_card"]
    if not card["context_card_id"].startswith("CTX-"):
        raise AssertionError(f"handoff summary should embed context card result: {summary}")
    if summary["customer"] != card["customer"]:
        raise AssertionError(f"summary customer should come from card: {summary}")
    if summary["policy_nodes_visited"] != card["policy_nodes_visited"]:
        raise AssertionError(f"summary policy path should come from card: {summary}")
    if card["policy_dag_path_so_far"]["current_node"] != "create_refund_review_ticket":
        raise AssertionError(f"summary should carry policy DAG path block: {summary}")
    if card["issues_summary"]["remaining_labels"] != ["service outage"]:
        raise AssertionError(f"summary should carry resolved/remaining issue labels: {summary}")


def assert_missing_and_invalid_context_card_inputs() -> None:
    db_path = build_context_card_db()
    if generate_context_card("missing-session", db_path=db_path) is not None:
        raise AssertionError("missing conversation should return None")

    bad_calls = (
        {"conversation_id": "   ", "db_path": db_path},
        {"conversation_id": "sess-context-card-001", "handoff_reason": "   ", "db_path": db_path},
    )
    for kwargs in bad_calls:
        try:
            generate_context_card(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad context card input was accepted: {kwargs}")


def assert_generate_opening_line_from_conversation_and_card() -> None:
    db_path = build_context_card_db()
    from_conversation = generate_opening_line(
        "sess-context-card-001",
        handoff_reason="Router remains offline after verification.",
        db_path=db_path,
    )
    if from_conversation is None:
        raise AssertionError("expected opening line")
    if not from_conversation["opening_line_id"].startswith("OPN-"):
        raise AssertionError(f"opening id missing: {from_conversation}")
    if from_conversation["opening_line"] != (
        "Hi Rahul Sharma, I have your service outage details and prior checks, so you do not have to repeat them."
    ):
        raise AssertionError(f"wrong conversation opening line: {from_conversation}")
    if from_conversation["customer_id"] != "CUST-1001" or from_conversation["customer_name"] != "Rahul Sharma":
        raise AssertionError(f"customer context missing: {from_conversation}")
    if from_conversation["issue_labels"] != ["service outage"] or from_conversation["has_remaining_issues"] is not True:
        raise AssertionError(f"issue labels missing: {from_conversation}")

    card = generate_context_card("sess-context-card-001", db_path=db_path)
    from_card = generate_opening_line(context_card=card)
    if from_card["opening_line"] != card["recommended_opening"]:
        raise AssertionError(f"context-card opening should match card recommendation: {from_card} {card}")

    resolved_card = {
        "customer": {"customer_id": "CUST-2000", "name": "Maya Iyer"},
        "issues_remaining": [],
        "reason_for_escalation": "All requested checks are complete.",
    }
    resolved = generate_opening_line(context_card=resolved_card)
    if resolved["opening_line"] != (
        "Hi Maya Iyer, I have the case context and escalation reason: All requested checks are complete, "
        "so I can continue from the last step."
    ):
        raise AssertionError(f"resolved-card opening line wrong: {resolved}")
    if resolved["has_remaining_issues"] is not False:
        raise AssertionError(f"resolved card should not have remaining issues: {resolved}")


def assert_missing_and_invalid_opening_line_inputs() -> None:
    db_path = build_context_card_db()
    if generate_opening_line("missing-session", db_path=db_path) is not None:
        raise AssertionError("missing conversation should return None")

    bad_calls = (
        lambda: generate_opening_line(db_path=db_path),
        lambda: generate_opening_line("", db_path=db_path),
        lambda: generate_opening_line(context_card=[], db_path=db_path),
        lambda: generate_opening_line(context_card={}, handoff_reason="   ", db_path=db_path),
    )
    for call in bad_calls:
        try:
            call()
        except ValueError:
            pass
        else:
            raise AssertionError("bad opening line input was accepted")


def assert_generate_context_card_api_endpoint() -> None:
    db_path = build_context_card_db()
    with TestClient(create_app(db_path=db_path)) as client:
        response = client.post(
            "/api/tools/generate_context_card",
            json={
                "conversation_id": "sess-context-card-001",
                "handoff_reason": "Specialist needs full context.",
            },
        )
        if response.status_code != 200:
            raise AssertionError(f"context card endpoint failed: {response.status_code} {response.text}")
        payload = response.json()
        if payload["tool_name"] != "generate_context_card" or payload["ok"] is not True:
            raise AssertionError(f"wrong tool envelope: {payload}")
        if payload["result"]["customer"]["customer_id"] != "CUST-1001":
            raise AssertionError(f"wrong endpoint payload: {payload}")

        missing = client.post(
            "/api/tools/generate_context_card",
            json={"conversation_id": "missing-session"},
        )
        if missing.status_code != 404:
            raise AssertionError(f"missing conversation should return 404: {missing.status_code} {missing.text}")

        invalid = client.post(
            "/api/tools/generate_context_card",
            json={"conversation_id": ""},
        )
        if invalid.status_code != 422:
            raise AssertionError(f"empty conversation should return 422: {invalid.status_code} {invalid.text}")


def assert_generate_opening_line_api_endpoint() -> None:
    db_path = build_context_card_db()
    with TestClient(create_app(db_path=db_path)) as client:
        response = client.post(
            "/api/tools/generate_opening_line",
            json={
                "conversation_id": "sess-context-card-001",
                "handoff_reason": "Specialist needs full context.",
            },
        )
        if response.status_code != 200:
            raise AssertionError(f"opening line endpoint failed: {response.status_code} {response.text}")
        payload = response.json()
        if payload["tool_name"] != "generate_opening_line" or payload["ok"] is not True:
            raise AssertionError(f"wrong opening envelope: {payload}")
        if "do not have to repeat" not in payload["result"]["opening_line"]:
            raise AssertionError(f"wrong opening endpoint payload: {payload}")

        context_response = client.post(
            "/api/tools/generate_opening_line",
            json={
                "context_card": {
                    "customer": {"customer_id": "CUST-2000", "name": "Maya Iyer"},
                    "issues_remaining": [{"intent": "billing", "label": "billing question"}],
                    "reason_for_escalation": "Agent should continue billing follow-up.",
                }
            },
        )
        if context_response.status_code != 200:
            raise AssertionError(
                f"opening line endpoint should accept context card: {context_response.status_code} {context_response.text}"
            )
        if context_response.json()["result"]["issue_labels"] != ["billing question"]:
            raise AssertionError(f"context-card opening endpoint wrong: {context_response.json()}")

        missing = client.post(
            "/api/tools/generate_opening_line",
            json={"conversation_id": "missing-session"},
        )
        if missing.status_code != 404:
            raise AssertionError(f"missing conversation should return 404: {missing.status_code} {missing.text}")

        invalid = client.post(
            "/api/tools/generate_opening_line",
            json={},
        )
        if invalid.status_code != 422:
            raise AssertionError(f"missing input should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_generates_full_context_card()
    assert_handoff_summary_uses_context_card_builder()
    assert_missing_and_invalid_context_card_inputs()
    assert_generate_opening_line_from_conversation_and_card()
    assert_missing_and_invalid_opening_line_inputs()
    assert_generate_context_card_api_endpoint()
    assert_generate_opening_line_api_endpoint()
    print("context card tests passed")


if __name__ == "__main__":
    main()
