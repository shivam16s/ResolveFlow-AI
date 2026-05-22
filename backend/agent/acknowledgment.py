from __future__ import annotations

from .issue_queue import IssueQueue


ISSUE_LABELS = {
    "billing_dispute": "a billing issue",
    "duplicate_charge": "a possible duplicate charge",
    "service_outage": "a service problem",
    "router_issue": "a router or signal issue",
    "plan_change": "a plan change request",
    "cancellation_intent": "that you are thinking about cancelling",
    "refund_request": "a refund or credit request",
    "technician_request": "a technician visit request",
    "general_query": "your question",
}


def generate_acknowledgment(issue_queue: IssueQueue) -> str:
    labels = [ISSUE_LABELS[issue.intent] for issue in issue_queue]

    if not labels:
        return "I can help with that. Let me check the details and handle it step by step."

    concern_word = "concern" if len(labels) == 1 else "concerns"
    return (
        f"I can see { _count_phrase(len(labels)) } {concern_word}: "
        f"{_join_labels(labels)}. Let me address each one step by step."
    )


def _count_phrase(count: int) -> str:
    words = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
    }
    return words.get(count, str(count))


def _join_labels(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"
