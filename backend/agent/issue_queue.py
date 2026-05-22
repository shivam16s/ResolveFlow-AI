from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Iterable

from .intent_classifier import INTENT_PRIORITY, IntentClassification


IssueStatus = str


REQUIRED_SLOTS = {
    "billing_dispute": ["customer_id"],
    "duplicate_charge": ["customer_id", "invoice_id"],
    "service_outage": ["customer_id", "location"],
    "router_issue": ["customer_id"],
    "plan_change": ["customer_id", "requested_plan_id"],
    "cancellation_intent": ["customer_id"],
    "refund_request": ["customer_id", "amount", "reason"],
    "technician_request": ["customer_id", "time_slot"],
    "general_query": ["customer_id"],
}


@dataclass
class Issue:
    intent: str
    status: IssueStatus
    priority: int
    required_slots: list[str]
    tools_called: list[str] = field(default_factory=list)
    resolution: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class IssueQueue:
    def __init__(self, issues: Iterable[Issue]) -> None:
        self.issues = list(issues)

    def __iter__(self):
        return iter(self.issues)

    def __len__(self) -> int:
        return len(self.issues)

    def __getitem__(self, index: int) -> Issue:
        return self.issues[index]

    @property
    def current_issue(self) -> Issue | None:
        for issue in self.issues:
            if issue.status in {"pending", "in_progress"}:
                return issue
        return None

    def to_list(self) -> list[dict]:
        return [issue.to_dict() for issue in self.issues]

    def to_json(self) -> str:
        return json.dumps(self.to_list(), sort_keys=True)


def build_issue_queue(classification: IntentClassification | Iterable[str]) -> IssueQueue:
    intents = classification.intents if isinstance(classification, IntentClassification) else list(classification)
    normalized_intents = _normalize_queue_intents(intents)
    issues = [
        Issue(
            intent=intent,
            status="pending",
            priority=index + 1,
            required_slots=list(REQUIRED_SLOTS[intent]),
        )
        for index, intent in enumerate(normalized_intents)
    ]
    return IssueQueue(issues)


def _normalize_queue_intents(intents: Iterable[str]) -> list[str]:
    unique_intents = []
    seen = set()
    for intent in intents:
        if intent in seen:
            continue
        seen.add(intent)
        unique_intents.append(intent)

    if "duplicate_charge" in unique_intents:
        unique_intents = [intent for intent in unique_intents if intent != "billing_dispute"]

    if not unique_intents:
        unique_intents = ["general_query"]

    return sorted(unique_intents, key=lambda intent: INTENT_PRIORITY[intent])
