from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

from .issue_queue import Issue, IssueQueue, build_issue_queue
from .slot_schema import generate_targeted_question


NEXT_ACTIONS = ("ANSWER", "ASK", "CALL_TOOL", "HANDOFF")

TOOL_BY_INTENT = {
    "billing_dispute": "get_invoice_history",
    "duplicate_charge": "check_duplicate_charge",
    "service_outage": "check_outage_status",
    "router_issue": "run_router_diagnostic",
    "plan_change": "change_plan",
    "cancellation_intent": "retrieve_policy",
    "refund_request": "retrieve_policy",
    "technician_request": "schedule_technician",
    "general_query": None,
}


@dataclass(frozen=True)
class NextActionDecision:
    action: str
    intent: str | None
    reason: str
    question: dict | None = None
    tool_name: str | None = None
    handoff_reason: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in NEXT_ACTIONS:
            raise ValueError(f"action must be one of {NEXT_ACTIONS}")
        if self.action == "ASK" and self.question is None:
            raise ValueError("ASK decisions must include question metadata")
        if self.action == "CALL_TOOL" and not self.tool_name:
            raise ValueError("CALL_TOOL decisions must include tool_name")
        if self.action == "HANDOFF" and not self.handoff_reason:
            raise ValueError("HANDOFF decisions must include handoff_reason")

    def to_dict(self) -> dict:
        return asdict(self)


def decide_next_action(
    issues: IssueQueue | Iterable[Issue] | Iterable[str],
    slots: dict[str, object] | None = None,
    *,
    handoff_requested: bool = False,
    health_score: float | None = None,
    tool_failure: bool = False,
    handoff_health_threshold: float = 30.0,
    ambiguity_detected: bool = False,
) -> NextActionDecision:
    if slots is not None and not isinstance(slots, dict):
        raise ValueError("slots must be a dict when provided")
    normalized_slots = slots or {}
    issue_queue = _coerce_issue_queue(issues)

    if handoff_requested:
        return NextActionDecision(
            action="HANDOFF",
            intent=issue_queue.current_issue.intent if issue_queue.current_issue else None,
            reason="handoff explicitly requested",
            handoff_reason="Customer explicitly requested a human agent.",
        )
    if tool_failure:
        return NextActionDecision(
            action="HANDOFF",
            intent=issue_queue.current_issue.intent if issue_queue.current_issue else None,
            reason="tool failure requires safe human handoff",
            handoff_reason="A required backend tool failed or returned unsafe uncertainty.",
        )
    if handoff_health_threshold < 0 or handoff_health_threshold > 100:
        raise ValueError("handoff_health_threshold must be between 0 and 100")
    if health_score is not None:
        numeric_health = float(health_score)
        if numeric_health < 0 or numeric_health > 100:
            raise ValueError("health_score must be between 0 and 100")
        if numeric_health < handoff_health_threshold:
            return NextActionDecision(
                action="HANDOFF",
                intent=issue_queue.current_issue.intent if issue_queue.current_issue else None,
                reason="conversation health below handoff threshold",
                handoff_reason=f"Conversation health dropped below {handoff_health_threshold:g}.",
                metadata={"health_score": numeric_health,
                          "threshold": handoff_health_threshold},
            )

    current_issue = issue_queue.current_issue
    if current_issue is None:
        return NextActionDecision(
            action="ANSWER",
            intent=None,
            reason="no pending issues remain",
        )

    if ambiguity_detected:
        return NextActionDecision(
            action="ASK",
            intent=current_issue.intent,
            reason="ambiguous customer report requires problem_description",
            question={
                "intent": current_issue.intent,
                "slot": "problem_description",
                "question": "What is the main problem you want me to fix first?",
                "value_type": "string",
                "priority": 1,
            },
            metadata={
                "status": current_issue.status,
                "priority": current_issue.priority,
                "ambiguity_detected": True,
                "queue": [issue.intent for issue in issue_queue],
            },
        )

    question = generate_targeted_question(
        current_issue.intent, normalized_slots)
    if question is not None:
        return NextActionDecision(
            action="ASK",
            intent=question.intent,
            reason=f"missing required slot {question.slot}",
            question=question.to_dict(),
            metadata={
                "status": current_issue.status,
                "priority": current_issue.priority,
                "queue": [issue.intent for issue in issue_queue],
            },
        )

    tool_name = TOOL_BY_INTENT.get(current_issue.intent)
    if tool_name is None:
        return NextActionDecision(
            action="ANSWER",
            intent=current_issue.intent,
            reason="required slots are complete and no backend tool is required",
        )

    return NextActionDecision(
        action="CALL_TOOL",
        intent=current_issue.intent,
        reason="required slots are complete for current issue",
        tool_name=tool_name,
        metadata={
            "status": current_issue.status,
            "priority": current_issue.priority,
            "queue": [issue.intent for issue in issue_queue],
        },
    )


def _coerce_issue_queue(issues: IssueQueue | Iterable[Issue] | Iterable[str]) -> IssueQueue:
    if isinstance(issues, IssueQueue):
        return issues
    issue_list = list(issues)
    if not issue_list:
        return build_issue_queue([])
    if all(isinstance(issue, Issue) for issue in issue_list):
        return IssueQueue(issue_list)
    return build_issue_queue([str(issue) for issue in issue_list])
