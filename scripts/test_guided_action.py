from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent import (  # noqa: E402
    ACTION_TO_TOOL_MAP,
    DEFAULT_ACTION_INSTRUCTIONS,
    MAX_ATTEMPTS,
    VALID_STATE_TRANSITIONS,
    GuidedActionAuditEvent,
    GuidedActionCoordinator,
    GuidedActionHandoff,
    GuidedActionInstruction,
    GuidedActionState,
    GuidedActionTool,
    GuidedActionTransition,
    GuidedActionVerification,
    guided_action_tool_map,
    resolve_action_tool,
)


def assert_state_enum_contract() -> None:
    expected = ["IDLE", "WAITING", "VERIFYING", "RESOLVED", "FAILED", "ESCALATED"]
    actual = [state.value for state in GuidedActionState]
    if actual != expected:
        raise AssertionError(f"guided action states changed: {actual}")

    if GuidedActionState.WAITING not in VALID_STATE_TRANSITIONS[GuidedActionState.IDLE]:
        raise AssertionError("IDLE must be able to enter WAITING")
    if GuidedActionState.VERIFYING not in VALID_STATE_TRANSITIONS[GuidedActionState.WAITING]:
        raise AssertionError("WAITING must be able to enter VERIFYING")
    if GuidedActionState.RESOLVED not in VALID_STATE_TRANSITIONS[GuidedActionState.VERIFYING]:
        raise AssertionError("VERIFYING must be able to resolve")
    if GuidedActionState.ESCALATED not in VALID_STATE_TRANSITIONS[GuidedActionState.FAILED]:
        raise AssertionError("FAILED must be able to escalate")
    if GuidedActionState.WAITING not in VALID_STATE_TRANSITIONS[GuidedActionState.FAILED]:
        raise AssertionError("FAILED must be able to retry by entering WAITING")
    if MAX_ATTEMPTS != 2:
        raise AssertionError(f"guided actions must allow exactly two attempts: {MAX_ATTEMPTS}")
    if ACTION_TO_TOOL_MAP["router_reset"] != "run_router_diagnostic":
        raise AssertionError(f"router_reset mapping wrong: {ACTION_TO_TOOL_MAP}")


def assert_coordinator_initializes_and_serializes() -> None:
    coordinator = GuidedActionCoordinator(
        action_name="router_reset",
        customer_id="CUST-1001",
        context={"tool_name": "run_router_diagnostic"},
    )

    payload = coordinator.to_dict()
    if payload != {
        "action_name": "router_reset",
        "customer_id": "CUST-1001",
        "state": "IDLE",
        "attempt_count": 0,
        "max_attempts": 2,
        "attempts_remaining": 2,
        "can_retry": False,
        "context": {"tool_name": "run_router_diagnostic"},
        "current_instruction": None,
        "last_verification": None,
        "handoff": None,
        "is_terminal": False,
        "transition_history": [],
        "audit_events": [],
    }:
        raise AssertionError(f"initial coordinator payload wrong: {payload}")


def assert_valid_transitions_are_recorded() -> None:
    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001", state="idle")
    waiting = coordinator.transition(
        GuidedActionState.WAITING,
        "instruction sent",
        metadata={"instruction_id": "guide-001"},
        timestamp="2026-05-24T10:00:00+00:00",
    )
    if not isinstance(waiting, GuidedActionTransition):
        raise AssertionError(f"transition type wrong: {waiting}")
    if coordinator.state != GuidedActionState.WAITING:
        raise AssertionError(f"state should be WAITING: {coordinator.state}")
    if waiting.to_dict() != {
        "from_state": "IDLE",
        "to_state": "WAITING",
        "reason": "instruction sent",
        "timestamp": "2026-05-24T10:00:00+00:00",
        "metadata": {"instruction_id": "guide-001"},
    }:
        raise AssertionError(f"transition payload wrong: {waiting.to_dict()}")

    coordinator.transition("VERIFYING", "customer reported completion")
    coordinator.transition("RESOLVED", "verification passed", metadata={"download_mbps": 183})
    if not coordinator.is_terminal:
        raise AssertionError("RESOLVED should be terminal")
    if coordinator.to_dict()["transition_history"][-1]["metadata"] != {"download_mbps": 183}:
        raise AssertionError(f"verification metadata missing: {coordinator.to_dict()}")
    if len(coordinator.audit_events) != 3:
        raise AssertionError(f"manual transitions should be audited: {coordinator.to_dict()}")
    if not isinstance(coordinator.audit_events[-1], GuidedActionAuditEvent):
        raise AssertionError(f"audit event type wrong: {coordinator.audit_events[-1]}")
    if coordinator.audit_events[-1].to_dict()["to_state"] != "RESOLVED":
        raise AssertionError(f"resolved transition audit missing: {coordinator.audit_events[-1].to_dict()}")


def assert_audit_sink_receives_every_state_transition() -> None:
    audit_payloads = []

    def audit_sink(event: dict) -> None:
        audit_payloads.append(event)

    def still_bad(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "customer_found": True,
            "diagnostic_failure": True,
            "needs_technician": True,
            "account_active": True,
        }

    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001", audit_sink=audit_sink)
    coordinator.instruct(timestamp="2026-05-24T10:01:00+00:00")
    coordinator.handle_user_report("done", still_bad, tool_name="run_router_diagnostic")

    if len(audit_payloads) != 3:
        raise AssertionError(f"each transition should emit one audit payload: {audit_payloads}")
    if [event["to_state"] for event in audit_payloads] != ["WAITING", "VERIFYING", "FAILED"]:
        raise AssertionError(f"audit transition order wrong: {audit_payloads}")
    if len(coordinator.audit_events) != len(coordinator.transition_history):
        raise AssertionError(f"audit event count should match transition count: {coordinator.to_dict()}")
    first = audit_payloads[0]
    if not first["event_id"].startswith("GAUD-"):
        raise AssertionError(f"audit event id missing: {first}")
    if first["action_name"] != "router_reset" or first["customer_id"] != "CUST-1001":
        raise AssertionError(f"audit identity wrong: {first}")
    if first["from_state"] != "IDLE" or first["to_state"] != "WAITING":
        raise AssertionError(f"first audit transition wrong: {first}")
    if first["attempt_count"] != 1 or first["metadata"]["attempt_number"] != 1:
        raise AssertionError(f"attempt metadata missing: {first}")
    if coordinator.to_dict()["audit_events"][2] != audit_payloads[2]:
        raise AssertionError(f"serialized audit events should match sink payloads: {coordinator.to_dict()}")


def assert_action_tool_mapping_resolves_registered_tools() -> None:
    calls = []

    def fake_diagnostic(customer_id: str) -> dict:
        calls.append(customer_id)
        return {"customer_id": customer_id, "diagnostic_failure": False, "needs_technician": False}

    action_tool = resolve_action_tool(
        "router_reset",
        tool_registry={"run_router_diagnostic": fake_diagnostic},
    )
    if not isinstance(action_tool, GuidedActionTool):
        raise AssertionError(f"wrong action tool type: {action_tool}")
    if action_tool.to_dict() != {
        "action_name": "router_reset",
        "tool_name": "run_router_diagnostic",
    }:
        raise AssertionError(f"action tool payload wrong: {action_tool.to_dict()}")
    result = action_tool.tool("CUST-1001")
    if calls != ["CUST-1001"] or result["diagnostic_failure"] is not False:
        raise AssertionError(f"resolved tool did not execute: {calls} {result}")
    public_map = guided_action_tool_map()
    public_map["router_reset"] = "changed"
    if ACTION_TO_TOOL_MAP["router_reset"] != "run_router_diagnostic":
        raise AssertionError("guided_action_tool_map should return a copy")


def assert_handle_user_report_uses_action_tool_mapping() -> None:
    calls = []

    def fake_diagnostic(customer_id: str) -> dict:
        calls.append(customer_id)
        return {
            "customer_id": customer_id,
            "customer_found": True,
            "diagnostic_available": True,
            "diagnostic_failure": False,
            "needs_technician": False,
            "account_active": True,
        }

    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    coordinator.instruct()
    verification = coordinator.handle_user_report(
        "The light is steady now",
        tool_registry={"run_router_diagnostic": fake_diagnostic},
    )

    if calls != ["CUST-1001"]:
        raise AssertionError(f"mapped diagnostic was not called: {calls}")
    if verification.tool_name != "run_router_diagnostic":
        raise AssertionError(f"mapped tool name missing: {verification.to_dict()}")
    if coordinator.state != GuidedActionState.RESOLVED:
        raise AssertionError(f"mapped tool should resolve healthy diagnostic: {coordinator.to_dict()}")
    if coordinator.transition_history[-2].metadata["tool_name"] != "run_router_diagnostic":
        raise AssertionError(f"transition should log mapped tool name: {coordinator.to_dict()}")


def assert_instruct_generates_single_step_and_enters_waiting() -> None:
    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    instruction = coordinator.instruct(
        timestamp="2026-05-24T10:01:00+00:00",
        metadata={"source": "router_issue"},
    )

    if not isinstance(instruction, GuidedActionInstruction):
        raise AssertionError(f"wrong instruction type: {instruction}")
    if coordinator.state != GuidedActionState.WAITING:
        raise AssertionError(f"instruct() should enter WAITING: {coordinator.to_dict()}")
    if coordinator.attempt_count != 1:
        raise AssertionError(f"instruct() should increment attempt count: {coordinator.to_dict()}")
    if instruction.to_dict() != {
        "action_name": "router_reset",
        "customer_id": "CUST-1001",
        "instruction": DEFAULT_ACTION_INSTRUCTIONS["router_reset"][0],
        "attempt_number": 1,
        "state": "WAITING",
        "metadata": {"attempt_number": 1, "source": "router_issue"},
    }:
        raise AssertionError(f"instruction payload wrong: {instruction.to_dict()}")
    if coordinator.transition_history[-1].to_dict() != {
        "from_state": "IDLE",
        "to_state": "WAITING",
        "reason": "single-step instruction sent",
        "timestamp": "2026-05-24T10:01:00+00:00",
        "metadata": {"attempt_number": 1, "source": "router_issue"},
    }:
        raise AssertionError(f"instruct transition wrong: {coordinator.to_dict()}")
    if coordinator.to_dict()["current_instruction"] != instruction.to_dict():
        raise AssertionError(f"current instruction should serialize: {coordinator.to_dict()}")


def assert_instruct_accepts_custom_single_step() -> None:
    coordinator = GuidedActionCoordinator("wifi_reconnect", "CUST-1002")
    instruction = coordinator.instruct("Reconnect to your home Wi-Fi network and tell me when connected.")

    if instruction.instruction != "Reconnect to your home Wi-Fi network and tell me when connected.":
        raise AssertionError(f"custom instruction was not used: {instruction.to_dict()}")


def assert_handle_user_report_runs_verification_tool_and_resolves() -> None:
    calls = []

    def verification_tool(customer_id: str) -> dict:
        calls.append(customer_id)
        return {
            "customer_id": customer_id,
            "customer_found": True,
            "diagnostic_available": True,
            "router_status": "ok",
            "signal_strength": 86,
            "diagnostic_failure": False,
            "needs_technician": False,
            "account_active": True,
        }

    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    coordinator.instruct(timestamp="2026-05-24T10:01:00+00:00")
    verification = coordinator.handle_user_report(
        "I restarted it and the light is steady",
        verification_tool,
        timestamp="2026-05-24T10:03:00+00:00",
        metadata={"channel": "chat"},
    )

    if calls != ["CUST-1001"]:
        raise AssertionError(f"verification tool was not called with customer_id: {calls}")
    if not isinstance(verification, GuidedActionVerification):
        raise AssertionError(f"wrong verification type: {verification}")
    if coordinator.state != GuidedActionState.RESOLVED:
        raise AssertionError(f"successful verification should resolve: {coordinator.to_dict()}")
    if verification.verified is not True:
        raise AssertionError(f"verification should be true: {verification.to_dict()}")
    if verification.tool_name != "verification_tool":
        raise AssertionError(f"tool name should default from callable: {verification.to_dict()}")
    if coordinator.transition_history[-2].to_dict() != {
        "from_state": "WAITING",
        "to_state": "VERIFYING",
        "reason": "customer reported action completion; verification tool required",
        "timestamp": "2026-05-24T10:03:00+00:00",
        "metadata": {
            "attempt_number": 1,
            "user_report": "I restarted it and the light is steady",
            "tool_name": "verification_tool",
            "channel": "chat",
        },
    }:
        raise AssertionError(f"VERIFYING transition wrong: {coordinator.to_dict()}")
    if coordinator.transition_history[-1].to_dict()["to_state"] != "RESOLVED":
        raise AssertionError(f"final transition should resolve: {coordinator.to_dict()}")
    if coordinator.to_dict()["last_verification"] != verification.to_dict():
        raise AssertionError(f"last verification should serialize: {coordinator.to_dict()}")


def assert_handle_user_report_fails_when_tool_disagrees_with_user_claim() -> None:
    def still_bad(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "customer_found": True,
            "diagnostic_available": True,
            "router_status": "degraded",
            "signal_strength": 28,
            "diagnostic_failure": True,
            "needs_technician": True,
            "account_active": True,
        }

    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    coordinator.instruct()
    verification = coordinator.handle_user_report(
        "It is fixed now",
        still_bad,
        tool_name="run_router_diagnostic",
    )

    if coordinator.state != GuidedActionState.FAILED:
        raise AssertionError(f"failed diagnostic should set FAILED, not trust claim: {coordinator.to_dict()}")
    if verification.verified is not False:
        raise AssertionError(f"verification should be false: {verification.to_dict()}")
    if verification.tool_result["diagnostic_failure"] is not True:
        raise AssertionError(f"tool result should be preserved: {verification.to_dict()}")


def assert_handle_user_report_supports_custom_success_evaluator() -> None:
    coordinator = GuidedActionCoordinator("wifi_reconnect", "CUST-1002")
    coordinator.instruct()
    verification = coordinator.handle_user_report(
        "Connected",
        lambda customer_id: {"customer_id": customer_id, "speed_mbps": 183},
        tool_name="speed_test",
        success_evaluator=lambda result: result["speed_mbps"] >= 100,
    )

    if coordinator.state != GuidedActionState.RESOLVED or verification.verified is not True:
        raise AssertionError(f"custom evaluator should resolve: {coordinator.to_dict()}")


def assert_retry_uses_clearer_second_instruction() -> None:
    def still_bad(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "customer_found": True,
            "diagnostic_available": True,
            "router_status": "degraded",
            "signal_strength": 28,
            "diagnostic_failure": True,
            "needs_technician": True,
            "account_active": True,
        }

    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    first_instruction = coordinator.instruct(timestamp="2026-05-24T10:01:00+00:00")
    coordinator.handle_user_report("Done", still_bad, timestamp="2026-05-24T10:03:00+00:00")

    if coordinator.state != GuidedActionState.FAILED or not coordinator.can_retry:
        raise AssertionError(f"first failed verification should be retryable: {coordinator.to_dict()}")
    if coordinator.attempts_remaining != 1:
        raise AssertionError(f"one attempt should remain: {coordinator.to_dict()}")

    second_instruction = coordinator.instruct(timestamp="2026-05-24T10:04:00+00:00")
    if second_instruction.instruction != DEFAULT_ACTION_INSTRUCTIONS["router_reset"][1]:
        raise AssertionError(f"second instruction should be clearer retry text: {second_instruction.to_dict()}")
    if second_instruction.instruction == first_instruction.instruction:
        raise AssertionError("retry instruction should not repeat the first wording")
    if coordinator.state != GuidedActionState.WAITING or coordinator.attempt_count != 2:
        raise AssertionError(f"retry should enter WAITING as attempt 2: {coordinator.to_dict()}")
    if coordinator.transition_history[-1].to_dict() != {
        "from_state": "FAILED",
        "to_state": "WAITING",
        "reason": "single-step instruction sent",
        "timestamp": "2026-05-24T10:04:00+00:00",
        "metadata": {"attempt_number": 2},
    }:
        raise AssertionError(f"retry transition wrong: {coordinator.to_dict()}")


def assert_retry_stops_after_max_attempts() -> None:
    def still_bad(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "customer_found": True,
            "diagnostic_failure": True,
            "needs_technician": True,
            "account_active": True,
        }

    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    coordinator.instruct()
    coordinator.handle_user_report("done", still_bad)
    coordinator.instruct()
    coordinator.handle_user_report("done again", still_bad)

    if coordinator.state != GuidedActionState.FAILED:
        raise AssertionError(f"second failed verification should remain FAILED: {coordinator.to_dict()}")
    if coordinator.can_retry or coordinator.attempts_remaining != 0:
        raise AssertionError(f"max attempts should stop retry: {coordinator.to_dict()}")
    try:
        coordinator.instruct()
    except ValueError:
        pass
    else:
        raise AssertionError("third instruction was accepted despite MAX_ATTEMPTS = 2")


def assert_verification_tool_exception_leaves_retryable_failed_state() -> None:
    def tool_crashes(customer_id: str) -> dict:
        raise RuntimeError(f"diagnostic timeout for {customer_id}")

    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    coordinator.instruct()
    verification = coordinator.handle_user_report(
        "done",
        tool_crashes,
        tool_name="run_router_diagnostic",
    )

    if coordinator.state != GuidedActionState.FAILED:
        raise AssertionError(f"tool exception should leave FAILED, not VERIFYING: {coordinator.to_dict()}")
    if not coordinator.can_retry or coordinator.attempts_remaining != 1:
        raise AssertionError(f"first tool exception should remain retryable: {coordinator.to_dict()}")
    if verification.verified is not False:
        raise AssertionError(f"crashed verification should not verify: {verification.to_dict()}")
    if verification.tool_result.get("verification_error") is not True:
        raise AssertionError(f"crashed tool result should be captured: {verification.to_dict()}")
    if coordinator.transition_history[-1].to_state != GuidedActionState.FAILED:
        raise AssertionError(f"crash should transition out of VERIFYING: {coordinator.to_dict()}")

    retry = coordinator.instruct()
    if retry.attempt_number != 2 or coordinator.state != GuidedActionState.WAITING:
        raise AssertionError(f"retry after verifier crash should be allowed: {coordinator.to_dict()}")


def assert_final_verification_exception_can_escalate() -> None:
    handoff_calls = []

    def tool_crashes(customer_id: str) -> dict:
        raise TimeoutError(f"diagnostic timeout for {customer_id}")

    def handoff_builder(**kwargs) -> dict:
        handoff_calls.append(kwargs)
        return {"handoff_id": "HND-CRASH", "status": "waiting"}

    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    coordinator.instruct()
    coordinator.handle_user_report("done", tool_crashes, tool_name="run_router_diagnostic")
    coordinator.instruct()
    verification = coordinator.handle_user_report(
        "done again",
        tool_crashes,
        tool_name="run_router_diagnostic",
    )

    if coordinator.state != GuidedActionState.FAILED:
        raise AssertionError(f"final tool exception should leave FAILED: {coordinator.to_dict()}")
    if coordinator.can_retry or coordinator.attempts_remaining != 0:
        raise AssertionError(f"final tool exception should exhaust retry attempts: {coordinator.to_dict()}")
    if verification.tool_result.get("error_type") != "TimeoutError":
        raise AssertionError(f"timeout details should be preserved: {verification.to_dict()}")

    handoff = coordinator.escalate_to_handoff(handoff_builder=handoff_builder)
    if coordinator.state != GuidedActionState.ESCALATED or not coordinator.is_terminal:
        raise AssertionError(f"final failed verifier should escalate cleanly: {coordinator.to_dict()}")
    if handoff.handoff_result != {"handoff_id": "HND-CRASH", "status": "waiting"}:
        raise AssertionError(f"handoff result missing: {handoff.to_dict()}")
    if handoff_calls[0]["context_card"]["last_verification"]["tool_result"]["error_type"] != "TimeoutError":
        raise AssertionError(f"handoff context should preserve verifier error: {handoff_calls}")


def assert_escalates_failed_state_to_feature8_handoff() -> None:
    handoff_calls = []

    def still_bad(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "customer_found": True,
            "diagnostic_failure": True,
            "needs_technician": True,
            "account_active": True,
        }

    def handoff_builder(**kwargs) -> dict:
        handoff_calls.append(kwargs)
        return {"handoff_id": "HND-001", "status": "waiting"}

    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    coordinator.instruct()
    coordinator.handle_user_report("done", still_bad, tool_name="run_router_diagnostic")
    try:
        coordinator.escalate_to_handoff()
    except ValueError:
        pass
    else:
        raise AssertionError("handoff should wait until retry attempts are exhausted")

    coordinator.instruct()
    coordinator.handle_user_report("done again", still_bad, tool_name="run_router_diagnostic")
    handoff = coordinator.escalate_to_handoff(
        handoff_builder=handoff_builder,
        timestamp="2026-05-24T10:08:00+00:00",
        metadata={"conversation_id": "sess-guided-001"},
    )

    if not isinstance(handoff, GuidedActionHandoff):
        raise AssertionError(f"wrong handoff type: {handoff}")
    if coordinator.state != GuidedActionState.ESCALATED or not coordinator.is_terminal:
        raise AssertionError(f"handoff should move to terminal ESCALATED: {coordinator.to_dict()}")
    if "failed after 2 attempt" not in handoff.handoff_reason:
        raise AssertionError(f"default handoff reason should mention exhausted attempts: {handoff.to_dict()}")
    if "specialist" not in handoff.customer_message:
        raise AssertionError(f"customer-facing handoff message missing: {handoff.to_dict()}")
    if handoff.handoff_result != {"handoff_id": "HND-001", "status": "waiting"}:
        raise AssertionError(f"Feature 8 handoff result missing: {handoff.to_dict()}")
    if len(handoff_calls) != 1:
        raise AssertionError(f"handoff builder should be called once: {handoff_calls}")
    call = handoff_calls[0]
    if call["customer_id"] != "CUST-1001":
        raise AssertionError(f"handoff builder customer wrong: {call}")
    if call["context_card"]["last_verification"]["tool_result"]["diagnostic_failure"] is not True:
        raise AssertionError(f"context card should preserve failed diagnostic: {call}")
    if call["metadata"]["conversation_id"] != "sess-guided-001":
        raise AssertionError(f"handoff metadata missing: {call}")
    if coordinator.transition_history[-1].to_dict() != {
        "from_state": "FAILED",
        "to_state": "ESCALATED",
        "reason": "guided action failed after maximum attempts; handoff required",
        "timestamp": "2026-05-24T10:08:00+00:00",
        "metadata": {
            "handoff_reason": handoff.handoff_reason,
            "customer_message": handoff.customer_message,
            "handoff_result": {"handoff_id": "HND-001", "status": "waiting"},
            "attempt_count": 2,
            "max_attempts": 2,
            "action_name": "router_reset",
            "conversation_id": "sess-guided-001",
        },
    }:
        raise AssertionError(f"handoff transition wrong: {coordinator.to_dict()}")
    if coordinator.to_dict()["handoff"] != handoff.to_dict():
        raise AssertionError(f"handoff should serialize: {coordinator.to_dict()}")


def assert_escalation_validates_inputs() -> None:
    def still_bad(customer_id: str) -> dict:
        return {
            "customer_id": customer_id,
            "diagnostic_failure": True,
            "needs_technician": True,
            "account_active": True,
        }

    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    invalid_before_failed = lambda: coordinator.escalate_to_handoff()

    failed = GuidedActionCoordinator("router_reset", "CUST-1001")
    failed.instruct()
    failed.handle_user_report("done", still_bad)
    failed.instruct()
    failed.handle_user_report("done again", still_bad)

    invalid_calls = (
        invalid_before_failed,
        lambda: failed.escalate_to_handoff(handoff_builder="not callable"),
        lambda: failed.escalate_to_handoff(handoff_reason=" "),
        lambda: failed.escalate_to_handoff(customer_message=" "),
        lambda: failed.escalate_to_handoff(handoff_builder=lambda **kwargs: "bad"),
    )
    for invalid_call in invalid_calls:
        try:
            invalid_call()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid escalation input was accepted")


def assert_invalid_transitions_are_rejected() -> None:
    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    invalid_calls = (
        lambda: coordinator.transition("RESOLVED", "cannot skip waiting"),
        lambda: GuidedActionCoordinator("", "CUST-1001"),
        lambda: GuidedActionCoordinator("router_reset", ""),
        lambda: GuidedActionCoordinator("router_reset", "CUST-1001", state="unknown"),
        lambda: GuidedActionCoordinator("router_reset", "CUST-1001", attempt_count=-1),
        lambda: GuidedActionCoordinator("router_reset", "CUST-1001", max_attempts=0),
        lambda: GuidedActionCoordinator("router_reset", "CUST-1001", context=[]),
        lambda: GuidedActionCoordinator("router_reset", "CUST-1001", audit_sink="not callable"),
        lambda: resolve_action_tool("unknown_action", tool_registry={}),
        lambda: resolve_action_tool("router_reset", tool_registry=[]),
        lambda: resolve_action_tool("router_reset", tool_registry={"run_router_diagnostic": "not callable"}),
        lambda: GuidedActionCoordinator("router_reset", "CUST-1001", state="WAITING").instruct(),
        lambda: GuidedActionCoordinator("router_reset", "CUST-1001", attempt_count=2).instruct(),
        lambda: GuidedActionCoordinator("router_reset", "CUST-1001").instruct("- Unplug router\n- Plug it in"),
        lambda: GuidedActionCoordinator("router_reset", "CUST-1001").handle_user_report("done", lambda customer_id: {}),
        lambda: _waiting_coordinator().handle_user_report("", lambda customer_id: {}),
        lambda: _waiting_coordinator().handle_user_report("done", "not callable"),
        lambda: _waiting_coordinator().handle_user_report("done", lambda customer_id: "ok"),
    )
    for invalid_call in invalid_calls:
        try:
            invalid_call()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid guided action input was accepted")


def main() -> None:
    assert_state_enum_contract()
    assert_coordinator_initializes_and_serializes()
    assert_valid_transitions_are_recorded()
    assert_audit_sink_receives_every_state_transition()
    assert_action_tool_mapping_resolves_registered_tools()
    assert_handle_user_report_uses_action_tool_mapping()
    assert_instruct_generates_single_step_and_enters_waiting()
    assert_instruct_accepts_custom_single_step()
    assert_handle_user_report_runs_verification_tool_and_resolves()
    assert_handle_user_report_fails_when_tool_disagrees_with_user_claim()
    assert_handle_user_report_supports_custom_success_evaluator()
    assert_retry_uses_clearer_second_instruction()
    assert_retry_stops_after_max_attempts()
    assert_verification_tool_exception_leaves_retryable_failed_state()
    assert_final_verification_exception_can_escalate()
    assert_escalates_failed_state_to_feature8_handoff()
    assert_escalation_validates_inputs()
    assert_invalid_transitions_are_rejected()
    print("guided action coordinator tests passed")


def _waiting_coordinator() -> GuidedActionCoordinator:
    coordinator = GuidedActionCoordinator("router_reset", "CUST-1001")
    coordinator.instruct()
    return coordinator


if __name__ == "__main__":
    main()
