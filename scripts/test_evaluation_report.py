from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.evaluation import generate_metric_report, run_evaluation  # noqa: E402
from backend.evaluation.reporting import METRIC_NAMES, NON_COLLABORATIVE_SCENARIOS  # noqa: E402


def assert_generates_9_metric_report_from_real_run() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-report-")) / "resolveflow.db"
    evaluation = run_evaluation(k=1, db_path=db_path)
    report = generate_metric_report(evaluation)

    if report["pass_k"] != 1 or report["scenario_count"] != 13 or report["total_runs"] != 13:
        raise AssertionError(f"wrong report identity: {report}")
    if tuple(report["metrics"]) != METRIC_NAMES:
        raise AssertionError(f"metric names/order wrong: {list(report['metrics'])}")
    if len(report["metrics"]) != 9:
        raise AssertionError(f"expected exactly 9 metrics: {report['metrics']}")
    for metric_name, metric in report["metrics"].items():
        if metric["name"] != metric_name:
            raise AssertionError(f"metric key/name mismatch: {metric}")
        if not isinstance(metric["value"], (int, float)):
            raise AssertionError(f"metric value must be numeric: {metric}")


def assert_non_collaborative_degradation_metric_is_present() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-report-nc-")) / "resolveflow.db"
    report = generate_metric_report(k=1, db_path=db_path)
    metric = report["metrics"]["non_collaborative_degradation"]

    if metric["higher_is_better"] is not False:
        raise AssertionError(f"degradation should be lower-is-better: {metric}")
    if metric["value"] < 0:
        raise AssertionError(f"degradation should not be negative: {metric}")
    if set(report["non_collaborative_scenarios"]) != NON_COLLABORATIVE_SCENARIOS:
        raise AssertionError(f"non-collaborative case set wrong: {report['non_collaborative_scenarios']}")
    for scenario_id in NON_COLLABORATIVE_SCENARIOS:
        if report["per_scenario"][scenario_id]["non_collaborative"] is not True:
            raise AssertionError(f"{scenario_id} should be marked non-collaborative")


def assert_report_rejects_bad_evaluation_result() -> None:
    bad_payloads = (
        [],
        {"results": "not-a-list"},
        {"results": [{"scenario_id": "missing-case"}]},
    )
    for payload in bad_payloads:
        try:
            generate_metric_report(payload)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad report payload accepted: {payload}")


def main() -> None:
    assert_generates_9_metric_report_from_real_run()
    assert_non_collaborative_degradation_metric_is_present()
    assert_report_rejects_bad_evaluation_result()
    print("evaluation report tests passed")


if __name__ == "__main__":
    main()
