from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
from seed_billing import seed_billing  # noqa: E402


AUDIT_HEADERS = {
    "X-ResolveFlow-Session-Id": "sess-tool-audit-001",
    "X-ResolveFlow-Case-Id": "case-tool-audit-001",
}


VALID_SERVICE_CREDIT_CONTEXT = {
    "check_outage_status": {
        "verified": True,
        "duration_hours": 7,
    },
    "get_invoice_history": {
        "credit_this_cycle": False,
    },
}


def build_billing_db() -> Path:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-tool-audit-")) / "resolveflow.db"
    seed_billing(db_path)
    return db_path


def read_audit_row(db_path: Path, case_id: str = "case-tool-audit-001") -> dict:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT case_id, customer_id, session_id, tools_called, evidence_used, action_taken,
                   policy_dag_path, ujcs, policy_status, handoff_required
            FROM audit_logs
            WHERE case_id = ?
            """,
            (case_id,),
        ).fetchone()
    if row is None:
        raise AssertionError(f"audit row missing for {case_id}")
    return {
        "case_id": row["case_id"],
        "customer_id": row["customer_id"],
        "session_id": row["session_id"],
        "tools_called": json.loads(row["tools_called"]),
        "evidence_used": json.loads(row["evidence_used"]),
        "action_taken": json.loads(row["action_taken"]),
        "policy_dag_path": json.loads(row["policy_dag_path"]),
        "ujcs": float(row["ujcs"]) if row["ujcs"] is not None else None,
        "policy_status": row["policy_status"],
        "handoff_required": bool(row["handoff_required"]),
    }


def assert_get_tool_calls_are_appended_to_audit_log() -> None:
    db_path = build_billing_db()
    with TestClient(create_app(db_path=db_path)) as client:
        lookup = client.get("/api/tools/lookup_customer/CUST-1001", headers=AUDIT_HEADERS)
        if lookup.status_code != 200:
            raise AssertionError(f"lookup failed: {lookup.status_code} {lookup.text}")
        invoices = client.get("/api/tools/get_invoice_history/CUST-1001?months=3", headers=AUDIT_HEADERS)
        if invoices.status_code != 200:
            raise AssertionError(f"invoice history failed: {invoices.status_code} {invoices.text}")
        duplicate = client.get("/api/tools/check_duplicate_charge/CUST-1001", headers=AUDIT_HEADERS)
        if duplicate.status_code != 200:
            raise AssertionError(f"duplicate check failed: {duplicate.status_code} {duplicate.text}")

    row = read_audit_row(db_path)
    if row["customer_id"] != "CUST-1001" or row["session_id"] != "sess-tool-audit-001":
        raise AssertionError(f"audit identity wrong: {row}")
    tool_names = [entry["tool_name"] for entry in row["tools_called"]]
    if tool_names != ["lookup_customer", "get_invoice_history", "check_duplicate_charge"]:
        raise AssertionError(f"tool calls should append in order: {row}")
    if not any("same customer_id" in str(item) for item in row["evidence_used"]):
        raise AssertionError(f"duplicate-charge evidence should be appended: {row}")
    if row["policy_status"] != "pending":
        raise AssertionError(f"non-policy tool calls should keep pending status: {row}")

    with sqlite3.connect(db_path) as connection:
        conversation = connection.execute(
            "SELECT customer_id FROM conversations WHERE session_id = ?",
            ("sess-tool-audit-001",),
        ).fetchone()
    if conversation is None or conversation[0] != "CUST-1001":
        raise AssertionError("audit logger should create a conversation shell for the session")


def assert_action_tool_logging_records_actions_and_policy_path() -> None:
    db_path = build_billing_db()
    with TestClient(create_app(db_path=db_path)) as client:
        response = client.post(
            "/api/tools/apply_credit",
            headers=AUDIT_HEADERS,
            json={
                "customer_id": "CUST-1001",
                "amount": 300,
                "reason": "Verified outage credit",
                "policy_context": VALID_SERVICE_CREDIT_CONTEXT,
                "applied_to_invoice": "INV-8821",
            },
        )
    if response.status_code != 200:
        raise AssertionError(f"apply credit failed: {response.status_code} {response.text}")

    row = read_audit_row(db_path)
    if [entry["tool_name"] for entry in row["tools_called"]] != ["apply_credit"]:
        raise AssertionError(f"apply_credit should be logged: {row}")
    if row["policy_dag_path"][-1] != "auto_apply_credit":
        raise AssertionError(f"policy path missing from audit row: {row}")
    if row["ujcs"] != 0.6667 or row["policy_status"] != "compliant":
        raise AssertionError(f"policy metadata wrong: {row}")
    if row["action_taken"][0]["credit_id"] != response.json()["result"]["credit_id"]:
        raise AssertionError(f"credit action not captured: {row}")
    if "Verified outage credit" not in row["evidence_used"]:
        raise AssertionError(f"credit reason should be evidence: {row}")


def assert_audit_session_mismatch_blocks_action_before_commit() -> None:
    db_path = build_billing_db()
    headers = {
        "X-ResolveFlow-Session-Id": "sess-owned-by-1002",
        "X-ResolveFlow-Case-Id": "case-mismatch-credit",
    }
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO conversations(session_id, customer_id, messages) VALUES (?, ?, ?)",
            ("sess-owned-by-1002", "CUST-1002", "[]"),
        )

    with TestClient(create_app(db_path=db_path)) as client:
        response = client.post(
            "/api/tools/apply_credit",
            headers=headers,
            json={
                "customer_id": "CUST-1001",
                "amount": 300,
                "reason": "Verified outage credit",
                "policy_context": VALID_SERVICE_CREDIT_CONTEXT,
                "applied_to_invoice": "INV-8821",
            },
        )

    if response.status_code != 409:
        raise AssertionError(f"session mismatch should fail before action: {response.status_code} {response.text}")
    if "does not belong to customer" not in response.text:
        raise AssertionError(f"mismatch detail missing: {response.text}")

    with sqlite3.connect(db_path) as connection:
        credits = connection.execute(
            "SELECT COUNT(*) FROM credits WHERE customer_id = ?",
            ("CUST-1001",),
        ).fetchone()[0]
        audit_rows = connection.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE case_id = ?",
            ("case-mismatch-credit",),
        ).fetchone()[0]
    if credits != 0:
        raise AssertionError(f"mismatched session committed credit rows: {credits}")
    if audit_rows != 0:
        raise AssertionError(f"mismatched session should not write audit row: {audit_rows}")


def assert_policy_lookup_uses_customer_header_for_audit() -> None:
    db_path = build_billing_db()
    headers = {
        "X-ResolveFlow-Session-Id": "sess-policy-audit-001",
        "X-ResolveFlow-Case-Id": "case-policy-audit-001",
        "X-ResolveFlow-Customer-Id": "CUST-1001",
    }
    with TestClient(create_app(db_path=db_path)) as client:
        response = client.get(
            "/api/tools/retrieve_policy/service_credit_policy",
            params={"query": "verified outage credit", "top_k": 1},
            headers=headers,
        )
    if response.status_code != 200:
        raise AssertionError(f"retrieve policy failed: {response.status_code} {response.text}")
    row = read_audit_row(db_path, case_id="case-policy-audit-001")
    if row["customer_id"] != "CUST-1001":
        raise AssertionError(f"customer header was not used: {row}")
    if row["tools_called"][0]["tool_name"] != "retrieve_policy":
        raise AssertionError(f"retrieve_policy not logged: {row}")
    if "service_credit_policy" not in row["evidence_used"]:
        raise AssertionError(f"policy id evidence missing: {row}")


def assert_calls_without_audit_context_still_work_without_logging() -> None:
    db_path = build_billing_db()
    with TestClient(create_app(db_path=db_path)) as client:
        response = client.get("/api/tools/lookup_customer/CUST-1001")
    if response.status_code != 200:
        raise AssertionError(f"lookup without audit context failed: {response.status_code} {response.text}")
    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    if count != 0:
        raise AssertionError(f"calls without audit headers should not create audit rows; got {count}")


def assert_concurrent_tool_calls_preserve_all_audit_entries() -> None:
    db_path = build_billing_db()
    app = create_app(db_path=db_path)
    headers = {
        "X-ResolveFlow-Session-Id": "sess-concurrent-audit",
        "X-ResolveFlow-Case-Id": "case-concurrent-audit",
    }

    def call_lookup(_: int) -> None:
        with TestClient(app) as client:
            response = client.get("/api/tools/lookup_customer/CUST-1001", headers=headers)
        if response.status_code != 200:
            raise AssertionError(f"concurrent lookup failed: {response.status_code} {response.text}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(call_lookup, range(8)))

    row = read_audit_row(db_path, case_id="case-concurrent-audit")
    tool_names = [entry["tool_name"] for entry in row["tools_called"]]
    if len(tool_names) != 8 or any(name != "lookup_customer" for name in tool_names):
        raise AssertionError(f"concurrent audit appends were lost: {row}")


def main() -> None:
    assert_get_tool_calls_are_appended_to_audit_log()
    assert_action_tool_logging_records_actions_and_policy_path()
    assert_audit_session_mismatch_blocks_action_before_commit()
    assert_policy_lookup_uses_customer_header_for_audit()
    assert_calls_without_audit_context_still_work_without_logging()
    assert_concurrent_tool_calls_preserve_all_audit_entries()
    print("tool audit logging tests passed")


if __name__ == "__main__":
    main()
