from __future__ import annotations

import json
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

    if report["pass_k"] != 1 or report["scenario_count"] != 30 or report["total_runs"] != 30:
        raise AssertionError(f"wrong report identity: {report}")
    if tuple(report["metrics"]) != METRIC_NAMES:
        raise AssertionError(f"metric names/order wrong: {list(report['metrics'])}")
    if len(report["metrics"]) != 9:
        raise AssertionError(f"expected exactly 9 metrics: {report['metrics']}")
    for metric_name, metric in report["metrics"].items():
        if metric["name"] != metric_name:
            raise AssertionError(f"metric key/name mismatch: {metric}")
        if metric["value"] is not None and not isinstance(metric["value"], (int, float)):
            raise AssertionError(f"metric value must be numeric: {metric}")


def assert_non_collaborative_degradation_metric_is_present() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-report-nc-")) / "resolveflow.db"
    report = generate_metric_report(k=1, db_path=db_path)
    metric = report["metrics"]["non_collaborative_degradation"]

    if metric["higher_is_better"] is not False:
        raise AssertionError(f"degradation should be lower-is-better: {metric}")
    if not isinstance(metric["value"], (int, float)):
        raise AssertionError(f"degradation should be numeric: {metric}")
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


def assert_unmeasured_rates_are_na_not_perfect() -> None:
    scenarios_path = Path(tempfile.mkdtemp(prefix="resolveflow-report-na-scenario-")) / "scenarios.json"
    scenario = {
        "scenario_id": "no_measured_dimensions",
        "title": "No measured dimensions",
        "customer_id": "CUST-1003",
        "customer_messages": ["Thanks"],
        "initial_state": {
            "db_reset_required": True,
            "scenario_date": "2026-05-24",
            "customer": {
                "customer_id": "CUST-1003",
                "account_status": "active",
                "plan_id": "fiber_work_500",
                "risk_level": "low",
            },
            "seed_evidence": {"invoices": [], "payments": [], "outages": []},
            "conversation_context": [],
        },
        "goal_state": {
            "expected_final_status": "abandoned",
            "expected_intents": ["general_query"],
            "issue_queue_order": [],
            "required_tools": [],
            "forbidden_tools": [],
            "required_policies": [],
            "expected_artifacts": {},
            "success_criteria": ["No policy, escalation, or audit dimensions are measured."],
        },
    }
    scenarios_path.write_text(json.dumps([scenario]), encoding="utf-8")
    evaluation_result = {
        "pass_k": 1,
        "scenario_count": 1,
        "total_runs": 1,
        "passed_runs": 1,
        "success_rate": 1.0,
        "results": [
            {
                "pass_index": 1,
                "scenario_id": "no_measured_dimensions",
                "customer_id": "CUST-1003",
                "passed": True,
                "score": 1.0,
                "observed_intents": ["general_query"],
                "issue_queue_order": [],
                "tools_called": [],
                "required_tools_missing": [],
                "forbidden_tools_called": [],
                "policies_retrieved": [],
                "artifacts": {},
                "failures": [],
            }
        ],
    }
    report = generate_metric_report(evaluation_result, scenarios_path=scenarios_path)
    for metric_name in ("policy_compliance", "escalation_correctness", "audit_trail_coverage"):
        metric = report["metrics"][metric_name]
        if metric["value"] is not None:
            raise AssertionError(f"{metric_name} should be N/A when denominator is zero: {metric}")
        if metric["denominator"] != 0.0:
            raise AssertionError(f"{metric_name} denominator should show zero observations: {metric}")


def assert_non_collaborative_degradation_preserves_sign_flip() -> None:
    scenarios_path = Path(tempfile.mkdtemp(prefix="resolveflow-report-sign-scenario-")) / "scenarios.json"
    scenarios = [
        {
            "scenario_id": "case_01_simple_bill_question",
            "title": "Collaborative low score",
            "customer_id": "CUST-1003",
            "customer_messages": ["bill"],
            "initial_state": {
                "db_reset_required": True,
                "scenario_date": "2026-05-24",
                "customer": {
                    "customer_id": "CUST-1003",
                    "account_status": "active",
                    "plan_id": "fiber_work_500",
                    "risk_level": "low",
                },
                "seed_evidence": {"invoices": [], "payments": [], "outages": []},
                "conversation_context": [],
            },
            "goal_state": {
                "expected_final_status": "resolved",
                "expected_intents": ["billing_dispute"],
                "issue_queue_order": [],
                "required_tools": [],
                "forbidden_tools": [],
                "required_policies": [],
                "expected_artifacts": {},
                "success_criteria": [],
            },
        },
        {
            "scenario_id": "case_11_impatient_user",
            "title": "Non-collaborative high score",
            "customer_id": "CUST-1003",
            "customer_messages": ["hurry up"],
            "initial_state": {
                "db_reset_required": True,
                "scenario_date": "2026-05-24",
                "customer": {
                    "customer_id": "CUST-1003",
                    "account_status": "active",
                    "plan_id": "fiber_work_500",
                    "risk_level": "low",
                },
                "seed_evidence": {"invoices": [], "payments": [], "outages": []},
                "conversation_context": [],
            },
            "goal_state": {
                "expected_final_status": "resolved",
                "expected_intents": ["billing_dispute"],
                "issue_queue_order": [],
                "required_tools": [],
                "forbidden_tools": [],
                "required_policies": [],
                "expected_artifacts": {},
                "success_criteria": [],
            },
        },
    ]
    scenarios_path.write_text(json.dumps(scenarios), encoding="utf-8")
    evaluation_result = {
        "pass_k": 1,
        "scenario_count": 2,
        "total_runs": 2,
        "passed_runs": 2,
        "success_rate": 1.0,
        "results": [
            {
                "pass_index": 1,
                "scenario_id": "case_01_simple_bill_question",
                "customer_id": "CUST-1003",
                "passed": True,
                "score": 0.4,
                "observed_intents": [],
                "issue_queue_order": [],
                "tools_called": [],
                "required_tools_missing": [],
                "forbidden_tools_called": [],
                "policies_retrieved": [],
                "artifacts": {},
                "failures": [],
            },
            {
                "pass_index": 1,
                "scenario_id": "case_11_impatient_user",
                "customer_id": "CUST-1003",
                "passed": True,
                "score": 0.9,
                "observed_intents": [],
                "issue_queue_order": [],
                "tools_called": [],
                "required_tools_missing": [],
                "forbidden_tools_called": [],
                "policies_retrieved": [],
                "artifacts": {},
                "failures": [],
            },
        ],
    }
    report = generate_metric_report(evaluation_result, scenarios_path=scenarios_path)
    metric = report["metrics"]["non_collaborative_degradation"]
    if metric["value"] != -0.5:
        raise AssertionError(f"sign flip should be preserved, not clamped: {metric}")


def main() -> None:
    assert_generates_9_metric_report_from_real_run()
    assert_non_collaborative_degradation_metric_is_present()
    assert_report_rejects_bad_evaluation_result()
    assert_unmeasured_rates_are_na_not_perfect()
    assert_non_collaborative_degradation_preserves_sign_flip()
    print("evaluation report tests passed")


if __name__ == "__main__":
    main()
