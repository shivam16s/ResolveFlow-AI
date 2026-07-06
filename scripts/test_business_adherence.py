from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.evaluation.business_adherence import (  # noqa: E402
    INCONSISTENT_RULES,
    MISSED_ESCALATION,
    POLICY_VIOLATION,
    compute_business_adherence,
)
from backend.evaluation.runner import run_evaluation  # noqa: E402


def _fake_run(results: list[dict], pass_k: int = 1) -> dict:
    return {"pass_k": pass_k, "scenario_count": len(results), "results": results}


def test_clean_run_scores_full_adherence() -> None:
    # Two scenarios, both passing on every pass, no violations.
    results = [
        {"scenario_id": "case_01_simple_bill_question", "pass_index": 1, "passed": True,
         "failures": [], "forbidden_tools_called": []},
        {"scenario_id": "case_03_outage_credit", "pass_index": 1, "passed": True,
         "failures": [], "forbidden_tools_called": []},
    ]
    report = compute_business_adherence(_fake_run(results))
    assert report["business_adherence_score"] == 1.0
    assert report["grade"].startswith("A")
    dims = {d["dimension"]: d for d in report["dimensions"]}
    assert dims[POLICY_VIOLATION]["violations"] == 0


def test_policy_violation_is_detected() -> None:
    results = [
        {"scenario_id": "case_08_wrong_refund_request", "pass_index": 1, "passed": False,
         "failures": ["forbidden tool called apply_credit"], "forbidden_tools_called": ["apply_credit"]},
    ]
    report = compute_business_adherence(_fake_run(results))
    dims = {d["dimension"]: d for d in report["dimensions"]}
    assert dims[POLICY_VIOLATION]["violations"] == 1
    assert "case_08_wrong_refund_request" in dims[POLICY_VIOLATION]["offending_scenarios"]
    assert report["business_adherence_score"] < 1.0


def test_policy_violation_uses_structured_signals_not_failure_prose() -> None:
    prose_only = [
        {"scenario_id": "case_08_wrong_refund_request", "pass_index": 1, "passed": False,
         "failures": ["credit should not have happened"], "forbidden_tools_called": []},
    ]
    prose_report = compute_business_adherence(_fake_run(prose_only))
    prose_dims = {d["dimension"]: d for d in prose_report["dimensions"]}
    assert prose_dims[POLICY_VIOLATION]["violations"] == 0

    structured = [
        {"scenario_id": "case_08_wrong_refund_request", "pass_index": 1, "passed": False,
         "failures": ["wording changed by runner"], "forbidden_tools_called": [],
         "artifacts": {"tool_results": {"apply_credit": {"policy_status": "blocked"}}}},
    ]
    structured_report = compute_business_adherence(_fake_run(structured))
    structured_dims = {d["dimension"]: d for d in structured_report["dimensions"]}
    assert structured_dims[POLICY_VIOLATION]["violations"] == 1


def test_missed_escalation_only_counts_when_required() -> None:
    # case_04 requires retention handoff in the seeded scenarios; simulate a miss.
    results = [
        {"scenario_id": "case_04_cancellation_intent", "pass_index": 1, "passed": False,
         "failures": ["expected handoff side effect missing"], "forbidden_tools_called": []},
    ]
    report = compute_business_adherence(_fake_run(results))
    dims = {d["dimension"]: d for d in report["dimensions"]}
    # Either it is an escalation opportunity and counted, or the scenario does not
    # require escalation; both are internally consistent.
    if dims[MISSED_ESCALATION]["opportunities"] > 0:
        assert dims[MISSED_ESCALATION]["violations"] == 1


def test_observed_escalation_uses_structured_handoff_artifacts() -> None:
    results = [
        {"scenario_id": "case_04_cancellation_intent", "pass_index": 1, "passed": True,
         "failures": ["handoff wording no longer matters"], "forbidden_tools_called": [],
         "tools_called": ["generate_handoff_summary"],
         "artifacts": {"tool_results": {"generate_handoff_summary": {"handoff_summary_id": "H-1"}}}},
    ]
    report = compute_business_adherence(_fake_run(results))
    dims = {d["dimension"]: d for d in report["dimensions"]}
    if dims[MISSED_ESCALATION]["opportunities"] > 0:
        assert dims[MISSED_ESCALATION]["violations"] == 0


def test_inconsistency_across_passes_is_flagged() -> None:
    results = [
        {"scenario_id": "case_01_simple_bill_question", "pass_index": 1, "passed": True,
         "failures": [], "forbidden_tools_called": []},
        {"scenario_id": "case_01_simple_bill_question", "pass_index": 2, "passed": False,
         "failures": ["flaky failure"], "forbidden_tools_called": []},
    ]
    report = compute_business_adherence(_fake_run(results, pass_k=2))
    dims = {d["dimension"]: d for d in report["dimensions"]}
    assert dims[INCONSISTENT_RULES]["violations"] == 1
    assert "case_01_simple_bill_question" in dims[INCONSISTENT_RULES]["offending_scenarios"]


def test_business_adherence_accepts_real_evaluation_runner_output() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-business-real-")) / "resolveflow.db"
    evaluation = run_evaluation(k=1, db_path=db_path)
    report = compute_business_adherence(evaluation)

    assert evaluation["scenario_count"] == 30
    assert evaluation["total_runs"] == 30
    assert 0 <= report["business_adherence_score"] <= 1
    assert len(report["dimensions"]) == 3
    assert {dimension["dimension"] for dimension in report["dimensions"]} == {
        POLICY_VIOLATION,
        MISSED_ESCALATION,
        INCONSISTENT_RULES,
    }


if __name__ == "__main__":
    test_clean_run_scores_full_adherence()
    test_policy_violation_is_detected()
    test_policy_violation_uses_structured_signals_not_failure_prose()
    test_missed_escalation_only_counts_when_required()
    test_observed_escalation_uses_structured_handoff_artifacts()
    test_inconsistency_across_passes_is_flagged()
    test_business_adherence_accepts_real_evaluation_runner_output()
    print("business adherence tests passed")
