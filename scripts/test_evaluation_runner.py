from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.evaluation import run_evaluation  # noqa: E402
from backend.evaluation.runner import _case_score, _issue_queue_order_failure  # noqa: E402


def assert_runs_pass_k_for_all_30_cases() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-runner-")) / "resolveflow.db"
    result = run_evaluation(k=5, db_path=db_path, use_live_agent=False)

    if result["pass_k"] != 5:
        raise AssertionError(f"wrong pass_k: {result}")
    if result["scenario_count"] != 30:
        raise AssertionError(f"wrong scenario count: {result}")
    if result["total_runs"] != 150 or len(result["results"]) != 150:
        raise AssertionError(f"expected 150 case results: {result['total_runs']}")
    if not 0 <= result["success_rate"] <= 1:
        raise AssertionError(f"success rate out of range: {result}")

    first = result["results"][0]
    if first["pass_index"] != 1 or first["scenario_id"] != "case_01_simple_bill_question":
        raise AssertionError(f"first result order wrong: {first}")
    last = result["results"][-1]
    if last["pass_index"] != 5 or last["scenario_id"] != "case_30_proactive_credit_then_cancel":
        raise AssertionError(f"last result order wrong: {last}")


def assert_case_results_include_tool_and_reset_artifacts() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-artifacts-")) / "resolveflow.db"
    result = run_evaluation(k=1, db_path=db_path, use_live_agent=False)
    by_id = {item["scenario_id"]: item for item in result["results"]}

    duplicate = by_id["case_02_duplicate_charge"]
    for tool_name in ("lookup_customer", "get_invoice_history", "check_duplicate_charge", "retrieve_policy", "create_ticket"):
        if tool_name not in duplicate["tools_called"]:
            raise AssertionError(f"duplicate case did not call {tool_name}: {duplicate}")
    if duplicate["artifacts"]["reset_table_counts"]["customers"] != 20:
        raise AssertionError(f"reset counts missing from artifacts: {duplicate}")
    if "duplicate_charge_policy" not in duplicate["policies_retrieved"]:
        raise AssertionError(f"duplicate policy retrieval missing: {duplicate}")
    duplicate_context = duplicate["artifacts"]["policy_contexts"]["duplicate_charge_refund_dag"]
    if duplicate_context["payment_age_days"] != 6:
        raise AssertionError(f"duplicate payment age should be derived from seeded payment date: {duplicate_context}")

    outage_credit = by_id["case_03_outage_credit"]
    if "apply_credit" not in outage_credit["tools_called"]:
        raise AssertionError(f"outage credit case should apply credit: {outage_credit}")
    if outage_credit["artifacts"]["tool_results"]["apply_credit"]["policy_status"] != "compliant":
        raise AssertionError(f"credit policy result should be compliant: {outage_credit}")

    tool_failure = by_id["case_09_tool_failure"]
    outage_result = tool_failure["artifacts"]["tool_results"]["check_outage_status"]
    if outage_result.get("ok") is not False or "simulated" not in outage_result.get("error", ""):
        raise AssertionError(f"tool failure flag was not applied: {tool_failure}")
    if "check_outage_status" in tool_failure["tools_called"]:
        raise AssertionError(f"failed tool should not be counted as called: {tool_failure}")
    attempts = tool_failure["artifacts"].get("tool_attempts", [])
    if {"tool_name": "check_outage_status", "ok": False} not in attempts:
        raise AssertionError(f"failed tool attempt should be recorded separately: {tool_failure}")

    cancellation = by_id["case_04_cancellation_intent"]
    cancellation_context = cancellation["artifacts"]["policy_contexts"]["cancellation_retention_dag"]
    if cancellation_context["churn_score"] != 0.84:
        raise AssertionError(f"churn score should be derived from seeded customer row: {cancellation_context}")


def assert_required_tools_are_rubric_not_execution_plan() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-rubric-db-")) / "resolveflow.db"
    scenarios_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-rubric-scenario-")) / "scenarios.json"
    scenario = {
        "scenario_id": "rubric_not_plan",
        "title": "Rubric should not drive tools",
        "customer_id": "CUST-1003",
        "customer_messages": ["Can you explain my latest broadband bill?"],
        "initial_state": {
            "db_reset_required": True,
            "scenario_date": "2026-05-24",
            "customer": {
                "customer_id": "CUST-1003",
                "account_status": "active",
                "plan_id": "fiber_work_500",
                "risk_level": "low",
            },
            "seed_evidence": {"invoices": ["INV-1003"], "payments": ["PAY-1003"], "outages": []},
            "conversation_context": [],
        },
        "goal_state": {
            "expected_final_status": "resolved",
            "expected_intents": ["billing_dispute"],
            "issue_queue_order": ["billing_dispute"],
            "required_tools": ["lookup_customer", "schedule_technician"],
            "forbidden_tools": [],
            "required_policies": [],
            "expected_artifacts": {},
            "success_criteria": ["The runner must not execute required_tools as the plan."],
        },
    }
    scenarios_path.write_text(json.dumps([scenario]), encoding="utf-8")
    result = run_evaluation(k=1, scenarios_path=scenarios_path, db_path=db_path, use_live_agent=False)
    case = result["results"][0]

    if "schedule_technician" in case["tools_called"]:
        raise AssertionError(f"runner executed required_tools directly: {case}")
    if "schedule_technician" not in case["required_tools_missing"]:
        raise AssertionError(f"missing rubric tool was not reported: {case}")
    if "get_invoice_history" not in case["artifacts"].get("agent_tool_plan", []):
        raise AssertionError(f"agent-derived plan did not inspect billing context: {case}")


def assert_case_score_does_not_double_count_tool_failures() -> None:
    scenario = SimpleNamespace(
        goal_state={
            "required_policies": [],
            "issue_queue_order": [],
            "expected_artifacts": {},
        }
    )
    score = _case_score(
        scenario=scenario,
        required_tools=["lookup_customer", "schedule_technician"],
        missing_tools=["schedule_technician"],
        forbidden_called=[],
        failure_count=1,
    )
    if score != 0.5:
        raise AssertionError(f"missing tool should be charged once, got score {score}")


def assert_live_agent_mode_observes_chat_stream_tools() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-live-db-")) / "resolveflow.db"
    scenarios_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-live-scenario-")) / "scenarios.json"
    scenario = {
        "scenario_id": "live_agent_probe",
        "title": "Live agent probe",
        "customer_id": "CUST-1001",
        "customer_messages": ["I was charged twice this month. Please fix it."],
        "initial_state": {
            "db_reset_required": True,
            "scenario_date": "2026-05-24",
            "customer": {
                "customer_id": "CUST-1001",
                "account_status": "active",
                "plan_id": "fiber_plus_200",
                "risk_level": "high",
            },
            "seed_evidence": {
                "invoices": ["INV-8821"],
                "payments": ["PAY-1001-A", "PAY-1001-B"],
                "outages": [],
            },
            "conversation_context": [],
        },
        "goal_state": {
            "expected_final_status": "resolved",
            "expected_intents": ["duplicate_charge"],
            "issue_queue_order": ["duplicate_charge"],
            "required_tools": ["lookup_customer", "get_invoice_history", "check_duplicate_charge"],
            "forbidden_tools": [],
            "required_policies": [],
            "expected_artifacts": {},
            "success_criteria": ["The runner must observe the real chat stream."],
        },
    }
    scenarios_path.write_text(json.dumps([scenario]), encoding="utf-8")
    result = run_evaluation(k=1, scenarios_path=scenarios_path, db_path=db_path, use_live_agent=True)
    case = result["results"][0]

    if case["artifacts"]["agent_source"] != "live_chat_stream":
        raise AssertionError(f"runner did not use live chat stream: {case}")
    if not case["artifacts"]["live_agent"]["ok"]:
        raise AssertionError(f"live chat probe failed: {case}")
    for tool_name in ("lookup_customer", "get_invoice_history", "check_duplicate_charge"):
        if tool_name not in case["artifacts"]["agent_tool_plan"]:
            raise AssertionError(f"live probe did not observe {tool_name}: {case}")


def assert_temperature_schedule_is_recorded_per_pass() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-temp-db-")) / "resolveflow.db"
    scenarios_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-temp-scenario-")) / "scenarios.json"
    scenario = {
        "scenario_id": "temperature_probe",
        "title": "Temperature probe",
        "customer_id": "CUST-1003",
        "customer_messages": ["Can you explain my latest broadband bill?"],
        "initial_state": {
            "db_reset_required": True,
            "scenario_date": "2026-05-24",
            "customer": {
                "customer_id": "CUST-1003",
                "account_status": "active",
                "plan_id": "fiber_work_500",
                "risk_level": "low",
            },
            "seed_evidence": {"invoices": ["INV-1003"], "payments": ["PAY-1003"], "outages": []},
            "conversation_context": [],
        },
        "goal_state": {
            "expected_final_status": "resolved",
            "expected_intents": ["billing_dispute"],
            "issue_queue_order": ["billing_dispute"],
            "required_tools": ["lookup_customer", "get_invoice_history"],
            "forbidden_tools": [],
            "required_policies": [],
            "expected_artifacts": {},
            "success_criteria": ["Temperatures should be tied to pass index."],
        },
    }
    scenarios_path.write_text(json.dumps([scenario]), encoding="utf-8")
    result = run_evaluation(
        k=3,
        scenarios_path=scenarios_path,
        db_path=db_path,
        use_live_agent=False,
        use_live_llm=True,
        temperature_schedule=[0.2, 0.8],
    )
    temperatures = [case["temperature"] for case in result["results"]]
    if temperatures != [0.2, 0.8, 0.2]:
        raise AssertionError(f"temperature schedule did not cycle by pass: {temperatures}")


def assert_credit_amount_is_not_sourced_from_expected_maximum() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-credit-max-db-")) / "resolveflow.db"
    scenarios_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-credit-max-scenario-")) / "scenarios.json"
    scenario = {
        "scenario_id": "credit_max_not_tautological",
        "title": "Credit maximum should be independently checked",
        "customer_id": "CUST-1001",
        "customer_messages": ["My internet was down yesterday for hours. Am I eligible for a credit?"],
        "initial_state": {
            "db_reset_required": True,
            "scenario_date": "2026-05-24",
            "customer": {
                "customer_id": "CUST-1001",
                "account_status": "active",
                "plan_id": "fiber_plus_200",
                "risk_level": "high",
            },
            "seed_evidence": {"invoices": ["INV-8821"], "payments": [], "outages": ["OUT-CHN-04-20260520"]},
            "conversation_context": [],
        },
        "goal_state": {
            "expected_final_status": "resolved",
            "expected_intents": ["service_outage", "refund_request"],
            "issue_queue_order": ["service_outage", "refund_request"],
            "required_tools": ["lookup_customer", "check_outage_status", "retrieve_policy", "apply_credit"],
            "forbidden_tools": [],
            "required_policies": ["service_credit_policy"],
            "expected_artifacts": {
                "credit_required": True,
                "maximum_credit_inr": 100,
                "policy_dag": "service_credit_dag",
            },
            "success_criteria": ["The runner must not source credit amount from maximum_credit_inr."],
        },
    }
    scenarios_path.write_text(json.dumps([scenario]), encoding="utf-8")
    result = run_evaluation(k=1, scenarios_path=scenarios_path, db_path=db_path, use_live_agent=False)
    case = result["results"][0]

    amount = case["artifacts"]["tool_results"]["apply_credit"]["amount"]
    if amount != 500.0:
        raise AssertionError(f"observed outage credit amount should come from policy evidence: {case}")
    if case["passed"]:
        raise AssertionError(f"case should fail because observed credit exceeds expected max: {case}")
    if "persisted credit exceeds maximum_credit_inr 100" not in case["failures"]:
        raise AssertionError(f"max-credit DB failure missing: {case}")


def assert_expected_handoff_does_not_create_observed_handoff() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-handoff-db-")) / "resolveflow.db"
    scenarios_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-handoff-scenario-")) / "scenarios.json"
    scenario = {
        "scenario_id": "expected_handoff_is_not_observed",
        "title": "Expected handoff should not write audit observation",
        "customer_id": "CUST-1003",
        "customer_messages": ["Can you explain my latest broadband bill?"],
        "initial_state": {
            "db_reset_required": True,
            "scenario_date": "2026-05-24",
            "customer": {
                "customer_id": "CUST-1003",
                "account_status": "active",
                "plan_id": "fiber_work_500",
                "risk_level": "low",
            },
            "seed_evidence": {"invoices": ["INV-1003"], "payments": ["PAY-1003"], "outages": []},
            "conversation_context": [],
        },
        "goal_state": {
            "expected_final_status": "escalated",
            "expected_intents": ["billing_dispute"],
            "issue_queue_order": ["billing_dispute"],
            "required_tools": ["lookup_customer", "generate_handoff_summary"],
            "forbidden_tools": [],
            "required_policies": [],
            "expected_artifacts": {"handoff_required": True},
            "success_criteria": ["The scenario expectation alone must not mark audit handoff observed."],
        },
    }
    scenarios_path.write_text(json.dumps([scenario]), encoding="utf-8")
    result = run_evaluation(k=1, scenarios_path=scenarios_path, db_path=db_path, use_live_agent=False)
    case = result["results"][0]

    if case["passed"]:
        raise AssertionError(f"case should fail because observed agent did not handoff: {case}")
    if "generate_handoff_summary" in case["tools_called"]:
        raise AssertionError(f"agent unexpectedly executed handoff tool: {case}")
    if "expected handoff side effect missing" not in case["failures"]:
        raise AssertionError(f"missing handoff failure was not reported: {case}")


def assert_expected_audit_does_not_create_audit_row() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-audit-db-")) / "resolveflow.db"
    scenarios_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-audit-scenario-")) / "scenarios.json"
    scenario = {
        "scenario_id": "expected_audit_is_not_observed",
        "title": "Expected audit should not force audit row",
        "customer_id": "CUST-1003",
        "customer_messages": ["Can you explain my latest broadband bill?"],
        "initial_state": {
            "db_reset_required": True,
            "scenario_date": "2026-05-24",
            "customer": {
                "customer_id": "CUST-1003",
                "account_status": "active",
                "plan_id": "fiber_work_500",
                "risk_level": "low",
            },
            "seed_evidence": {"invoices": ["INV-1003"], "payments": ["PAY-1003"], "outages": []},
            "conversation_context": [],
        },
        "goal_state": {
            "expected_final_status": "resolved",
            "expected_intents": ["billing_dispute"],
            "issue_queue_order": ["billing_dispute"],
            "required_tools": ["lookup_customer", "get_invoice_history"],
            "forbidden_tools": [],
            "required_policies": [],
            "expected_artifacts": {"audit_log_required": True},
            "success_criteria": ["Expectation alone must not write audit log."],
        },
    }
    scenarios_path.write_text(json.dumps([scenario]), encoding="utf-8")
    result = run_evaluation(k=1, scenarios_path=scenarios_path, db_path=db_path, use_live_agent=False)
    case = result["results"][0]

    if case["passed"]:
        raise AssertionError(f"case should fail because no audit trail was observed: {case}")
    if case["artifacts"].get("audit_log"):
        raise AssertionError(f"audit artifact should not be created by expectation alone: {case}")
    if "expected persisted audit log missing" not in case["failures"]:
        raise AssertionError(f"missing audit failure was not reported: {case}")


def assert_missing_policy_dag_is_not_repaired_with_canned_context() -> None:
    db_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-dag-db-")) / "resolveflow.db"
    scenarios_path = Path(tempfile.mkdtemp(prefix="resolveflow-eval-dag-scenario-")) / "scenarios.json"
    scenario = {
        "scenario_id": "case_02_duplicate_charge",
        "title": "Missing observed DAG should not be repaired",
        "customer_id": "CUST-1001",
        "customer_messages": ["Can you explain my latest broadband bill?"],
        "initial_state": {
            "db_reset_required": True,
            "scenario_date": "2026-05-24",
            "customer": {
                "customer_id": "CUST-1001",
                "account_status": "active",
                "plan_id": "fiber_pro_200",
                "risk_level": "high",
            },
            "seed_evidence": {"invoices": ["INV-8821"], "payments": ["PAY-7711"], "outages": []},
            "conversation_context": [],
        },
        "goal_state": {
            "expected_final_status": "resolved",
            "expected_intents": ["billing_dispute"],
            "issue_queue_order": ["billing_dispute"],
            "required_tools": ["lookup_customer", "get_invoice_history"],
            "forbidden_tools": [],
            "required_policies": [],
            "expected_artifacts": {"policy_dag": "duplicate_charge_refund_dag"},
            "success_criteria": ["Scoring must require an observed policy path from a real tool call."],
        },
    }
    scenarios_path.write_text(json.dumps([scenario]), encoding="utf-8")
    result = run_evaluation(k=1, scenarios_path=scenarios_path, db_path=db_path, use_live_agent=False)
    case = result["results"][0]

    if case["passed"]:
        raise AssertionError(f"case should fail without an observed DAG path: {case}")
    if case["artifacts"].get("policy_paths"):
        raise AssertionError(f"scoring repaired policy paths from canned context: {case}")
    expected_failure = "policy DAG duplicate_charge_refund_dag did not traverse in observed tool path"
    if expected_failure not in case["failures"]:
        raise AssertionError(f"missing observed-DAG failure was not reported: {case}")


def assert_issue_queue_validation_requires_order_and_presence() -> None:
    if _issue_queue_order_failure(
        expected_order=["billing_dispute", "service_outage"],
        observed_queue=[],
        label="preserved issue queue",
    ) != "preserved issue queue is empty":
        raise AssertionError("empty observed queue should fail queue preservation")

    if _issue_queue_order_failure(
        expected_order=["billing_dispute", "service_outage"],
        observed_queue=["service_outage", "billing_dispute"],
        label="observed classifier queue",
    ) != "observed classifier queue order does not match expected issue queue":
        raise AssertionError("wrong queue order should fail even when membership matches")

    if _issue_queue_order_failure(
        expected_order=["billing_dispute", "service_outage"],
        observed_queue=["billing_dispute", "service_outage", "cancellation_intent"],
        label="observed classifier queue",
    ) is not None:
        raise AssertionError("matching expected prefix should pass queue validation")


def assert_rejects_invalid_pass_count() -> None:
    try:
        run_evaluation(k=0)
    except ValueError:
        pass
    else:
        raise AssertionError("k=0 should be rejected")


def main() -> None:
    assert_runs_pass_k_for_all_30_cases()
    assert_case_results_include_tool_and_reset_artifacts()
    assert_required_tools_are_rubric_not_execution_plan()
    assert_case_score_does_not_double_count_tool_failures()
    assert_live_agent_mode_observes_chat_stream_tools()
    assert_temperature_schedule_is_recorded_per_pass()
    assert_credit_amount_is_not_sourced_from_expected_maximum()
    assert_expected_handoff_does_not_create_observed_handoff()
    assert_expected_audit_does_not_create_audit_row()
    assert_missing_policy_dag_is_not_repaired_with_canned_context()
    assert_issue_queue_validation_requires_order_and_presence()
    assert_rejects_invalid_pass_count()
    print("evaluation runner tests passed")


if __name__ == "__main__":
    main()
