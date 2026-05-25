from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import HandoffTrigger, HandoffTriggerDetection, detect_handoff_triggers  # noqa: E402
from backend.agent.health import loop_penalty_component, sentiment_score_component  # noqa: E402


def assert_detects_all_eight_handoff_triggers() -> None:
    detection = detect_handoff_triggers(
        policy_result={
            "policy_name": "refund_exception_dag",
            "policy_status": "needs_review",
            "action": "handoff_human",
            "reason": "Refund exception needs manual approval.",
        },
        health_score=24.5,
        sentiment=sentiment_score_component([{"role": "user", "content": "This is terrible and I am angry."}]),
        loop_penalty=loop_penalty_component(
            [
                {"role": "user", "content": "Can you fix this?"},
                {"role": "user", "content": "Can you fix this"},
                {"role": "user", "content": "can you fix this???"},
            ]
        ),
        churn_score=0.82,
        tool_calls=[
            {"tool_name": "lookup_customer", "status": "ok"},
            {"tool_name": "retrieve_policy", "status": "failed", "error": "timeout"},
        ],
        refund_amount=750,
        user_message="I want a human agent now.",
    )

    if not isinstance(detection, HandoffTriggerDetection):
        raise AssertionError(f"wrong detection type: {detection}")
    if not detection.should_handoff:
        raise AssertionError(f"handoff should be required: {detection.to_dict()}")
    expected_codes = [
        "policy_exception",
        "score_below_30",
        "anger",
        "loop",
        "churn_risk",
        "tool_failure",
        "refund_over_500",
        "explicit_request",
    ]
    if detection.trigger_codes != expected_codes:
        raise AssertionError(f"trigger order/codes wrong: {detection.to_dict()}")
    if detection.highest_severity != "critical":
        raise AssertionError(f"highest severity wrong: {detection.to_dict()}")
    if not all(isinstance(trigger, HandoffTrigger) for trigger in detection.triggers):
        raise AssertionError(f"triggers should be dataclasses: {detection.to_dict()}")


def assert_accepts_raw_runtime_artifacts() -> None:
    detection = detect_handoff_triggers(
        policy_result={"blocked": True, "reason": "Prerequisite DAG node was not visited."},
        health_score={"score": 29},
        messages=[
            {"role": "user", "content": "Why is this still broken?"},
            {"role": "assistant", "content": "Checking."},
            {"role": "user", "content": "Why is this still broken"},
            {"role": "assistant", "content": "Still checking."},
            {"role": "user", "content": "why is this still broken???"},
        ],
        churn_score={"score": 0.7},
        tool_calls=[{"name": "check_outage_status", "ok": False}],
        refund_amount="INR 501",
        handoff_requested=True,
    )

    if detection.trigger_codes != [
        "policy_exception",
        "score_below_30",
        "loop",
        "churn_risk",
        "tool_failure",
        "refund_over_500",
        "explicit_request",
    ]:
        raise AssertionError(f"raw runtime trigger extraction wrong: {detection.to_dict()}")
    failure = next(trigger for trigger in detection.triggers if trigger.code == "tool_failure")
    if failure.evidence["failed_tools"][0]["name"] != "check_outage_status":
        raise AssertionError(f"failed tool evidence wrong: {detection.to_dict()}")


def assert_no_trigger_when_under_thresholds() -> None:
    detection = detect_handoff_triggers(
        policy_result={"policy_status": "compliant", "action": "apply_credit"},
        health_score=72,
        sentiment={"label": "calm", "score": 0.85},
        loop_penalty={"value": 0.5, "repeated_question_count": 2},
        churn_score=0.69,
        tool_calls=[{"tool_name": "lookup_customer", "status": "ok"}],
        refund_amount=500,
        user_message="Please continue.",
    )

    if detection.should_handoff or detection.trigger_codes:
        raise AssertionError(f"no triggers expected: {detection.to_dict()}")
    if detection.highest_severity is not None:
        raise AssertionError(f"highest severity should be empty: {detection.to_dict()}")


def assert_validates_bad_inputs() -> None:
    bad_calls = (
        lambda: detect_handoff_triggers(health_score=-1),
        lambda: detect_handoff_triggers(health_score=101),
        lambda: detect_handoff_triggers(sentiment={"score": 1.2}),
        lambda: detect_handoff_triggers(churn_score=1.1),
        lambda: detect_handoff_triggers(refund_amount=-5),
        lambda: detect_handoff_triggers(tool_calls="retrieve_policy"),
    )
    for bad_call in bad_calls:
        try:
            bad_call()
        except ValueError:
            pass
        else:
            raise AssertionError("bad handoff trigger input was accepted")


def main() -> None:
    assert_detects_all_eight_handoff_triggers()
    assert_accepts_raw_runtime_artifacts()
    assert_no_trigger_when_under_thresholds()
    assert_validates_bad_inputs()
    print("handoff trigger detection tests passed")


if __name__ == "__main__":
    main()
