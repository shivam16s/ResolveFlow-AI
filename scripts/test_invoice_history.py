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
from backend.tools import get_invoice_history  # noqa: E402
from seed_billing import seed_billing  # noqa: E402


TEMP_DIRS: list[tempfile.TemporaryDirectory] = []


def build_seeded_billing_db() -> Path:
    tmpdir = tempfile.TemporaryDirectory()
    TEMP_DIRS.append(tmpdir)
    db_path = Path(tmpdir.name) / "resolveflow.db"
    seed_billing(db_path)
    return db_path


def assert_invoice_history_returns_seeded_invoice_with_payment_metadata() -> None:
    db_path = build_seeded_billing_db()
    invoices = get_invoice_history(
        "CUST-1001",
        months=3,
        db_path=db_path,
        reference_date=date(2026, 5, 23),
    )

    if len(invoices) != 1:
        raise AssertionError(f"expected one seeded invoice: {invoices}")
    invoice = invoices[0]
    expected = {
        "invoice_id": "INV-8821",
        "customer_id": "CUST-1001",
        "amount": 1199.0,
        "date": "2026-05-18",
        "status": "disputed",
        "payment_id": "PAY-1001-A",
        "payment_amount": 1199.0,
        "payment_date": "2026-05-18T09:10:00",
        "payment_method": "upi",
        "duplicate_flag": True,
    }
    for key, value in expected.items():
        if invoice.get(key) != value:
            raise AssertionError(f"wrong invoice field {key}: {invoice}")


def assert_invoice_history_filters_by_month_window_and_validates_inputs() -> None:
    db_path = build_seeded_billing_db()
    current = get_invoice_history(
        "CUST-1014",
        months=1,
        db_path=db_path,
        reference_date=date(2026, 5, 23),
    )
    old = get_invoice_history(
        "CUST-1014",
        months=1,
        db_path=db_path,
        reference_date=date(2026, 6, 30),
    )
    if len(current) != 1 or current[0]["invoice_id"] != "INV-1014":
        raise AssertionError(f"expected pending April invoice within one-month window: {current}")
    if old:
        raise AssertionError(f"invoice should fall outside later one-month window: {old}")

    if get_invoice_history("CUST-9999", db_path=db_path, reference_date=date(2026, 5, 23)) != []:
        raise AssertionError("unknown customer should return an empty invoice list")

    for kwargs in (
        {"customer_id": "   ", "db_path": db_path},
        {"customer_id": "CUST-1001", "months": 0, "db_path": db_path},
        {"customer_id": "CUST-1001", "db_path": db_path, "reference_date": "   "},
    ):
        try:
            get_invoice_history(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad get_invoice_history inputs were accepted: {kwargs}")


def assert_invoice_history_api_endpoint() -> None:
    db_path = build_seeded_billing_db()
    client = TestClient(create_app(db_path=db_path))

    response = client.get("/api/tools/get_invoice_history/CUST-1001?months=3")
    if response.status_code != 200:
        raise AssertionError(f"invoice endpoint failed: {response.status_code} {response.text}")
    payload = response.json()
    if payload["tool_name"] != "get_invoice_history" or payload["ok"] is not True:
        raise AssertionError(f"wrong tool envelope: {payload}")
    if payload["result"]["invoice_count"] != 1:
        raise AssertionError(f"wrong invoice count: {payload}")
    if payload["result"]["invoices"][0]["invoice_id"] != "INV-8821":
        raise AssertionError(f"wrong invoice payload: {payload}")

    invalid = client.get("/api/tools/get_invoice_history/CUST-1001?months=0")
    if invalid.status_code != 422:
        raise AssertionError(f"bad months should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_invoice_history_returns_seeded_invoice_with_payment_metadata()
    assert_invoice_history_filters_by_month_window_and_validates_inputs()
    assert_invoice_history_api_endpoint()
    print("invoice history tests passed")


if __name__ == "__main__":
    main()
