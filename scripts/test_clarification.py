from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import NextActionDecision, build_issue_queue, decide_next_action  # noqa: E402


def assert_asks_for_current_issue_missing_slot() -> None:
    queue = build_issue_queue(["duplicate_charge", "service_outage"])
    decision = decide_next_action(queue, {"customer_id": "CUST-1001", "location": "Chennai Zone-04"})

    if not isinstance(decision, NextActionDecision):
        raise AssertionError(f"wrong decision type: {decision}")
    if decision.action != "ASK":
        raise AssertionError(f"expected ASK: {decision.to_dict()}")
    if decision.intent != "duplicate_charge":
        raise AssertionError(f"should ask for current issue before later issue: {decision.to_dict()}")
    if decision.question != {
        "intent": "duplicate_charge",
        "slot": "invoice_id",
        "question": "Which invoice shows the duplicate charge?",
        "value_type": "string",
        "priority": 2,
    }:
        raise AssertionError(f"wrong targeted question: {decision.to_dict()}")


def assert_calls_tool_when_required_slots_are_complete() -> None:
    decision = decide_next_action(
        ["duplicate_charge", "service_outage"],
        {"customer_id": "CUST-1001", "invoice_id": "INV-8821", "location": ""},
    )

    if decision.action != "CALL_TOOL":
        raise AssertionError(f"expected CALL_TOOL: {decision.to_dict()}")
    if decision.intent != "duplicate_charge" or decision.tool_name != "check_duplicate_charge":
        raise AssertionError(f"wrong tool routing: {decision.to_dict()}")
    if decision.metadata["priority"] != 1:
        raise AssertionError(f"current issue priority should be carried: {decision.to_dict()}")


def assert_asks_for_problem_description_on_ambiguous_report() -> None:
    decision = decide_next_action(
        ["service_outage", "billing_dispute"],
        {"customer_id": "CUST-1008", "location": "Chennai Zone-08"},
        ambiguity_detected=True,
    )

    if decision.action != "ASK":
        raise AssertionError(f"ambiguous report should ask: {decision.to_dict()}")
    if decision.question["slot"] != "problem_description":
        raise AssertionError(f"should ask for problem_description: {decision.to_dict()}")
    if not decision.metadata.get("ambiguity_detected"):
        raise AssertionError(f"ambiguity metadata missing: {decision.to_dict()}")


def assert_answers_when_no_tool_is_needed() -> None:
    missing_customer = decide_next_action(["general_query"], {})
    if missing_customer.action != "ASK" or missing_customer.question["slot"] != "customer_id":
        raise AssertionError(f"general query still needs customer ID: {missing_customer.to_dict()}")

    decision = decide_next_action(["general_query"], {"customer_id": "CUST-1001"})
    if decision.action != "ANSWER":
        raise AssertionError(f"expected ANSWER: {decision.to_dict()}")
    if decision.tool_name is not None:
        raise AssertionError(f"ANSWER should not include tool_name: {decision.to_dict()}")


def assert_handoff_preempts_ask_and_tool() -> None:
    requested = decide_next_action(["duplicate_charge"], {}, handoff_requested=True)
    if requested.action != "HANDOFF" or "human agent" not in requested.handoff_reason:
        raise AssertionError(f"handoff request should preempt missing slots: {requested.to_dict()}")

    failed = decide_next_action(
        ["duplicate_charge"],
        {"customer_id": "CUST-1001", "invoice_id": "INV-8821"},
        tool_failure=True,
    )
    if failed.action != "HANDOFF" or "backend tool failed" not in failed.handoff_reason:
        raise AssertionError(f"tool failure should preempt tool call: {failed.to_dict()}")

    unhealthy = decide_next_action(["service_outage"], {}, health_score=24.5, handoff_health_threshold=30)
    if unhealthy.action != "HANDOFF":
        raise AssertionError(f"low health should hand off: {unhealthy.to_dict()}")
    if unhealthy.metadata != {"health_score": 24.5, "threshold": 30}:
        raise AssertionError(f"health metadata wrong: {unhealthy.to_dict()}")


def assert_uses_next_unresolved_issue() -> None:
    queue = build_issue_queue(["duplicate_charge", "service_outage"])
    queue[0].status = "resolved"
    decision = decide_next_action(
        queue,
        {"customer_id": "CUST-1001", "invoice_id": "INV-8821", "location": "Chennai Zone-04"},
    )

    if decision.action != "CALL_TOOL":
        raise AssertionError(f"expected CALL_TOOL for next unresolved issue: {decision.to_dict()}")
    if decision.intent != "service_outage" or decision.tool_name != "check_outage_status":
        raise AssertionError(f"wrong next issue routing: {decision.to_dict()}")

    queue[1].status = "resolved"
    done = decide_next_action(queue, {})
    if done.action != "ANSWER" or done.intent is not None:
        raise AssertionError(f"resolved queue should answer: {done.to_dict()}")


def assert_validates_inputs() -> None:
    invalid_calls = (
        lambda: decide_next_action(["duplicate_charge"], []),
        lambda: decide_next_action(["duplicate_charge"], {}, health_score=-1),
        lambda: decide_next_action(["duplicate_charge"], {}, health_score=101),
        lambda: decide_next_action(["duplicate_charge"], {}, handoff_health_threshold=-1),
        lambda: decide_next_action(["duplicate_charge"], {}, handoff_health_threshold=101),
    )
    for invalid_call in invalid_calls:
        try:
            invalid_call()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid decide_next_action input was accepted")


def main() -> None:
    assert_asks_for_current_issue_missing_slot()
    assert_calls_tool_when_required_slots_are_complete()
    assert_asks_for_problem_description_on_ambiguous_report()
    assert_answers_when_no_tool_is_needed()
    assert_handoff_preempts_ask_and_tool()
    assert_uses_next_unresolved_issue()
    assert_validates_inputs()
    print("clarification decision tests passed")


if __name__ == "__main__":
    main()
