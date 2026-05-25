from __future__ import annotations

from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DB_PACKAGE = ROOT / "backend" / "db"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DB_PACKAGE))

from fastapi.testclient import TestClient  # noqa: E402

from backend.api import create_app  # noqa: E402
from backend.tools import lookup_customer  # noqa: E402
from seed_customers import seed_customers  # noqa: E402


TEMP_DIRS: list[tempfile.TemporaryDirectory] = []


def build_seeded_db() -> Path:
    tmpdir = tempfile.TemporaryDirectory()
    TEMP_DIRS.append(tmpdir)
    db_path = Path(tmpdir.name) / "resolveflow.db"
    seed_customers(db_path)
    return db_path


def assert_lookup_customer_returns_real_profile() -> None:
    db_path = build_seeded_db()
    profile = lookup_customer("CUST-1001", db_path=db_path)

    if profile is None:
        raise AssertionError("expected seeded customer profile")
    expected = {
        "customer_id": "CUST-1001",
        "name": "Rahul Sharma",
        "location": "Chennai Zone-04",
        "plan_id": "fiber_plus_200",
        "plan_name": "Fiber Plus 200",
        "risk_level": "high",
        "preferred_language": "en",
        "account_status": "active",
        "identity_verified": True,
        "account_active": True,
    }
    for key, value in expected.items():
        if profile.get(key) != value:
            raise AssertionError(f"wrong profile field {key}: {profile}")
    if profile["monthly_price"] != 1199.0 or profile["speed_mbps"] != 200:
        raise AssertionError(f"plan details missing from profile: {profile}")


def assert_lookup_customer_handles_suspended_and_missing_customers() -> None:
    db_path = build_seeded_db()
    suspended = lookup_customer("CUST-1014", db_path=db_path)
    if suspended is None:
        raise AssertionError("expected suspended seeded customer")
    if suspended["account_status"] != "suspended" or suspended["account_active"] is not False:
        raise AssertionError(f"suspended account flags wrong: {suspended}")

    if lookup_customer("CUST-9999", db_path=db_path) is not None:
        raise AssertionError("unknown customer should return None")

    try:
        lookup_customer("   ", db_path=db_path)
    except ValueError as exc:
        if "customer_id must not be empty" not in str(exc):
            raise AssertionError(f"wrong empty-id error: {exc}")
    else:
        raise AssertionError("empty customer_id was accepted")


def assert_lookup_customer_api_endpoint() -> None:
    db_path = build_seeded_db()
    client = TestClient(create_app(db_path=db_path))

    response = client.get("/api/tools/lookup_customer/CUST-1001")
    if response.status_code != 200:
        raise AssertionError(f"lookup endpoint failed: {response.status_code} {response.text}")
    payload = response.json()
    if payload["tool_name"] != "lookup_customer" or payload["ok"] is not True:
        raise AssertionError(f"wrong tool envelope: {payload}")
    if payload["result"]["customer_id"] != "CUST-1001":
        raise AssertionError(f"wrong endpoint result: {payload}")

    missing = client.get("/api/tools/lookup_customer/CUST-9999")
    if missing.status_code != 404:
        raise AssertionError(f"missing customer should return 404: {missing.status_code} {missing.text}")


def main() -> None:
    assert_lookup_customer_returns_real_profile()
    assert_lookup_customer_handles_suspended_and_missing_customers()
    assert_lookup_customer_api_endpoint()
    print("lookup customer tests passed")


if __name__ == "__main__":
    main()
