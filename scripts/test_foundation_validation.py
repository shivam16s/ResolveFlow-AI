from __future__ import annotations

from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DB_PACKAGE = ROOT / "backend" / "db"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DB_PACKAGE))

from backend.db.validation import assert_foundation_ready, validate_foundation_assets  # noqa: E402
from seed_billing import seed_billing  # noqa: E402
from seed_outages import seed_outages  # noqa: E402


def test_foundation_validation_passes_real_seed_shape() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "resolveflow.db"
        seed_billing(db_path)
        seed_outages(db_path)

        report = assert_foundation_ready(
            db_path=db_path,
            policies_dir=ROOT / "docs" / "policies",
            scenarios_dir=ROOT / "docs" / "scenarios",
        )
        if not report.ok:
            raise AssertionError(report.to_dict())
        if report.table_count != 13:
            raise AssertionError(f"expected 13 tables: {report.to_dict()}")
        if report.row_counts["customers"] != 20 or report.row_counts["invoices"] != 20:
            raise AssertionError(f"seed counts wrong: {report.to_dict()}")
        if report.duplicate_charge_customer_ids != ["CUST-1001"]:
            raise AssertionError(f"duplicate scenario missing: {report.to_dict()}")
        if report.verified_outage_count == 0 or report.unverified_outage_count == 0:
            raise AssertionError(f"outage mix missing: {report.to_dict()}")


def test_foundation_validation_reports_missing_assets() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        report = validate_foundation_assets(
            db_path=Path(tmpdir) / "missing.db",
            policies_dir=Path(tmpdir) / "missing-policies",
            scenarios_dir=Path(tmpdir) / "missing-scenarios",
        )
        if report.ok:
            raise AssertionError("missing foundation assets should not validate")
        expected_fragments = ["database does not exist", "expected 8 policy docs", "expected 20 scenario scripts"]
        for fragment in expected_fragments:
            if not any(fragment in problem for problem in report.problems):
                raise AssertionError(f"missing problem fragment {fragment}: {report.to_dict()}")


def main() -> None:
    test_foundation_validation_passes_real_seed_shape()
    test_foundation_validation_reports_missing_assets()
    print("foundation validation tests passed")


if __name__ == "__main__":
    main()
