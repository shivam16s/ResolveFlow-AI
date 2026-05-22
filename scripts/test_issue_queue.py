from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import IntentClassifier, build_issue_queue  # noqa: E402


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


def main() -> None:
    test_build_issue_queue_orders_demo_intents()
    test_build_issue_queue_keeps_distinct_billing_issue()
    test_issue_queue_json_shape()
    print("PASS issue queue priority tests")


if __name__ == "__main__":
    main()
