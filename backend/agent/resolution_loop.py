from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

from .issue_queue import Issue, IssueQueue


@dataclass(frozen=True)
class IssueResolution:
    status: str
    resolution: str
    tools_called: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in {"resolved", "escalated"}:
            raise ValueError("IssueResolution.status must be 'resolved' or 'escalated'")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class IssueTransition:
    intent: str
    from_status: str
    to_status: str
    priority: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResolutionRun:
    issue_queue: IssueQueue
    transitions: list[IssueTransition]

    @property
    def resolved_issues(self) -> list[Issue]:
        return [issue for issue in self.issue_queue if issue.status == "resolved"]

    @property
    def escalated_issues(self) -> list[Issue]:
        return [issue for issue in self.issue_queue if issue.status == "escalated"]

    @property
    def completed(self) -> bool:
        return self.issue_queue.current_issue is None

    def to_dict(self) -> dict:
        return {
            "issue_queue": self.issue_queue.to_list(),
            "transitions": [transition.to_dict() for transition in self.transitions],
            "completed": self.completed,
        }


IssueResolver = Callable[[Issue], IssueResolution]


class SequentialResolutionLoop:
    """Runs one issue at a time from an IssueQueue."""

    def __init__(self, resolver: IssueResolver, stop_on_escalation: bool = False) -> None:
        self.resolver = resolver
        self.stop_on_escalation = stop_on_escalation

    def run(self, issue_queue: IssueQueue) -> ResolutionRun:
        transitions: list[IssueTransition] = []

        for issue in issue_queue:
            if issue.status != "pending":
                continue

            self._transition(issue, "in_progress", transitions)
            resolution = self._resolve(issue)
            issue.tools_called.extend(resolution.tools_called)
            issue.resolution = resolution.resolution
            self._transition(issue, resolution.status, transitions)

            if resolution.status == "escalated" and self.stop_on_escalation:
                break

        return ResolutionRun(issue_queue=issue_queue, transitions=transitions)

    def _resolve(self, issue: Issue) -> IssueResolution:
        try:
            return self.resolver(issue)
        except Exception as exc:
            return IssueResolution(
                status="escalated",
                resolution=f"Resolver failed for {issue.intent}: {exc}",
            )

    @staticmethod
    def _transition(issue: Issue, to_status: str, transitions: list[IssueTransition]) -> None:
        transitions.append(
            IssueTransition(
                intent=issue.intent,
                from_status=issue.status,
                to_status=to_status,
                priority=issue.priority,
            )
        )
        issue.status = to_status
