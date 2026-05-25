from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import IntentClassifier, build_issue_queue, generate_acknowledgment  # noqa: E402


def test_acknowledgment_covers_demo_issues_in_queue_order() -> None:
    classifier = IntentClassifier()
    classification = classifier.classify(
        "I was charged twice this month and my internet is still not working. Honestly, I want to cancel now."
    )
    queue = build_issue_queue(classification)
    response = generate_acknowledgment(queue)

    assert response == (
        "I can see three concerns: a possible duplicate charge, a service outage, "
        "and that you are thinking about cancelling. Let me address each one step by step."
    )


def test_acknowledgment_covers_two_distinct_issues() -> None:
    queue = build_issue_queue(["service_outage", "technician_request"])
    response = generate_acknowledgment(queue)

    assert "two concerns" in response
    assert "a service outage and a technician visit request" in response


def test_acknowledgment_covers_single_issue() -> None:
    queue = build_issue_queue(["plan_change"])
    response = generate_acknowledgment(queue)

    assert response == (
        "I can see one concern: a plan change request. "
        "Let me address each one step by step."
    )


def main() -> None:
    test_acknowledgment_covers_demo_issues_in_queue_order()
    test_acknowledgment_covers_two_distinct_issues()
    test_acknowledgment_covers_single_issue()
    print("PASS acknowledgment response tests")


if __name__ == "__main__":
    main()
