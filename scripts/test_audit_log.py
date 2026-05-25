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
from backend.tools import build_audit_log, generate_audit_log  # noqa: E402
from seed_customers import seed_customers  # noqa: E402


def build_audit_db() -> Path:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-audit-")) / "resolveflow.db"
    seed_customers(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO conversations(session_id, customer_id, messages, intents, slots, tools_called)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "sess-audit-001",
                "CUST-1001",
                json.dumps([{"role": "user", "content": "I was charged twice."}]),
                json.dumps(["duplicate_charge"]),
                json.dumps({"customer_id": "CUST-1001", "invoice_id": "INV-8821"}),
                json.dumps(["lookup_customer", "check_duplicate_charge"]),
            ),
        )
        connection.execute(
            """
            INSERT INTO conversations(session_id, customer_id, messages, intents, slots, tools_called)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "sess-audit-other",
                "CUST-1002",
                "[]",
                "[]",
                "{}",
                "[]",
            ),
        )
    return db_path


TOOLS_CALLED = [
    {"tool_name": "lookup_customer", "status": "ok"},
    {"tool_name": "check_duplicate_charge", "status": "ok"},
    {"tool_name": "create_ticket", "status": "ok", "result": {"ticket_id": "TKT-1001"}},
]
EVIDENCE_USED = ["invoice INV-8821", "payments PAY-1001-A/PAY-1001-B", "duplicate charge policy"]
ACTION_TAKEN = [{"action": "create_ticket", "ticket_id": "TKT-1001", "policy_basis": "duplicate_charge_policy"}]
POLICY_PATH = [
    "check_duplicate_confirmed",
    "check_invoice_match",
    "check_refund_window",
    "check_duplicate_amount",
    "create_refund_review_ticket",
]


def assert_builds_audit_log_from_runtime_artifacts() -> None:
    context_card = {
        "current_health_score": 26,
        "tools_called": [
            {"name": "lookup_customer", "status": "ok"},
        ],
        "evidence_used": ["context card outage evidence"],
        "actions_taken": [{"action": "human_handoff", "status": "waiting"}],
        "policy_dag_path_so_far": {
            "nodes": ["check_duplicate_confirmed", "create_refund_review_ticket"],
            "policy_status": "needs_review",
            "ujcs": 0.66,
        },
        "audit": {
            "policy_status": "needs_review",
            "ujcs": 0.66,
            "health_score": 26,
            "handoff_required": True,
        },
        "handoff_queue": {"handoff_id": "HND-1001", "status": "waiting"},
    }
    tool_results = [
        {
            "tool_name": "check_duplicate_charge",
            "status": "ok",
            "args": {"customer_id": "CUST-1001"},
            "result": {
                "duplicate_confirmed": True,
                "evidence": ["same customer_id", "same amount"],
                "action_taken": [{"action": "create_ticket", "ticket_id": "TKT-1001"}],
            },
        },
        {
            "tool_name": "retrieve_policy",
            "status": "ok",
            "result": {"policy_id": "duplicate_charge_policy"},
        },
    ]

    result = build_audit_log(
        " case-built-001 ",
        customer_id=" CUST-1001 ",
        session_id=" sess-audit-001 ",
        tools_called=[{"tool_name": "lookup_customer", "status": "ok"}],
        evidence_used=["invoice INV-8821"],
        action_taken=[{"action": "acknowledge_duplicate_charge"}],
        tool_results=tool_results,
        context_card=context_card,
    )

    if result["case_id"] != "case-built-001":
        raise AssertionError(f"case id should be trimmed: {result}")
    if result["customer_id"] != "CUST-1001" or result["session_id"] != "sess-audit-001":
        raise AssertionError(f"identity should be normalized: {result}")
    tool_names = [
        item.get("tool_name") or item.get("name")
        for item in result["tools_called"]
        if isinstance(item, dict)
    ]
    if tool_names != ["lookup_customer", "check_duplicate_charge", "retrieve_policy"]:
        raise AssertionError(f"tool assembly wrong: {result['tools_called']}")
    for evidence in ("invoice INV-8821", "same customer_id", "same amount", "context card outage evidence"):
        if evidence not in result["evidence_used"]:
            raise AssertionError(f"evidence {evidence!r} missing: {result}")
    if not any(isinstance(action, dict) and action.get("ticket_id") == "TKT-1001" for action in result["action_taken"]):
        raise AssertionError(f"tool action missing: {result['action_taken']}")
    if result["policy_dag_path"] != ["check_duplicate_confirmed", "create_refund_review_ticket"]:
        raise AssertionError(f"policy path should come from context card: {result}")
    if result["ujcs"] != 0.66 or result["policy_status"] != "needs_review":
        raise AssertionError(f"policy metadata missing: {result}")
    if result["health_score"] != 26.0 or result["handoff_required"] is not True:
        raise AssertionError(f"health/handoff metadata missing: {result}")
    if result["raw_json"]["tools_called"] != result["tools_called"]:
        raise AssertionError(f"raw json should preserve assembled tools: {result}")
    if "check_duplicate_charge" not in result["human_summary"]:
        raise AssertionError(f"human summary should include assembled tools: {result['human_summary']}")


def assert_build_audit_log_respects_explicit_policy_path_and_status() -> None:
    result = build_audit_log(
        "case-explicit-build",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=[],
        evidence_used=[],
        action_taken=[],
        policy_dag_path=["explicit_start", "explicit_end"],
        policy_result={
            "path": ["policy_start"],
            "ujcs": 0.95,
            "policy_status": "compliant",
        },
    )
    if result["policy_dag_path"] != ["explicit_start", "explicit_end"]:
        raise AssertionError(f"explicit policy path should win: {result}")
    if result["ujcs"] != 0.95 or result["policy_status"] != "compliant":
        raise AssertionError(f"policy result metadata missing: {result}")


def assert_build_audit_log_computes_ujcs_from_policy_dag_path() -> None:
    result = build_audit_log(
        "case-computed-ujcs",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=[],
        evidence_used=[],
        action_taken=[],
        policy_dag_path=[
            "check_outage_verified",
            "check_outage_duration",
            "check_prior_credit",
            "auto_apply_credit",
        ],
        policy_name="service_credit_dag",
    )
    if result["ujcs"] != round(4 / 6, 4):
        raise AssertionError(f"UJCS should be computed from service_credit_dag path: {result}")
    if result["policy_status"] != "needs_review":
        raise AssertionError(f"computed UJCS below compliance threshold should need review: {result}")
    if "UJCS 0.6667" not in result["human_summary"]:
        raise AssertionError(f"human summary should show computed UJCS: {result}")


def assert_policy_status_is_compliant_only_above_point_eight() -> None:
    below = build_audit_log(
        "case-ujcs-below",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=[],
        evidence_used=[],
        action_taken=[],
        policy_dag_path=[],
        ujcs=0.8,
    )
    above = build_audit_log(
        "case-ujcs-above",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=[],
        evidence_used=[],
        action_taken=[],
        policy_dag_path=[],
        ujcs=0.8001,
    )
    zero = build_audit_log(
        "case-ujcs-zero",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=[],
        evidence_used=[],
        action_taken=[],
        policy_dag_path=[],
        ujcs=0,
    )
    missing = build_audit_log(
        "case-ujcs-missing",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=[],
        evidence_used=[],
        action_taken=[],
        policy_dag_path=[],
    )

    if below["policy_status"] != "needs_review":
        raise AssertionError(f"UJCS exactly 0.8 should not be compliant: {below}")
    if above["policy_status"] != "compliant":
        raise AssertionError(f"UJCS above 0.8 should be compliant: {above}")
    if zero["policy_status"] != "non_compliant":
        raise AssertionError(f"UJCS zero should be non_compliant: {zero}")
    if missing["policy_status"] != "pending":
        raise AssertionError(f"missing UJCS should be pending: {missing}")


def assert_generates_and_persists_audit_log() -> None:
    db_path = build_audit_db()
    result = generate_audit_log(
        "case-audit-001",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=TOOLS_CALLED,
        evidence_used=EVIDENCE_USED,
        action_taken=ACTION_TAKEN,
        policy_dag_path=POLICY_PATH,
        ujcs=0.92,
        health_score=78,
        handoff_required=False,
        db_path=db_path,
    )

    if result["case_id"] != "case-audit-001" or result["inserted"] is not True:
        raise AssertionError(f"wrong audit result identity: {result}")
    if result["policy_status"] != "compliant":
        raise AssertionError(f"UJCS > 0.8 should default to compliant: {result}")
    if result["ujcs"] != 0.92 or result["health_score"] != 78.0:
        raise AssertionError(f"numeric fields wrong: {result}")
    if result["handoff_required"] is not False:
        raise AssertionError(f"handoff flag wrong: {result}")
    if "UJCS 0.9200" not in result["human_summary"]:
        raise AssertionError(f"human summary should include UJCS: {result}")
    if result["raw_json"]["tools_called"][1]["tool_name"] != "check_duplicate_charge":
        raise AssertionError(f"raw json should preserve tool payloads: {result}")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT case_id, customer_id, session_id, tools_called, evidence_used, action_taken,
                   policy_dag_path, ujcs, policy_status, health_score, handoff_required
            FROM audit_logs
            WHERE case_id = ?
            """,
            ("case-audit-001",),
        ).fetchone()

    if row is None:
        raise AssertionError("audit log was not inserted")
    if row["customer_id"] != "CUST-1001" or row["session_id"] != "sess-audit-001":
        raise AssertionError(f"stored identity wrong: {dict(row)}")
    if json.loads(row["evidence_used"]) != EVIDENCE_USED:
        raise AssertionError(f"stored evidence wrong: {dict(row)}")
    if row["policy_status"] != "compliant" or float(row["ujcs"]) != 0.92:
        raise AssertionError(f"stored compliance wrong: {dict(row)}")


def assert_persists_assembled_proof_trail_to_audit_logs_table() -> None:
    db_path = build_audit_db()
    draft = build_audit_log(
        "case-persisted-proof",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=[
            {"tool_name": "lookup_customer", "status": "ok", "customer_id": "CUST-1001"},
            {"tool_name": "check_duplicate_charge", "status": "ok", "invoice_id": "INV-8821"},
        ],
        evidence_used=[
            {"source": "invoice", "id": "INV-8821", "finding": "same invoice paid twice"},
            {"source": "payment", "ids": ["PAY-1001-A", "PAY-1001-B"], "amount": 1299.0},
        ],
        action_taken=[
            {"action": "create_ticket", "ticket_id": "TKT-PROOF-001", "reason": "duplicate charge review"},
        ],
        policy_dag_path=[
            "check_duplicate_confirmed",
            "check_invoice_match",
            "check_refund_window",
            "check_duplicate_amount",
            "create_refund_review_ticket",
        ],
        ujcs=0.91,
        health_score=73,
        handoff_required=True,
    )

    result = generate_audit_log(
        draft["case_id"],
        customer_id=draft["customer_id"],
        session_id=draft["session_id"],
        tools_called=draft["tools_called"],
        evidence_used=draft["evidence_used"],
        action_taken=draft["action_taken"],
        policy_dag_path=draft["policy_dag_path"],
        ujcs=draft["ujcs"],
        policy_status=draft["policy_status"],
        health_score=draft["health_score"],
        handoff_required=draft["handoff_required"],
        db_path=db_path,
    )

    if result["inserted"] is not True:
        raise AssertionError(f"proof trail should insert first audit row: {result}")
    if result["raw_json"]["tools_called"] != draft["tools_called"]:
        raise AssertionError(f"returned raw json should match draft tools: {result}")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT case_id, customer_id, session_id, tools_called, evidence_used, action_taken,
                   policy_dag_path, ujcs, policy_status, health_score, handoff_required
            FROM audit_logs
            WHERE case_id = ?
            """,
            (draft["case_id"],),
        ).fetchone()

    if row is None:
        raise AssertionError("assembled proof trail was not persisted to audit_logs")

    stored = {
        "case_id": row["case_id"],
        "customer_id": row["customer_id"],
        "session_id": row["session_id"],
        "tools_called": json.loads(row["tools_called"]),
        "evidence_used": json.loads(row["evidence_used"]),
        "action_taken": json.loads(row["action_taken"]),
        "policy_dag_path": json.loads(row["policy_dag_path"]),
        "ujcs": float(row["ujcs"]) if row["ujcs"] is not None else None,
        "policy_status": row["policy_status"],
        "health_score": float(row["health_score"]) if row["health_score"] is not None else None,
        "handoff_required": bool(row["handoff_required"]),
    }
    expected = {
        "case_id": draft["case_id"],
        "customer_id": draft["customer_id"],
        "session_id": draft["session_id"],
        "tools_called": draft["tools_called"],
        "evidence_used": draft["evidence_used"],
        "action_taken": draft["action_taken"],
        "policy_dag_path": draft["policy_dag_path"],
        "ujcs": draft["ujcs"],
        "policy_status": draft["policy_status"],
        "health_score": draft["health_score"],
        "handoff_required": draft["handoff_required"],
    }
    if stored != expected:
        raise AssertionError(f"persisted audit row does not match assembled draft: {stored}")


def assert_generates_and_persists_computed_ujcs() -> None:
    db_path = build_audit_db()
    result = generate_audit_log(
        "case-computed-ujcs",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=[],
        evidence_used=["verified outage path"],
        action_taken=[{"action": "apply_credit"}],
        policy_dag_path=[
            "check_outage_verified",
            "check_outage_duration",
            "check_prior_credit",
            "auto_apply_credit",
        ],
        policy_name="service_credit_dag",
        db_path=db_path,
    )
    if result["ujcs"] != round(4 / 6, 4):
        raise AssertionError(f"persisted audit should use computed UJCS: {result}")
    if result["policy_status"] != "needs_review":
        raise AssertionError(f"computed UJCS should drive status: {result}")

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT ujcs, policy_status FROM audit_logs WHERE case_id = ?",
            ("case-computed-ujcs",),
        ).fetchone()
    if row is None or float(row[0]) != round(4 / 6, 4) or row[1] != "needs_review":
        raise AssertionError(f"computed UJCS was not stored: {row}")


def assert_persists_compliant_status_when_ujcs_exceeds_point_eight() -> None:
    db_path = build_audit_db()
    result = generate_audit_log(
        "case-compliant-threshold",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=[],
        evidence_used=[],
        action_taken=[],
        policy_dag_path=[],
        ujcs=0.8001,
        db_path=db_path,
    )
    if result["policy_status"] != "compliant":
        raise AssertionError(f"persisted status should be compliant above threshold: {result}")
    with sqlite3.connect(db_path) as connection:
        stored_status = connection.execute(
            "SELECT policy_status FROM audit_logs WHERE case_id = ?",
            ("case-compliant-threshold",),
        ).fetchone()[0]
    if stored_status != "compliant":
        raise AssertionError(f"stored policy_status should be compliant: {stored_status}")


def assert_updates_existing_case_idempotently() -> None:
    db_path = build_audit_db()
    first = generate_audit_log(
        "case-audit-001",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=["lookup_customer"],
        evidence_used=["initial evidence"],
        action_taken=[],
        policy_dag_path=[],
        ujcs=0.5,
        db_path=db_path,
    )
    second = generate_audit_log(
        "case-audit-001",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=TOOLS_CALLED,
        evidence_used=EVIDENCE_USED,
        action_taken=ACTION_TAKEN,
        policy_dag_path=POLICY_PATH,
        ujcs=0.5,
        handoff_required=True,
        db_path=db_path,
    )
    if first["inserted"] is not True or second["inserted"] is not False:
        raise AssertionError(f"insert/update flags wrong: {first} {second}")
    if second["policy_status"] != "needs_review":
        raise AssertionError(f"UJCS <= 0.8 should need review: {second}")
    if second["handoff_required"] is not True:
        raise AssertionError(f"updated handoff flag missing: {second}")

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM audit_logs WHERE case_id = ?", ("case-audit-001",)).fetchone()[0]
    if count != 1:
        raise AssertionError(f"upsert should keep one row, got {count}")


def assert_explicit_policy_status_and_pending_defaults() -> None:
    db_path = build_audit_db()
    pending = generate_audit_log(
        "case-audit-pending",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=[],
        evidence_used=[],
        action_taken=[],
        policy_dag_path=[],
        db_path=db_path,
    )
    if pending["policy_status"] != "pending" or pending["ujcs"] is not None:
        raise AssertionError(f"missing UJCS should be pending: {pending}")

    explicit = generate_audit_log(
        "case-audit-explicit",
        customer_id="CUST-1001",
        session_id="sess-audit-001",
        tools_called=[],
        evidence_used=[],
        action_taken=[],
        policy_dag_path=[],
        ujcs=0,
        policy_status="non_compliant",
        db_path=db_path,
    )
    if explicit["policy_status"] != "non_compliant":
        raise AssertionError(f"explicit status should be preserved: {explicit}")


def assert_validates_inputs_and_references() -> None:
    db_path = build_audit_db()
    bad_calls = (
        {"case_id": "   ", "customer_id": "CUST-1001", "session_id": "sess-audit-001"},
        {"case_id": "case", "customer_id": "   ", "session_id": "sess-audit-001"},
        {"case_id": "case", "customer_id": "CUST-1001", "session_id": "   "},
        {"case_id": "case", "customer_id": "CUST-9999", "session_id": "sess-audit-001"},
        {"case_id": "case", "customer_id": "CUST-1001", "session_id": "missing-session"},
        {"case_id": "case", "customer_id": "CUST-1001", "session_id": "sess-audit-other"},
        {"case_id": "case", "customer_id": "CUST-1001", "session_id": "sess-audit-001", "tools_called": "lookup_customer"},
        {"case_id": "case", "customer_id": "CUST-1001", "session_id": "sess-audit-001", "ujcs": 1.1},
        {"case_id": "case", "customer_id": "CUST-1001", "session_id": "sess-audit-001", "health_score": -1},
        {"case_id": "case", "customer_id": "CUST-1001", "session_id": "sess-audit-001", "policy_status": "partial"},
    )
    for kwargs in bad_calls:
        payload = {
            "tools_called": [],
            "evidence_used": [],
            "action_taken": [],
            "policy_dag_path": [],
            "db_path": db_path,
        }
        payload.update(kwargs)
        try:
            generate_audit_log(**payload)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad generate_audit_log inputs were accepted: {kwargs}")

    bad_build_calls = (
        {"case_id": "", "customer_id": "CUST-1001", "session_id": "sess-audit-001"},
        {"case_id": "case", "customer_id": "", "session_id": "sess-audit-001"},
        {"case_id": "case", "customer_id": "CUST-1001", "session_id": ""},
        {"case_id": "case", "customer_id": "CUST-1001", "session_id": "sess-audit-001", "tools_called": "lookup_customer"},
        {"case_id": "case", "customer_id": "CUST-1001", "session_id": "sess-audit-001", "context_card": []},
        {"case_id": "case", "customer_id": "CUST-1001", "session_id": "sess-audit-001", "policy_status": "partial"},
    )
    for kwargs in bad_build_calls:
        payload = {
            "tools_called": [],
            "evidence_used": [],
            "action_taken": [],
            "policy_dag_path": [],
        }
        payload.update(kwargs)
        try:
            build_audit_log(**payload)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad build_audit_log inputs were accepted: {kwargs}")


def assert_generate_audit_log_api_endpoint() -> None:
    db_path = build_audit_db()
    with TestClient(create_app(db_path=db_path)) as client:
        response = client.post(
            "/api/tools/generate_audit_log",
            json={
                "case_id": "case-api-001",
                "customer_id": "CUST-1001",
                "session_id": "sess-audit-001",
                "tools_called": TOOLS_CALLED,
                "evidence_used": EVIDENCE_USED,
                "action_taken": ACTION_TAKEN,
                "policy_dag_path": POLICY_PATH,
                "policy_name": "duplicate_charge_refund_dag",
                "ujcs": 0.92,
                "health_score": 78,
                "handoff_required": False,
            },
        )
        if response.status_code != 200:
            raise AssertionError(f"audit endpoint failed: {response.status_code} {response.text}")
        payload = response.json()
        if payload["tool_name"] != "generate_audit_log" or payload["ok"] is not True:
            raise AssertionError(f"wrong tool envelope: {payload}")
        if payload["result"]["policy_status"] != "compliant":
            raise AssertionError(f"endpoint policy status wrong: {payload}")

        computed = client.post(
            "/api/tools/generate_audit_log",
            json={
                "case_id": "case-api-computed-ujcs",
                "customer_id": "CUST-1001",
                "session_id": "sess-audit-001",
                "tools_called": [],
                "evidence_used": ["computed from policy path"],
                "action_taken": [],
                "policy_dag_path": [
                    "check_outage_verified",
                    "check_outage_duration",
                    "check_prior_credit",
                    "auto_apply_credit",
                ],
                "policy_name": "service_credit_dag",
            },
        )
        if computed.status_code != 200:
            raise AssertionError(f"computed UJCS endpoint failed: {computed.status_code} {computed.text}")
        computed_payload = computed.json()
        if computed_payload["result"]["ujcs"] != round(4 / 6, 4):
            raise AssertionError(f"endpoint should compute UJCS: {computed_payload}")

        invalid = client.post(
            "/api/tools/generate_audit_log",
            json={
                "case_id": "case-api-bad",
                "customer_id": "CUST-1001",
                "session_id": "missing-session",
                "tools_called": [],
                "evidence_used": [],
                "action_taken": [],
                "policy_dag_path": [],
            },
        )
        if invalid.status_code != 422:
            raise AssertionError(f"missing session should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_builds_audit_log_from_runtime_artifacts()
    assert_build_audit_log_respects_explicit_policy_path_and_status()
    assert_build_audit_log_computes_ujcs_from_policy_dag_path()
    assert_policy_status_is_compliant_only_above_point_eight()
    assert_generates_and_persists_audit_log()
    assert_persists_assembled_proof_trail_to_audit_logs_table()
    assert_generates_and_persists_computed_ujcs()
    assert_persists_compliant_status_when_ujcs_exceeds_point_eight()
    assert_updates_existing_case_idempotently()
    assert_explicit_policy_status_and_pending_defaults()
    assert_validates_inputs_and_references()
    assert_generate_audit_log_api_endpoint()
    print("audit log tests passed")


if __name__ == "__main__":
    main()
