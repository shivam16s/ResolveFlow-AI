from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DB_PACKAGE = ROOT / "backend" / "db"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DB_PACKAGE))

from fastapi.testclient import TestClient  # noqa: E402

from backend.api import create_app  # noqa: E402
from backend.tools import check_outage_status  # noqa: E402
from seed_outages import seed_outages  # noqa: E402


TEMP_DIRS: list[tempfile.TemporaryDirectory] = []


def build_seeded_outage_db() -> Path:
    tmpdir = tempfile.TemporaryDirectory()
    TEMP_DIRS.append(tmpdir)
    db_path = Path(tmpdir.name) / "resolveflow.db"
    seed_outages(db_path)
    return db_path


def assert_detects_verified_customer_outage() -> None:
    db_path = build_seeded_outage_db()
    status = check_outage_status(
        "Chennai Zone-04",
        customer_id="CUST-1001",
        db_path=db_path,
        reference_date=date(2026, 5, 23),
    )

    expected = {
        "location": "Chennai Zone-04",
        "customer_id": "CUST-1001",
        "has_outage_record": True,
        "verified": True,
        "outage_id": "OUT-CHN-04-20260520",
        "start_time": "2026-05-20T08:00:00",
        "end_time": "2026-05-20T15:00:00",
        "duration_hours": 7.0,
        "customer_affected": True,
        "outage_cleared": True,
        "affected_area": "Chennai Zone-04",
    }
    for key, value in expected.items():
        if status.get(key) != value:
            raise AssertionError(f"wrong outage field {key}: {status}")
    if status["affected_customers"] != ["CUST-1001", "CUST-1020"]:
        raise AssertionError(f"affected customers wrong: {status}")


def assert_detects_unverified_open_outage_and_customer_impact() -> None:
    db_path = build_seeded_outage_db()
    status = check_outage_status(
        "Kochi Central-03",
        customer_id="CUST-9999",
        db_path=db_path,
        reference_date=date(2026, 5, 23),
    )
    if status["verified"] is not False or status["outage_cleared"] is not False:
        raise AssertionError(f"open unverified outage flags wrong: {status}")
    if status["duration_hours"] is not None or status["end_time"] is not None:
        raise AssertionError(f"open outage duration/end should be None: {status}")
    if status["customer_affected"] is not False:
        raise AssertionError(f"unlisted customer should not be affected: {status}")


def assert_handles_missing_location_and_bad_inputs() -> None:
    db_path = build_seeded_outage_db()
    missing = check_outage_status(
        "Unknown Zone",
        customer_id="CUST-1001",
        db_path=db_path,
        reference_date=date(2026, 5, 23),
    )
    if missing["has_outage_record"] is not False or missing["verified"] is not False:
        raise AssertionError(f"missing location should return no outage: {missing}")
    if missing["customer_affected"] is not False or missing["outage_cleared"] is not True:
        raise AssertionError(f"missing location flags wrong: {missing}")

    for kwargs in (
        {"location": "   ", "db_path": db_path},
        {"location": "Chennai Zone-04", "customer_id": "   ", "db_path": db_path},
        {"location": "Chennai Zone-04", "reference_date": "   ", "db_path": db_path},
    ):
        try:
            check_outage_status(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad check_outage_status inputs were accepted: {kwargs}")


def assert_outage_status_api_endpoint() -> None:
    db_path = build_seeded_outage_db()
    client = TestClient(create_app(db_path=db_path))

    response = client.get("/api/tools/check_outage_status?location=Chennai%20Zone-04&customer_id=CUST-1001")
    if response.status_code != 200:
        raise AssertionError(f"outage endpoint failed: {response.status_code} {response.text}")
    payload = response.json()
    if payload["tool_name"] != "check_outage_status" or payload["ok"] is not True:
        raise AssertionError(f"wrong tool envelope: {payload}")
    if payload["result"]["outage_id"] != "OUT-CHN-04-20260520":
        raise AssertionError(f"wrong endpoint outage payload: {payload}")

    invalid = client.get("/api/tools/check_outage_status?location=")
    if invalid.status_code != 422:
        raise AssertionError(f"empty location should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_detects_verified_customer_outage()
    assert_detects_unverified_open_outage_and_customer_impact()
    assert_handles_missing_location_and_bad_inputs()
    assert_outage_status_api_endpoint()
    print("outage status tests passed")


if __name__ == "__main__":
    main()
