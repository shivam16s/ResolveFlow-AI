from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import (  # noqa: E402
    HandoffCustomerMessage,
    HandoffQueueEntry,
    detect_handoff_triggers,
    generate_handoff_customer_message,
)


def assert_generates_base_customer_message() -> None:
    result = generate_handoff_customer_message(
        customer_name="Riya",
        issue_summary="the duplicate charge review",
        estimated_wait="about 2 minutes",
    )

    if not isinstance(result, HandoffCustomerMessage):
        raise AssertionError(f"wrong message type: {result}")
    if result.message != (
        "Riya, I am connecting you to a specialist for the duplicate charge review. "
        "I will pass along the context, checks, and notes we already have so you do not need to repeat yourself. "
        "They will continue from here. Expected wait: about 2 minutes."
    ):
        raise AssertionError(f"customer message text wrong: {result.to_dict()}")
    if not result.includes_context_assurance:
        raise AssertionError(f"message must reassure context handoff: {result.to_dict()}")
    if result.handoff_id is not None or result.queue_status is not None:
        raise AssertionError(f"base message should not invent queue metadata: {result.to_dict()}")


def assert_generates_message_from_queue_entry_and_triggers() -> None:
    entry = HandoffQueueEntry(
        handoff_id="HND-123",
        case_id="case-001",
        customer_id="CUST-1001",
        context_card={
            "customer": {"name": "Rahul Sharma"},
            "issues_remaining": [
                {"intent": "service_outage", "label": "service outage"},
                {"intent": "refund_request", "label": "refund request"},
            ],
        },
        handoff_reason="Customer asked for a human.",
        status="waiting",
        created_at="2026-05-24T10:00:00",
        assigned_to=None,
        inserted=True,
    )
    detection = detect_handoff_triggers(
        health_score=24,
        refund_amount=750,
        user_message="I want a human agent.",
    )
    result = generate_handoff_customer_message(queue_entry=entry, trigger_detection=detection)

    if result.handoff_id != "HND-123" or result.queue_status != "waiting":
        raise AssertionError(f"queue metadata missing: {result.to_dict()}")
    if result.trigger_codes != ["score_below_30", "refund_over_500", "explicit_request"]:
        raise AssertionError(f"trigger codes missing: {result.to_dict()}")
    if "Rahul Sharma, I am connecting you to a specialist for service outage, refund request." not in result.message:
        raise AssertionError(f"queue context not reflected: {result.to_dict()}")
    if "do not need to repeat yourself" not in result.message:
        raise AssertionError(f"context assurance missing: {result.to_dict()}")


def assert_handles_dict_inputs_and_blank_context() -> None:
    result = generate_handoff_customer_message(
        queue_entry={"handoff_id": "HND-456", "status": "waiting", "context_card": {"customer": {"name": "   "}}},
        trigger_detection={"trigger_codes": ["anger", "anger", "tool_failure"]},
        customer_name=" ",
        issue_summary=" ",
    )

    if not result.message.startswith("I am connecting you to a specialist."):
        raise AssertionError(f"blank context should use neutral message: {result.to_dict()}")
    if result.trigger_codes != ["anger", "tool_failure"]:
        raise AssertionError(f"trigger codes should dedupe: {result.to_dict()}")
    if result.handoff_id != "HND-456" or result.queue_status != "waiting":
        raise AssertionError(f"dict queue metadata missing: {result.to_dict()}")


def main() -> None:
    assert_generates_base_customer_message()
    assert_generates_message_from_queue_entry_and_triggers()
    assert_handles_dict_inputs_and_blank_context()
    print("handoff customer message tests passed")


if __name__ == "__main__":
    main()
