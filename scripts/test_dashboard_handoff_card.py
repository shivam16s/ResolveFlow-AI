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
from backend.dashboard import RenderedHandoffCard, render_case_handoff_tab  # noqa: E402
from seed_customers import seed_customers  # noqa: E402


def build_dashboard_db() -> Path:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-dashboard-handoff-")) / "resolveflow.db"
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
                "sess-dashboard-handoff-001",
                "CUST-1001",
                json.dumps(
                    [
                        {"role": "assistant", "content": "I checked the payment and router records."},
                        {"role": "user", "content": "I paid twice and the router is still offline."},
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
                json.dumps([66, 42, 28]),
                "escalated",
                59,
                28,
                -31,
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
                "case-dashboard-handoff-001",
                "CUST-1001",
                "sess-dashboard-handoff-001",
                json.dumps(["check_duplicate_charge", "retrieve_policy"]),
                json.dumps(["INV-8821 duplicate payment evidence", "<script>alert('bad')</script>"]),
                json.dumps(
                    [
                        {"intent": "duplicate_charge", "status": "resolved", "action": "refund_review_ticket"},
                        {"intent": "service_outage", "status": "pending", "action": "diagnostic_escalation"},
                    ]
                ),
                json.dumps(["check_duplicate_confirmed", "create_refund_review_ticket"]),
                0.66,
                "needs_review",
                28,
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
                "HND-DASH-001",
                "case-dashboard-handoff-001",
                "CUST-1001",
                "{}",
                "Customer needs specialist help for unresolved outage.",
                "waiting",
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_store(memory_id, customer_id, memory_type, content, entity_tags, session_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "mem-dashboard-001",
                "CUST-1001",
                "session",
                "Customer reported duplicate charge and offline router in Chennai Zone-04.",
                json.dumps(["INV-8821", "router offline"]),
                "sess-dashboard-handoff-001",
            ),
        )
    return db_path


def assert_renders_case_detail_handoff_tab() -> None:
    db_path = build_dashboard_db()
    rendered = render_case_handoff_tab("case-dashboard-handoff-001", db_path=db_path)

    if not isinstance(rendered, RenderedHandoffCard):
        raise AssertionError(f"wrong rendered handoff type: {rendered}")
    if rendered.case_id != "case-dashboard-handoff-001" or rendered.customer_id != "CUST-1001":
        raise AssertionError(f"wrong rendered identity: {rendered.to_dict()}")
    html = rendered.html
    expected_fragments = (
        'data-testid="handoff-context-card"',
        'aria-selected="true">Handoff</a>',
        "Rahul Sharma",
        "Hi Rahul Sharma, I have your service outage details and prior checks, so you do not have to repeat them.",
        "1 of 2 issue(s) resolved; 1 remain: service outage.",
        "HND-DASH-001",
        "Handoff: waiting",
        "check_duplicate_confirmed",
        "create_refund_review_ticket",
        "needs_review",
        "Customer reported duplicate charge and offline router in Chennai Zone-04.",
        "I paid twice and the router is still offline.",
    )
    for fragment in expected_fragments:
        if fragment not in html:
            raise AssertionError(f"rendered HTML missing {fragment!r}: {html}")
    if "<script>alert" in html:
        raise AssertionError(f"unsafe script tag should be escaped: {html}")
    if "&lt;script&gt;alert" not in html:
        raise AssertionError(f"escaped evidence should be visible: {html}")


def assert_handoff_tab_endpoint_serves_html() -> None:
    db_path = build_dashboard_db()
    with TestClient(create_app(db_path=db_path)) as client:
        response = client.get("/api/cases/case-dashboard-handoff-001/handoff")
        if response.status_code != 200:
            raise AssertionError(f"handoff tab endpoint failed: {response.status_code} {response.text}")
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            raise AssertionError(f"handoff tab should serve HTML: {content_type}")
        if "Case Detail - Handoff" not in response.text:
            raise AssertionError(f"handoff tab title missing: {response.text}")
        if 'data-testid="policy-path"' not in response.text:
            raise AssertionError(f"policy path section missing: {response.text}")

        missing = client.get("/api/cases/missing-case/handoff")
        if missing.status_code != 404:
            raise AssertionError(f"missing case should return 404: {missing.status_code} {missing.text}")

        invalid = client.get("/api/cases/%20%20%20/handoff")
        if invalid.status_code != 422:
            raise AssertionError(f"blank case should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_renders_case_detail_handoff_tab()
    assert_handoff_tab_endpoint_serves_html()
    print("dashboard handoff card tests passed")


if __name__ == "__main__":
    main()
