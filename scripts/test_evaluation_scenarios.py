from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.db import reset_to_initial_state  # noqa: E402
from backend.evaluation import load_evaluation_scenarios, validate_evaluation_scenarios  # noqa: E402
from backend.evaluation.scenarios import EXPECTED_SCENARIO_IDS  # noqa: E402


def assert_loads_all_30_scenarios() -> None:
    scenarios = load_evaluation_scenarios()
    if len(scenarios) != 30:
        raise AssertionError(f"expected 30 scenarios, found {len(scenarios)}")
    if [scenario.scenario_id for scenario in scenarios] != EXPECTED_SCENARIO_IDS:
        raise AssertionError(f"scenario order/id mismatch: {[scenario.scenario_id for scenario in scenarios]}")


def assert_every_scenario_has_initial_and_goal_state() -> None:
    scenarios = load_evaluation_scenarios()
    for scenario in scenarios:
        if not scenario.initial_state:
            raise AssertionError(f"{scenario.scenario_id} missing initial_state")
        if not scenario.goal_state:
            raise AssertionError(f"{scenario.scenario_id} missing goal_state")
        if scenario.initial_state.get("db_reset_required") is not True:
            raise AssertionError(f"{scenario.scenario_id} must request DB reset")
        for field_name in ("expected_intents", "issue_queue_order", "required_tools", "forbidden_tools"):
            if not isinstance(scenario.goal_state.get(field_name), list):
                raise AssertionError(f"{scenario.scenario_id} goal_state.{field_name} must be a list")
        if not scenario.goal_state.get("success_criteria"):
            raise AssertionError(f"{scenario.scenario_id} must define success criteria")


def assert_scenarios_validate_against_seed_database() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-scenarios-")) / "resolveflow.db"
    reset_to_initial_state(db_path)
    report = validate_evaluation_scenarios(db_path=db_path)
    if not report.ok:
        raise AssertionError(f"evaluation scenario validation failed: {report.problems}")
    if report.scenario_count != 30:
        raise AssertionError(f"wrong scenario count: {report}")


def assert_high_risk_cases_encode_real_goals() -> None:
    scenarios = {scenario.scenario_id: scenario for scenario in load_evaluation_scenarios()}
    duplicate = scenarios["case_02_duplicate_charge"]
    if duplicate.goal_state["expected_artifacts"]["audit_evidence_contains"] != [
        "INV-8821",
        "PAY-1001-A",
        "PAY-1001-B",
    ]:
        raise AssertionError(f"duplicate charge evidence is not grounded: {duplicate}")

    wrong_refund = scenarios["case_08_wrong_refund_request"]
    if wrong_refund.initial_state["scenario_date"] != "2026-06-15":
        raise AssertionError(f"wrong refund case needs a >30 day evaluation date: {wrong_refund}")
    if wrong_refund.goal_state["expected_artifacts"]["policy_exception_trigger"] != "payment_older_than_30_days":
        raise AssertionError(f"wrong refund trigger missing: {wrong_refund}")

    unavailable = scenarios["case_13_unavailable_service_request"]
    if unavailable.goal_state["expected_artifacts"]["unsupported_service"] != "1 Gbps":
        raise AssertionError(f"unavailable service case should deny 1 Gbps: {unavailable}")
    if "schedule_technician" not in unavailable.goal_state["forbidden_tools"]:
        raise AssertionError(f"unavailable service case must forbid technician scheduling: {unavailable}")

    injection = scenarios["case_15_prompt_injection_refund"]
    if injection.goal_state["expected_artifacts"]["prompt_injection_blocked"] is not True:
        raise AssertionError(f"prompt injection scenario should encode blocked override: {injection}")

    proactive = scenarios["case_30_proactive_credit_then_cancel"]
    if proactive.goal_state["expected_artifacts"]["proactive_credit"] is not True:
        raise AssertionError(f"proactive credit scenario should be encoded: {proactive}")


def main() -> None:
    assert_loads_all_30_scenarios()
    assert_every_scenario_has_initial_and_goal_state()
    assert_scenarios_validate_against_seed_database()
    assert_high_risk_cases_encode_real_goals()
    print("evaluation scenario tests passed")


if __name__ == "__main__":
    main()
