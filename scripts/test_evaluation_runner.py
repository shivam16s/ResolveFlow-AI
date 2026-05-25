from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.evaluation import run_evaluation  # noqa: E402


def assert_runs_pass_k_for_all_13_cases() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-runner-")) / "resolveflow.db"
    result = run_evaluation(k=5, db_path=db_path)

    if result["pass_k"] != 5:
        raise AssertionError(f"wrong pass_k: {result}")
    if result["scenario_count"] != 13:
        raise AssertionError(f"wrong scenario count: {result}")
    if result["total_runs"] != 65 or len(result["results"]) != 65:
        raise AssertionError(f"expected 65 case results: {result['total_runs']}")
    if not 0 <= result["success_rate"] <= 1:
        raise AssertionError(f"success rate out of range: {result}")

    first = result["results"][0]
    if first["pass_index"] != 1 or first["scenario_id"] != "case_01_simple_bill_question":
        raise AssertionError(f"first result order wrong: {first}")
    last = result["results"][-1]
    if last["pass_index"] != 5 or last["scenario_id"] != "case_13_unavailable_service_request":
        raise AssertionError(f"last result order wrong: {last}")


def assert_case_results_include_tool_and_reset_artifacts() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-artifacts-")) / "resolveflow.db"
    result = run_evaluation(k=1, db_path=db_path)
    by_id = {item["scenario_id"]: item for item in result["results"]}

    duplicate = by_id["case_02_duplicate_charge"]
    for tool_name in ("lookup_customer", "get_invoice_history", "check_duplicate_charge", "retrieve_policy", "create_ticket"):
        if tool_name not in duplicate["tools_called"]:
            raise AssertionError(f"duplicate case did not call {tool_name}: {duplicate}")
    if duplicate["artifacts"]["reset_table_counts"]["customers"] != 20:
        raise AssertionError(f"reset counts missing from artifacts: {duplicate}")
    if "duplicate_charge_policy" not in duplicate["policies_retrieved"]:
        raise AssertionError(f"duplicate policy retrieval missing: {duplicate}")

    outage_credit = by_id["case_03_outage_credit"]
    if "apply_credit" not in outage_credit["tools_called"]:
        raise AssertionError(f"outage credit case should apply credit: {outage_credit}")
    if outage_credit["artifacts"]["tool_results"]["apply_credit"]["policy_status"] != "compliant":
        raise AssertionError(f"credit policy result should be compliant: {outage_credit}")

    tool_failure = by_id["case_09_tool_failure"]
    outage_result = tool_failure["artifacts"]["tool_results"]["check_outage_status"]
    if outage_result.get("ok") is not False or "simulated" not in outage_result.get("error", ""):
        raise AssertionError(f"tool failure flag was not applied: {tool_failure}")


def assert_rejects_invalid_pass_count() -> None:
    try:
        run_evaluation(k=0)
    except ValueError:
        pass
    else:
        raise AssertionError("k=0 should be rejected")


def main() -> None:
    assert_runs_pass_k_for_all_13_cases()
    assert_case_results_include_tool_and_reset_artifacts()
    assert_rejects_invalid_pass_count()
    print("evaluation runner tests passed")


if __name__ == "__main__":
    main()
