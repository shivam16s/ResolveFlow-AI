from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import IntentClassifier, build_issue_queue, slot_progress_for_issue  # noqa: E402


def test_build_issue_queue_orders_demo_intents() -> None:
    classifier = IntentClassifier()
    classification = classifier.classify(
        "I was charged twice this month and my internet is still not working. I want to cancel now."
    )
    queue = build_issue_queue(classification)

    assert [issue.intent for issue in queue] == [
        "duplicate_charge",
        "service_outage",
        "cancellation_intent",
    ]
    assert [issue.priority for issue in queue] == [1, 2, 3]
    assert all(issue.status == "pending" for issue in queue)
    assert queue.current_issue is queue[0]
    assert queue[0].required_slots == ["customer_id", "invoice_id"]


def test_build_issue_queue_keeps_distinct_billing_issue() -> None:
    classifier = IntentClassifier()
    classification = classifier.classify("Why is my bill so high and why is my internet down?")
    queue = build_issue_queue(classification)

    assert [issue.intent for issue in queue] == ["billing_dispute", "service_outage"]
    assert queue[0].required_slots == ["customer_id"]
    assert queue[1].required_slots == ["customer_id", "location"]


def test_issue_queue_json_shape() -> None:
    queue = build_issue_queue(["technician_request", "service_outage", "service_outage"])
    payload = json.loads(queue.to_json())

    assert payload == [
        {
            "intent": "service_outage",
            "priority": 1,
            "required_slots": ["customer_id", "location"],
            "resolution": None,
            "status": "pending",
            "tools_called": [],
        },
        {
            "intent": "technician_request",
            "priority": 2,
            "required_slots": ["customer_id", "time_slot"],
            "resolution": None,
            "status": "pending",
            "tools_called": [],
        },
    ]


def test_issue_queue_tracks_slot_progress() -> None:
    queue = build_issue_queue(["duplicate_charge", "service_outage"])
    slots = {"customer_id": "CUST-1001", "invoice_id": "INV-8821", "location": ""}
    progress = queue.slot_progress(slots)

    assert progress[0].to_dict() == {
        "intent": "duplicate_charge",
        "required_slots": ["customer_id", "invoice_id"],
        "filled_slots": {"customer_id": "CUST-1001", "invoice_id": "INV-8821"},
        "missing_slots": [],
        "is_complete": True,
    }
    assert progress[1].missing_slots == ["location"]
    assert progress[1].is_complete is False
    assert queue.next_missing_slot(slots) == ("service_outage", "location")

    single = slot_progress_for_issue(queue[0], {"customer_id": "CUST-1001"})
    assert single.missing_slots == ["invoice_id"]


def test_issue_queue_uses_schema_missing_slot_detection() -> None:
    queue = build_issue_queue(["refund_request"])
    progress = queue.slot_progress({"customer_id": "CUST-1001", "amount": "", "reason": "duplicate payment"})

    assert progress[0].filled_slots == {"customer_id": "CUST-1001", "reason": "duplicate payment"}
    assert progress[0].missing_slots == ["amount"]
    assert queue.next_missing_slot({"customer_id": "CUST-1001", "amount": "", "reason": "duplicate payment"}) == (
        "refund_request",
        "amount",
    )


def test_issue_queue_next_missing_slot_uses_prioritized_slot() -> None:
    refund_queue = build_issue_queue(["refund_request"])
    assert refund_queue.next_missing_slot({"amount": 300, "reason": "duplicate payment"}) == (
        "refund_request",
        "customer_id",
    )
    assert refund_queue.next_missing_slot({"customer_id": "CUST-1001", "amount": "", "reason": "duplicate payment"}) == (
        "refund_request",
        "amount",
    )

    queue = build_issue_queue(["refund_request", "service_outage"])
    assert queue.next_missing_slot({"customer_id": "CUST-1001", "amount": 300, "reason": "duplicate payment"}) == (
        "service_outage",
        "location",
    )
    assert (
        queue.next_missing_slot(
            {
                "customer_id": "CUST-1001",
                "amount": 300,
                "reason": "duplicate payment",
                "location": "Chennai Zone-04",
            }
        )
        is None
    )


def test_issue_queue_generates_targeted_question_for_current_missing_slot() -> None:
    queue = build_issue_queue(["duplicate_charge", "service_outage"])
    question = queue.targeted_question({"customer_id": "CUST-1001", "location": ""})
    assert question == {
        "intent": "duplicate_charge",
        "slot": "invoice_id",
        "question": "Which invoice shows the duplicate charge?",
        "value_type": "string",
        "priority": 2,
    }

    next_question = queue.targeted_question(
        {"customer_id": "CUST-1001", "invoice_id": "INV-8821", "location": ""}
    )
    assert next_question == {
        "intent": "service_outage",
        "slot": "location",
        "question": "Which service location or area is affected?",
        "value_type": "string",
        "priority": 2,
    }

    assert queue.targeted_question(
        {"customer_id": "CUST-1001", "invoice_id": "INV-8821", "location": "Chennai Zone-04"}
    ) is None


def main() -> None:
    test_build_issue_queue_orders_demo_intents()
    test_build_issue_queue_keeps_distinct_billing_issue()
    test_issue_queue_json_shape()
    test_issue_queue_tracks_slot_progress()
    test_issue_queue_uses_schema_missing_slot_detection()
    test_issue_queue_next_missing_slot_uses_prioritized_slot()
    test_issue_queue_generates_targeted_question_for_current_missing_slot()
    print("PASS issue queue priority tests")


if __name__ == "__main__":
    main()
