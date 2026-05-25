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
from backend.dashboard import RenderedAuditLogTabs, render_case_audit_log_tabs  # noqa: E402
from seed_customers import seed_customers  # noqa: E402


def build_dashboard_audit_db() -> Path:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-dashboard-audit-")) / "resolveflow.db"
    seed_customers(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO conversations(session_id, customer_id, messages, intents, slots, tools_called)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "sess-dashboard-audit-001",
                "CUST-1001",
                json.dumps([{"role": "user", "content": "I was charged twice."}]),
                json.dumps(["duplicate_charge"]),
                json.dumps({"customer_id": "CUST-1001", "invoice_id": "INV-8821"}),
                json.dumps(["lookup_customer"]),
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
                "case-dashboard-audit-001",
                "CUST-1001",
                "sess-dashboard-audit-001",
                json.dumps(
                    [
                        {"tool_name": "lookup_customer", "status": "ok"},
                        {"tool_name": "check_duplicate_charge", "status": "ok"},
                    ]
                ),
                json.dumps(["INV-8821 duplicate evidence", "<script>alert('audit')</script>"]),
                json.dumps([{"action": "create_ticket", "ticket_id": "TKT-1001"}]),
                json.dumps(["check_duplicate_confirmed", "create_refund_review_ticket"]),
                0.92,
                "compliant",
                78,
                0,
            ),
        )
    return db_path


def assert_renders_human_and_raw_json_tabs() -> None:
    db_path = build_dashboard_audit_db()
    rendered = render_case_audit_log_tabs("case-dashboard-audit-001", db_path=db_path)

    if not isinstance(rendered, RenderedAuditLogTabs):
        raise AssertionError(f"wrong rendered audit type: {rendered}")
    if rendered.case_id != "case-dashboard-audit-001" or rendered.customer_id != "CUST-1001":
        raise AssertionError(f"wrong rendered identity: {rendered.to_dict()}")
    if rendered.raw_json["policy_status"] != "compliant" or rendered.raw_json["ujcs"] != 0.92:
        raise AssertionError(f"raw json metadata wrong: {rendered.raw_json}")

    html = rendered.html
    expected_fragments = (
        'data-testid="proof-trail-tabs"',
        'aria-selected="true">Proof Trail</a>',
        'data-testid="human-readable-tab"',
        'data-testid="raw-json-tab-label"',
        'data-testid="human-readable-panel"',
        'data-testid="raw-json-panel"',
        "Resolution Proof Trail",
        "check_duplicate_charge",
        "INV-8821 duplicate evidence",
        "create_ticket",
        "check_duplicate_confirmed",
        "create_refund_review_ticket",
        "&quot;policy_status&quot;: &quot;compliant&quot;",
        "&quot;ujcs&quot;: 0.92",
    )
    for fragment in expected_fragments:
        if fragment not in html:
            raise AssertionError(f"rendered proof trail missing {fragment!r}: {html}")
    if "<script>alert" in html:
        raise AssertionError(f"unsafe script tag should be escaped: {html}")
    if "&lt;script&gt;alert" not in html:
        raise AssertionError(f"escaped audit evidence should remain visible: {html}")


def assert_audit_log_tabs_endpoint_serves_html() -> None:
    db_path = build_dashboard_audit_db()
    with TestClient(create_app(db_path=db_path)) as client:
        response = client.get("/api/cases/case-dashboard-audit-001/audit_log")
        if response.status_code != 200:
            raise AssertionError(f"audit log tab endpoint failed: {response.status_code} {response.text}")
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            raise AssertionError(f"audit log tab should serve HTML: {content_type}")
        if "Case Detail - Proof Trail" not in response.text:
            raise AssertionError(f"proof trail title missing: {response.text}")
        if 'data-testid="raw-json-panel"' not in response.text:
            raise AssertionError(f"raw JSON panel missing: {response.text}")

        missing = client.get("/api/cases/missing-case/audit_log")
        if missing.status_code != 404:
            raise AssertionError(f"missing case should return 404: {missing.status_code} {missing.text}")

        invalid = client.get("/api/cases/%20%20%20/audit_log")
        if invalid.status_code != 422:
            raise AssertionError(f"blank case should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_renders_human_and_raw_json_tabs()
    assert_audit_log_tabs_endpoint_serves_html()
    print("dashboard audit tabs tests passed")


if __name__ == "__main__":
    main()
