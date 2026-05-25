from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DB_PACKAGE = ROOT / "backend" / "db"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DB_PACKAGE))

from fastapi.testclient import TestClient  # noqa: E402

from backend.api import create_app  # noqa: E402
from backend.tools import run_router_diagnostic  # noqa: E402
from seed_customers import seed_customers  # noqa: E402


TEMP_DIRS: list[tempfile.TemporaryDirectory] = []


def build_seeded_diagnostic_db() -> Path:
    tmpdir = tempfile.TemporaryDirectory()
    TEMP_DIRS.append(tmpdir)
    db_path = Path(tmpdir.name) / "resolveflow.db"
    seed_customers(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO diagnostics(customer_id, router_status, signal_strength, last_checked, recommendation)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    "CUST-1001",
                    "degraded",
                    32,
                    "2026-05-23T09:00:00",
                    "Signal is weak after reset; technician dispatch is recommended.",
                ),
                (
                    "CUST-1002",
                    "ok",
                    86,
                    "2026-05-23T09:05:00",
                    "Router health is normal.",
                ),
            ],
        )
    return db_path


def assert_detects_failed_router_diagnostic() -> None:
    db_path = build_seeded_diagnostic_db()
    result = run_router_diagnostic(
        "CUST-1001",
        db_path=db_path,
        reference_date=date(2026, 5, 23),
    )

    expected = {
        "customer_id": "CUST-1001",
        "customer_found": True,
        "diagnostic_available": True,
        "router_status": "degraded",
        "signal_strength": 32,
        "last_checked": "2026-05-23T09:00:00",
        "recommendation": "Signal is weak after reset; technician dispatch is recommended.",
        "diagnostic_failure": True,
        "needs_technician": True,
        "account_active": True,
    }
    for key, value in expected.items():
        if result.get(key) != value:
            raise AssertionError(f"wrong diagnostic field {key}: {result}")


def assert_detects_healthy_router_and_missing_diagnostic() -> None:
    db_path = build_seeded_diagnostic_db()
    healthy = run_router_diagnostic("CUST-1002", db_path=db_path, reference_date=date(2026, 5, 23))
    if healthy["diagnostic_failure"] or healthy["needs_technician"]:
        raise AssertionError(f"healthy router should not fail diagnostic: {healthy}")
    if healthy["router_status"] != "ok" or healthy["signal_strength"] != 86:
        raise AssertionError(f"healthy diagnostic fields wrong: {healthy}")

    missing_diagnostic = run_router_diagnostic("CUST-1003", db_path=db_path, reference_date=date(2026, 5, 23))
    if missing_diagnostic["customer_found"] is not True:
        raise AssertionError(f"known customer should be found: {missing_diagnostic}")
    if missing_diagnostic["diagnostic_available"] is not False:
        raise AssertionError(f"missing diagnostic should be explicit: {missing_diagnostic}")
    if missing_diagnostic["diagnostic_failure"] is not False:
        raise AssertionError(f"missing diagnostic should not be guessed as failure: {missing_diagnostic}")
    if "unavailable" not in missing_diagnostic["recommendation"].lower():
        raise AssertionError(f"missing diagnostic recommendation should explain unavailability: {missing_diagnostic}")

    unknown = run_router_diagnostic("CUST-9999", db_path=db_path, reference_date=date(2026, 5, 23))
    if unknown["customer_found"] is not False or unknown["diagnostic_available"] is not False:
        raise AssertionError(f"unknown customer flags wrong: {unknown}")


def assert_validates_inputs() -> None:
    db_path = build_seeded_diagnostic_db()
    for kwargs in (
        {"customer_id": "   ", "db_path": db_path},
        {"customer_id": "CUST-1001", "db_path": db_path, "reference_date": "   "},
    ):
        try:
            run_router_diagnostic(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad run_router_diagnostic inputs were accepted: {kwargs}")


def assert_router_diagnostic_api_endpoint() -> None:
    db_path = build_seeded_diagnostic_db()
    client = TestClient(create_app(db_path=db_path))

    response = client.get("/api/tools/run_router_diagnostic/CUST-1001")
    if response.status_code != 200:
        raise AssertionError(f"diagnostic endpoint failed: {response.status_code} {response.text}")
    payload = response.json()
    if payload["tool_name"] != "run_router_diagnostic" or payload["ok"] is not True:
        raise AssertionError(f"wrong tool envelope: {payload}")
    if payload["result"]["diagnostic_failure"] is not True:
        raise AssertionError(f"endpoint should report failed diagnostic: {payload}")


def main() -> None:
    assert_detects_failed_router_diagnostic()
    assert_detects_healthy_router_and_missing_diagnostic()
    assert_validates_inputs()
    assert_router_diagnostic_api_endpoint()
    print("router diagnostic tests passed")


if __name__ == "__main__":
    main()
