from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Iterable

from .intent_classifier import INTENT_PRIORITY, IntentClassification
from .slot_schema import (
    REQUIRED_SLOTS,
    _slot_value_present,
    detect_missing_required_slots,
    generate_targeted_question,
    prioritize_slot,
)


IssueStatus = str


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


@dataclass(frozen=True)
class SlotProgress:
    intent: str
    required_slots: list[str]
    filled_slots: dict[str, object]
    missing_slots: list[str]
    is_complete: bool

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

    def slot_progress(self, slots: dict[str, object] | None = None) -> list[SlotProgress]:
        slots = slots or {}
        return [slot_progress_for_issue(issue, slots) for issue in self.issues]

    def next_missing_slot(self, slots: dict[str, object] | None = None) -> tuple[str, str] | None:
        slots = slots or {}
        for issue in self.issues:
            prioritized = prioritize_slot(issue.intent, slots)
            if prioritized is not None:
                return prioritized.intent, prioritized.slot
        return None

    def targeted_question(self, slots: dict[str, object] | None = None) -> dict | None:
        slots = slots or {}
        for issue in self.issues:
            question = generate_targeted_question(issue.intent, slots)
            if question is not None:
                return question.to_dict()
        return None


def build_issue_queue(classification: IntentClassification | Iterable[str]) -> IssueQueue:
    intents = classification.intents if isinstance(
        classification, IntentClassification) else list(classification)
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


def slot_progress_for_issue(issue: Issue, slots: dict[str, object] | None = None) -> SlotProgress:
    slots = slots or {}
    filled_slots = {
        slot: slots[slot]
        for slot in issue.required_slots
        if _slot_has_value(slots.get(slot))
    }
    missing_slots = [
        missing.slot for missing in detect_missing_required_slots(issue.intent, slots)]
    return SlotProgress(
        intent=issue.intent,
        required_slots=list(issue.required_slots),
        filled_slots=filled_slots,
        missing_slots=missing_slots,
        is_complete=not missing_slots,
    )


def _normalize_queue_intents(intents: Iterable[str]) -> list[str]:
    unique_intents = []
    seen = set()
    for raw_intent in intents:
        intent = _coerce_queue_intent(raw_intent)
        if intent in seen:
            continue
        seen.add(intent)
        unique_intents.append(intent)

    if "duplicate_charge" in unique_intents:
        unique_intents = [
            intent for intent in unique_intents if intent != "billing_dispute"]

    if not unique_intents:
        unique_intents = ["general_query"]

    return sorted(unique_intents, key=lambda intent: INTENT_PRIORITY[intent])


def _coerce_queue_intent(intent: object) -> str:
    normalized = str(intent or "").strip()
    if normalized in REQUIRED_SLOTS and normalized in INTENT_PRIORITY:
        return normalized
    return "general_query"


def _slot_has_value(value: object) -> bool:
    return _slot_value_present(value)
