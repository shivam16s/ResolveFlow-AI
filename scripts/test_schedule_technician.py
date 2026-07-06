from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PACKAGE = ROOT / "backend" / "db"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DB_PACKAGE))

from fastapi.testclient import TestClient  # noqa: E402

from backend.agent.policy_graph import PolicyActionBlocked  # noqa: E402
from backend.api import create_app  # noqa: E402
from backend.tools import schedule_technician  # noqa: E402
from seed_customers import seed_customers  # noqa: E402


TEMP_DIRS: list[tempfile.TemporaryDirectory] = []


VALID_DISPATCH_CONTEXT = {
    "lookup_customer": {
        "account_active": True,
    },
    "run_router_diagnostic": {
        "diagnostic_failure": True,
    },
    "check_outage_status": {
        "outage_cleared": True,
    },
    "appointment_slot_selected": True,
}


def build_seeded_customer_db() -> Path:
    tmpdir = tempfile.TemporaryDirectory()
    TEMP_DIRS.append(tmpdir)
    db_path = Path(tmpdir.name) / "resolveflow.db"
    seed_customers(db_path)
    return db_path


def assert_schedules_technician_and_creates_ticket() -> None:
    db_path = build_seeded_customer_db()
    result = schedule_technician(
        "CUST-1001",
        "2026-08-24 10:00-13:00",
        policy_context=VALID_DISPATCH_CONTEXT,
        db_path=db_path,
    )

    if not result["appointment_id"].startswith("APT-"):
        raise AssertionError(f"appointment id not generated: {result}")
    if not result["ticket_id"].startswith("TKT-") or result["ticket_created"] is not True:
        raise AssertionError(f"ticket should be created: {result}")
    if result["customer_id"] != "CUST-1001" or result["slot_confirmed"] is not True:
        raise AssertionError(f"wrong appointment payload: {result}")
    if result["time_slot"] != "2026-08-24 10:00-13:00":
        raise AssertionError(f"time slot should be normalized/preserved: {result}")
    if not result["technician_name"]:
        raise AssertionError(f"technician name missing: {result}")
    if result["policy_name"] != "technician_dispatch_dag" or result["policy_action"] != "schedule_technician":
        raise AssertionError(f"policy metadata missing: {result}")
    if result["policy_action_args"].get("requires_ticket") is not True:
        raise AssertionError(f"ticket requirement missing: {result}")
    if result["policy_path"] != [
        "check_account_active",
        "check_router_diagnostic",
        "check_outage_cleared",
        "check_appointment_slot",
        "schedule_technician_visit",
    ]:
        raise AssertionError(f"wrong policy path: {result}")
    if result["ujcs"] != round(5 / 6, 4) or result["policy_status"] != "compliant":
        raise AssertionError(f"wrong policy status: {result}")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT ticket_id, customer_id, issue_type, status, priority,
                   appointment_id, appointment_slot, technician_name, scheduled_at
            FROM tickets
            WHERE ticket_id = ?
            """,
            (result["ticket_id"],),
        ).fetchone()

    if row is None:
        raise AssertionError("technician ticket was not inserted")
    expected = {
        "customer_id": "CUST-1001",
        "issue_type": "technician_dispatch",
        "status": "in_progress",
        "priority": "high",
        "appointment_id": result["appointment_id"],
        "appointment_slot": "2026-08-24 10:00-13:00",
        "technician_name": result["technician_name"],
        "scheduled_at": result["scheduled_at"],
    }
    for key, value in expected.items():
        if row[key] != value:
            raise AssertionError(f"stored ticket field {key} wrong: {dict(row)}")


def assert_schedules_against_existing_ticket() -> None:
    db_path = build_seeded_customer_db()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO tickets(ticket_id, customer_id, issue_type, status, priority)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("TKT-EXISTING-001", "CUST-1001", "technician_dispatch", "open", "medium"),
        )

    result = schedule_technician(
        "CUST-1001",
        "2026-08-24 15:00-18:00",
        policy_context=VALID_DISPATCH_CONTEXT,
        ticket_id="TKT-EXISTING-001",
        db_path=db_path,
    )
    if result["ticket_id"] != "TKT-EXISTING-001" or result["ticket_created"] is not False:
        raise AssertionError(f"existing ticket should be linked: {result}")
    if result["appointment_id"] != "APT-EXISTING-001":
        raise AssertionError(f"appointment id should derive from linked ticket: {result}")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT status, priority, appointment_id, appointment_slot, technician_name FROM tickets WHERE ticket_id = ?",
            ("TKT-EXISTING-001",),
        ).fetchone()
    if row["status"] != "in_progress" or row["priority"] != "high":
        raise AssertionError(f"linked ticket should be moved to dispatch state: {dict(row)}")
    if row["appointment_id"] != "APT-EXISTING-001" or row["appointment_slot"] != "2026-08-24 15:00-18:00":
        raise AssertionError(f"linked ticket should persist appointment fields: {dict(row)}")
    if row["technician_name"] != result["technician_name"]:
        raise AssertionError(f"linked ticket should persist assigned technician: {dict(row)}")


def assert_blocks_when_policy_prerequisites_fail() -> None:
    db_path = build_seeded_customer_db()
    blocked_context = {
        "lookup_customer": {"account_active": True},
        "run_router_diagnostic": {"diagnostic_failure": True},
        "check_outage_status": {"outage_cleared": False},
        "appointment_slot_selected": True,
    }
    try:
        schedule_technician(
            "CUST-1001",
            "2026-08-24 10:00-13:00",
            policy_context=blocked_context,
            db_path=db_path,
        )
    except PolicyActionBlocked as exc:
        if "handoff_human" not in str(exc):
            raise AssertionError(f"blocked reason should include reached action: {exc}") from exc
    else:
        raise AssertionError("active outage should block technician scheduling")

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    if count != 0:
        raise AssertionError(f"blocked schedule should not create ticket; got {count}")


def assert_blocks_without_selected_slot() -> None:
    db_path = build_seeded_customer_db()
    blocked_context = dict(VALID_DISPATCH_CONTEXT)
    blocked_context["appointment_slot_selected"] = False
    try:
        schedule_technician(
            "CUST-1001",
            "2026-08-24 10:00-13:00",
            policy_context=blocked_context,
            db_path=db_path,
        )
    except PolicyActionBlocked:
        pass
    else:
        raise AssertionError("missing selected slot should block scheduling")


def assert_validates_inputs_and_ticket_ownership() -> None:
    db_path = build_seeded_customer_db()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO tickets(ticket_id, customer_id, issue_type, status, priority)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("TKT-OTHER-001", "CUST-1002", "technician_dispatch", "open", "medium"),
        )

    bad_calls = (
        {"customer_id": "   ", "time_slot": "2026-08-24 10:00-13:00", "policy_context": VALID_DISPATCH_CONTEXT},
        {"customer_id": "CUST-1001", "time_slot": "   ", "policy_context": VALID_DISPATCH_CONTEXT},
        {"customer_id": "CUST-1001", "time_slot": "tomorrow morning", "policy_context": VALID_DISPATCH_CONTEXT},
        {"customer_id": "CUST-1001", "time_slot": "2020-01-01 10:00-13:00", "policy_context": VALID_DISPATCH_CONTEXT},
        {"customer_id": "CUST-1001", "time_slot": "2026-08-24 13:00-10:00", "policy_context": VALID_DISPATCH_CONTEXT},
        {"customer_id": "CUST-1001", "time_slot": "2026-08-24 09:00-18:00", "policy_context": VALID_DISPATCH_CONTEXT},
        {"customer_id": "CUST-1001", "time_slot": "2026-08-24 10:00-13:00", "policy_context": []},
        {
            "customer_id": "CUST-1001",
            "time_slot": "2026-08-24 10:00-13:00",
            "policy_context": VALID_DISPATCH_CONTEXT,
            "policy_name": "   ",
        },
        {
            "customer_id": "CUST-1001",
            "time_slot": "2026-08-24 10:00-13:00",
            "policy_context": VALID_DISPATCH_CONTEXT,
            "ticket_id": "   ",
        },
        {
            "customer_id": "CUST-9999",
            "time_slot": "2026-08-24 10:00-13:00",
            "policy_context": VALID_DISPATCH_CONTEXT,
        },
        {
            "customer_id": "CUST-1001",
            "time_slot": "2026-08-24 10:00-13:00",
            "policy_context": VALID_DISPATCH_CONTEXT,
            "ticket_id": "TKT-MISSING-001",
        },
        {
            "customer_id": "CUST-1001",
            "time_slot": "2026-08-24 10:00-13:00",
            "policy_context": VALID_DISPATCH_CONTEXT,
            "ticket_id": "TKT-OTHER-001",
        },
    )
    for kwargs in bad_calls:
        try:
            schedule_technician(**kwargs, db_path=db_path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad schedule_technician inputs were accepted: {kwargs}")


def assert_schedule_technician_api_endpoint() -> None:
    db_path = build_seeded_customer_db()
    client = TestClient(create_app(db_path=db_path))

    response = client.post(
        "/api/tools/schedule_technician",
        json={
            "customer_id": "CUST-1001",
            "time_slot": "2026-08-24 10:00-13:00",
            "policy_context": VALID_DISPATCH_CONTEXT,
        },
    )
    if response.status_code != 200:
        raise AssertionError(f"schedule technician endpoint failed: {response.status_code} {response.text}")
    payload = response.json()
    if payload["tool_name"] != "schedule_technician" or payload["ok"] is not True:
        raise AssertionError(f"wrong tool envelope: {payload}")
    if payload["result"]["policy_path"][-1] != "schedule_technician_visit":
        raise AssertionError(f"endpoint policy path wrong: {payload}")

    blocked = client.post(
        "/api/tools/schedule_technician",
        json={
            "customer_id": "CUST-1001",
            "time_slot": "2026-08-24 10:00-13:00",
            "policy_context": {
                "lookup_customer": {"account_active": True},
                "run_router_diagnostic": {"diagnostic_failure": False},
                "check_outage_status": {"outage_cleared": True},
                "appointment_slot_selected": True,
            },
        },
    )
    if blocked.status_code != 409:
        raise AssertionError(f"blocked dispatch should return 409: {blocked.status_code} {blocked.text}")

    invalid = client.post(
        "/api/tools/schedule_technician",
        json={
            "customer_id": "CUST-1001",
            "time_slot": "",
            "policy_context": VALID_DISPATCH_CONTEXT,
        },
    )
    if invalid.status_code != 422:
        raise AssertionError(f"empty slot should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_schedules_technician_and_creates_ticket()
    assert_schedules_against_existing_ticket()
    assert_blocks_when_policy_prerequisites_fail()
    assert_blocks_without_selected_slot()
    assert_validates_inputs_and_ticket_ownership()
    assert_schedule_technician_api_endpoint()
    print("schedule technician tests passed")


if __name__ == "__main__":
    main()
