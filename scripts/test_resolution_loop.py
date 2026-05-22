from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import (  # noqa: E402
    IntentClassifier,
    Issue,
    IssueResolution,
    SequentialResolutionLoop,
    build_issue_queue,
)


def test_resolution_loop_processes_one_issue_at_a_time_in_priority_order() -> None:
    classifier = IntentClassifier()
    classification = classifier.classify(
        "I was charged twice this month and my internet is still not working. I want to cancel now."
    )
    queue = build_issue_queue(classification)
    seen: list[tuple[str, str]] = []

    def resolver(issue: Issue) -> IssueResolution:
        assert issue.status == "in_progress"
        seen.append((issue.intent, issue.status))
        return IssueResolution(
            status="resolved",
            resolution=f"resolved {issue.intent}",
            tools_called=[f"tool_for_{issue.intent}"],
        )

    run = SequentialResolutionLoop(resolver).run(queue)

    assert seen == [
        ("duplicate_charge", "in_progress"),
        ("service_outage", "in_progress"),
        ("cancellation_intent", "in_progress"),
    ]
    assert [issue.status for issue in queue] == ["resolved", "resolved", "resolved"]
    assert [issue.resolution for issue in queue] == [
        "resolved duplicate_charge",
        "resolved service_outage",
        "resolved cancellation_intent",
    ]
    assert [transition.to_status for transition in run.transitions] == [
        "in_progress",
        "resolved",
        "in_progress",
        "resolved",
        "in_progress",
        "resolved",
    ]
    assert run.completed is True
    assert len(run.resolved_issues) == 3
    assert len(run.escalated_issues) == 0


def test_resolution_loop_marks_escalated_issue_and_continues() -> None:
    queue = build_issue_queue(["service_outage", "refund_request"])

    def resolver(issue: Issue) -> IssueResolution:
        if issue.intent == "service_outage":
            return IssueResolution(
                status="escalated",
                resolution="outage tool failed; human review required",
                tools_called=["check_outage_status"],
            )
        return IssueResolution(status="resolved", resolution="refund path explained")

    run = SequentialResolutionLoop(resolver).run(queue)

    assert [issue.status for issue in queue] == ["escalated", "resolved"]
    assert queue[0].tools_called == ["check_outage_status"]
    assert run.completed is True
    assert [issue.intent for issue in run.escalated_issues] == ["service_outage"]


def test_resolution_loop_can_stop_on_escalation() -> None:
    queue = build_issue_queue(["service_outage", "refund_request"])

    def resolver(_: Issue) -> IssueResolution:
        return IssueResolution(status="escalated", resolution="handoff now")

    run = SequentialResolutionLoop(resolver, stop_on_escalation=True).run(queue)

    assert [issue.status for issue in queue] == ["escalated", "pending"]
    assert run.completed is False


def test_resolution_loop_turns_resolver_exception_into_escalation() -> None:
    queue = build_issue_queue(["service_outage"])

    def resolver(_: Issue) -> IssueResolution:
        raise RuntimeError("diagnostic backend unavailable")

    run = SequentialResolutionLoop(resolver).run(queue)

    assert queue[0].status == "escalated"
    assert "diagnostic backend unavailable" in (queue[0].resolution or "")
    assert run.completed is True


def main() -> None:
    test_resolution_loop_processes_one_issue_at_a_time_in_priority_order()
    test_resolution_loop_marks_escalated_issue_and_continues()
    test_resolution_loop_can_stop_on_escalation()
    test_resolution_loop_turns_resolver_exception_into_escalation()
    print("PASS sequential resolution loop tests")


if __name__ == "__main__":
    main()
