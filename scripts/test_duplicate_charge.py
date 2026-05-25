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
from backend.tools import check_duplicate_charge  # noqa: E402
from seed_billing import seed_billing  # noqa: E402


TEMP_DIRS: list[tempfile.TemporaryDirectory] = []


def build_seeded_billing_db() -> Path:
    tmpdir = tempfile.TemporaryDirectory()
    TEMP_DIRS.append(tmpdir)
    db_path = Path(tmpdir.name) / "resolveflow.db"
    seed_billing(db_path)
    return db_path


def assert_detects_seeded_duplicate_charge() -> None:
    db_path = build_seeded_billing_db()
    result = check_duplicate_charge(
        "CUST-1001",
        db_path=db_path,
        reference_date=date(2026, 5, 23),
    )

    if result["duplicate_confirmed"] is not True or result["has_duplicate"] is not True:
        raise AssertionError(f"seeded duplicate should be confirmed: {result}")
    if result["duplicate_payment_ids"] != ["PAY-1001-A", "PAY-1001-B"]:
        raise AssertionError(f"duplicate payment IDs wrong: {result}")
    if result["duplicate_amount"] != 1199.0:
        raise AssertionError(f"duplicate amount wrong: {result}")
    if result["payment_method"] != "upi":
        raise AssertionError(f"payment method wrong: {result}")
    if result["invoice_id"] != "INV-8821" or result["single_matching_invoice"] is not True:
        raise AssertionError(f"matching invoice evidence wrong: {result}")
    expected_evidence = {
        "same customer_id",
        "same payment amount",
        "same payment method",
        "payment timestamps within 10 minutes",
        "single matching invoice",
    }
    if not expected_evidence.issubset(set(result["evidence"])):
        raise AssertionError(f"missing duplicate evidence: {result}")


def assert_returns_negative_when_no_duplicate_or_outside_window() -> None:
    db_path = build_seeded_billing_db()
    clean = check_duplicate_charge("CUST-1002", db_path=db_path, reference_date=date(2026, 5, 23))
    if clean["duplicate_confirmed"] or clean["duplicate_payment_ids"]:
        raise AssertionError(f"non-duplicate customer should be clean: {clean}")

    old = check_duplicate_charge(
        "CUST-1001",
        db_path=db_path,
        lookback_days=1,
        reference_date=date(2026, 5, 23),
    )
    if old["duplicate_confirmed"]:
        raise AssertionError(f"duplicate outside lookback window should not confirm: {old}")

    unknown = check_duplicate_charge("CUST-9999", db_path=db_path, reference_date=date(2026, 5, 23))
    if unknown["duplicate_confirmed"] or unknown["duplicate_groups"]:
        raise AssertionError(f"unknown customer should have no duplicate groups: {unknown}")


def assert_requires_single_matching_invoice() -> None:
    db_path = build_seeded_billing_db()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO invoices(invoice_id, customer_id, amount, date, status, payment_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("INV-EXTRA", "CUST-1001", 1199, "2026-05-18", "paid", "PAY-1001-B"),
        )

    result = check_duplicate_charge("CUST-1001", db_path=db_path, reference_date=date(2026, 5, 23))
    if result["duplicate_confirmed"] is True or result["single_matching_invoice"] is True:
        raise AssertionError(f"duplicate should not confirm without one matching invoice: {result}")
    if "matching invoice not unique" not in result["evidence"]:
        raise AssertionError(f"non-unique invoice evidence should be recorded: {result}")


def assert_validates_inputs() -> None:
    db_path = build_seeded_billing_db()
    for kwargs in (
        {"customer_id": "   ", "db_path": db_path},
        {"customer_id": "CUST-1001", "lookback_days": 0, "db_path": db_path},
        {"customer_id": "CUST-1001", "reference_date": "   ", "db_path": db_path},
    ):
        try:
            check_duplicate_charge(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad check_duplicate_charge inputs were accepted: {kwargs}")


def assert_duplicate_charge_api_endpoint() -> None:
    db_path = build_seeded_billing_db()
    client = TestClient(create_app(db_path=db_path))

    response = client.get("/api/tools/check_duplicate_charge/CUST-1001?lookback_days=30")
    if response.status_code != 200:
        raise AssertionError(f"duplicate endpoint failed: {response.status_code} {response.text}")
    payload = response.json()
    if payload["tool_name"] != "check_duplicate_charge" or payload["ok"] is not True:
        raise AssertionError(f"wrong tool envelope: {payload}")
    if payload["result"]["duplicate_confirmed"] is not True:
        raise AssertionError(f"endpoint should confirm seeded duplicate: {payload}")

    invalid = client.get("/api/tools/check_duplicate_charge/CUST-1001?lookback_days=0")
    if invalid.status_code != 422:
        raise AssertionError(f"bad lookback should return 422: {invalid.status_code} {invalid.text}")


def main() -> None:
    assert_detects_seeded_duplicate_charge()
    assert_returns_negative_when_no_duplicate_or_outside_window()
    assert_requires_single_matching_invoice()
    assert_validates_inputs()
    assert_duplicate_charge_api_endpoint()
    print("duplicate charge tests passed")


if __name__ == "__main__":
    main()
