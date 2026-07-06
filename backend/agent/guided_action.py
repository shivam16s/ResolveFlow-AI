from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
from uuid import uuid4


class GuidedActionState(str, Enum):
    IDLE = "IDLE"
    WAITING = "WAITING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


MAX_ATTEMPTS = 2


VALID_STATE_TRANSITIONS = {
    GuidedActionState.IDLE: {GuidedActionState.WAITING, GuidedActionState.ESCALATED},
    GuidedActionState.WAITING: {GuidedActionState.VERIFYING, GuidedActionState.FAILED, GuidedActionState.ESCALATED},
    GuidedActionState.VERIFYING: {
        GuidedActionState.WAITING,
        GuidedActionState.RESOLVED,
        GuidedActionState.FAILED,
        GuidedActionState.ESCALATED,
    },
    GuidedActionState.RESOLVED: set(),
    GuidedActionState.FAILED: {GuidedActionState.WAITING, GuidedActionState.ESCALATED},
    GuidedActionState.ESCALATED: set(),
}


DEFAULT_ACTION_INSTRUCTIONS = {
    "router_reset": (
        "Unplug the router power cable, wait 30 seconds, plug it back in, and tell me when the internet light is steady.",
        "Hold the router power button for 10 seconds, wait until the internet light turns steady, and tell me when that is done.",
    ),
    "modem_reseat": (
        "Check that the fiber or coax cable is firmly connected to the modem, then tell me when it is secure.",
        "Remove and firmly reconnect the modem cable until it clicks or feels seated, then tell me when it is secure.",
    ),
    "wifi_reconnect": (
        "Turn Wi-Fi off on your device, turn it back on, reconnect to your home network, and tell me when connected.",
        "Forget the home Wi-Fi network on your device, join it again with the saved password, and tell me when connected.",
    ),
}


ACTION_TO_TOOL_MAP = {
    "router_reset": "run_router_diagnostic",
    "modem_reseat": "run_router_diagnostic",
    "wifi_reconnect": "run_router_diagnostic",
}


@dataclass(frozen=True)
class GuidedActionTransition:
    from_state: GuidedActionState
    to_state: GuidedActionState
    reason: str
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["from_state"] = self.from_state.value
        payload["to_state"] = self.to_state.value
        return payload


@dataclass(frozen=True)
class GuidedActionTool:
    action_name: str
    tool_name: str
    tool: Callable[..., Any]

    def __post_init__(self) -> None:
        _require_text(self.action_name, "action_name")
        _require_text(self.tool_name, "tool_name")
        if not callable(self.tool):
            raise ValueError("tool must be callable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_name": self.action_name,
            "tool_name": self.tool_name,
        }


@dataclass(frozen=True)
class GuidedActionAuditEvent:
    event_id: str
    action_name: str
    customer_id: str
    from_state: GuidedActionState
    to_state: GuidedActionState
    reason: str
    timestamp: str
    attempt_count: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["from_state"] = self.from_state.value
        payload["to_state"] = self.to_state.value
        return payload


@dataclass(frozen=True)
class GuidedActionInstruction:
    action_name: str
    customer_id: str
    instruction: str
    attempt_number: int
    state: GuidedActionState
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.instruction, "instruction")
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be at least 1")
        if self.state != GuidedActionState.WAITING:
            raise ValueError(
                "guided action instructions must leave the coordinator in WAITING")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


@dataclass(frozen=True)
class GuidedActionVerification:
    action_name: str
    customer_id: str
    user_report: str
    tool_name: str
    tool_result: dict[str, Any]
    verified: bool
    state: GuidedActionState
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.user_report, "user_report")
        _require_text(self.tool_name, "tool_name")
        if self.state not in {GuidedActionState.RESOLVED, GuidedActionState.FAILED}:
            raise ValueError(
                "guided action verification must end in RESOLVED or FAILED")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


@dataclass(frozen=True)
class GuidedActionHandoff:
    action_name: str
    customer_id: str
    handoff_reason: str
    customer_message: str
    context_card: dict[str, Any]
    state: GuidedActionState
    handoff_result: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.handoff_reason, "handoff_reason")
        _require_text(self.customer_message, "customer_message")
        if self.state != GuidedActionState.ESCALATED:
            raise ValueError("guided action handoff must end in ESCALATED")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


@dataclass
class GuidedActionCoordinator:
    action_name: str
    customer_id: str
    state: GuidedActionState = GuidedActionState.IDLE
    attempt_count: int = 0
    max_attempts: int = MAX_ATTEMPTS
    transition_history: list[GuidedActionTransition] = field(
        default_factory=list)
    audit_events: list[GuidedActionAuditEvent] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    current_instruction: GuidedActionInstruction | None = None
    last_verification: GuidedActionVerification | None = None
    handoff: GuidedActionHandoff | None = None
    audit_sink: Callable[[dict[str, Any]], Any] | None = None

    def __post_init__(self) -> None:
        self.action_name = _require_text(self.action_name, "action_name")
        self.customer_id = _require_text(self.customer_id, "customer_id")
        self.state = _coerce_state(self.state)
        if self.attempt_count < 0:
            raise ValueError("attempt_count must be zero or greater")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.attempt_count > self.max_attempts:
            raise ValueError("attempt_count cannot exceed max_attempts")
        if self.context is None:
            self.context = {}
        if not isinstance(self.context, dict):
            raise ValueError("context must be a dict")
        if self.audit_sink is not None and not callable(self.audit_sink):
            raise ValueError("audit_sink must be callable when provided")

    @property
    def is_terminal(self) -> bool:
        return self.state in {GuidedActionState.RESOLVED, GuidedActionState.ESCALATED}

    @property
    def attempts_remaining(self) -> int:
        return max(self.max_attempts - self.attempt_count, 0)

    @property
    def can_retry(self) -> bool:
        return self.state == GuidedActionState.FAILED and self.attempts_remaining > 0

    def can_transition_to(self, next_state: GuidedActionState | str) -> bool:
        normalized_next = _coerce_state(next_state)
        return normalized_next in VALID_STATE_TRANSITIONS[self.state]

    def transition(
        self,
        next_state: GuidedActionState | str,
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> GuidedActionTransition:
        normalized_next = _coerce_state(next_state)
        normalized_reason = _require_text(reason, "reason")
        if not self.can_transition_to(normalized_next):
            raise ValueError(
                f"invalid guided action transition: {self.state.value} -> {normalized_next.value}")

        transition = GuidedActionTransition(
            from_state=self.state,
            to_state=normalized_next,
            reason=normalized_reason,
            timestamp=timestamp or _utc_now_iso(),
            metadata=dict(metadata or {}),
        )
        self.transition_history.append(transition)
        self.state = normalized_next
        self._record_audit_event(transition)
        return transition

    def _record_audit_event(self, transition: GuidedActionTransition) -> GuidedActionAuditEvent:
        event = GuidedActionAuditEvent(
            event_id=f"GAUD-{uuid4().hex[:12].upper()}",
            action_name=self.action_name,
            customer_id=self.customer_id,
            from_state=transition.from_state,
            to_state=transition.to_state,
            reason=transition.reason,
            timestamp=transition.timestamp,
            attempt_count=self.attempt_count,
            metadata=dict(transition.metadata),
        )
        self.audit_events.append(event)
        if self.audit_sink is not None:
            self.audit_sink(event.to_dict())
        return event

    def instruct(
        self,
        instruction: str | None = None,
        *,
        metadata: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> GuidedActionInstruction:
        if self.state not in {GuidedActionState.IDLE, GuidedActionState.VERIFYING, GuidedActionState.FAILED}:
            raise ValueError(
                f"instruct() cannot run from state {self.state.value}")
        if self.attempt_count >= self.max_attempts:
            raise ValueError("maximum guided action attempts reached")

        next_attempt = self.attempt_count + 1
        instruction_text = _single_step_instruction(
            instruction or _default_instruction_for(
                self.action_name, next_attempt)
        )
        instruction_metadata = {
            "attempt_number": next_attempt, **dict(metadata or {})}
        self.attempt_count = next_attempt
        self.transition(
            GuidedActionState.WAITING,
            "single-step instruction sent",
            metadata=instruction_metadata,
            timestamp=timestamp,
        )
        self.current_instruction = GuidedActionInstruction(
            action_name=self.action_name,
            customer_id=self.customer_id,
            instruction=instruction_text,
            attempt_number=next_attempt,
            state=self.state,
            metadata=instruction_metadata,
        )
        return self.current_instruction

    def handle_user_report(
        self,
        user_report: str,
        verification_tool: Callable[..., Any] | None = None,
        *,
        tool_name: str | None = None,
        tool_registry: dict[str, Callable[..., Any]] | None = None,
        success_evaluator: Callable[[dict[str, Any]], bool] | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> GuidedActionVerification:
        if self.state != GuidedActionState.WAITING:
            raise ValueError(
                f"handle_user_report() cannot run from state {self.state.value}")
        normalized_report = _require_text(user_report, "user_report")
        if verification_tool is not None and not callable(verification_tool):
            raise ValueError("verification_tool must be callable")
        action_tool = resolve_action_tool(
            self.action_name, tool_registry=tool_registry) if verification_tool is None else None
        resolved_tool = verification_tool or action_tool.tool
        normalized_tool_name = _require_text(
            tool_name or (action_tool.tool_name if action_tool else getattr(
                resolved_tool, "__name__", "verification_tool")),
            "tool_name",
        )

        verify_metadata = {
            "attempt_number": self.attempt_count,
            "user_report": normalized_report,
            "tool_name": normalized_tool_name,
            **dict(metadata or {}),
        }
        self.transition(
            GuidedActionState.VERIFYING,
            "customer reported action completion; verification tool required",
            metadata=verify_metadata,
            timestamp=timestamp,
        )

        try:
            raw_tool_result = resolved_tool(self.customer_id)
        except Exception as exc:  # noqa: BLE001 - failed verifiers must not strand VERIFYING.
            return self._finish_verification(
                user_report=normalized_report,
                tool_name=normalized_tool_name,
                tool_result=_verification_error_result(exc),
                verified=False,
                reason="verification tool raised an error",
                verify_metadata=verify_metadata,
                timestamp=timestamp,
            )

        try:
            tool_result = _normalize_tool_result(raw_tool_result)
            evaluator = success_evaluator or _default_verification_success
            verified = bool(evaluator(tool_result))
        except Exception:
            self._finish_verification(
                user_report=normalized_report,
                tool_name=normalized_tool_name,
                tool_result=_verification_error_result(
                    ValueError("verification tool returned an invalid result")
                ),
                verified=False,
                reason="verification result could not be evaluated",
                verify_metadata=verify_metadata,
                timestamp=timestamp,
            )
            raise

        return self._finish_verification(
            user_report=normalized_report,
            tool_name=normalized_tool_name,
            tool_result=tool_result,
            verified=verified,
            reason="verification tool confirmed resolution" if verified else "verification tool did not confirm resolution",
            verify_metadata=verify_metadata,
            timestamp=timestamp,
        )

    def _finish_verification(
        self,
        *,
        user_report: str,
        tool_name: str,
        tool_result: dict[str, Any],
        verified: bool,
        reason: str,
        verify_metadata: dict[str, Any],
        timestamp: str | None,
    ) -> GuidedActionVerification:
        next_state = GuidedActionState.RESOLVED if verified else GuidedActionState.FAILED
        self.transition(
            next_state,
            reason,
            metadata={
                "attempt_number": self.attempt_count,
                "tool_name": tool_name,
                "tool_result": tool_result,
            },
            timestamp=timestamp,
        )
        self.last_verification = GuidedActionVerification(
            action_name=self.action_name,
            customer_id=self.customer_id,
            user_report=user_report,
            tool_name=tool_name,
            tool_result=tool_result,
            verified=verified,
            state=self.state,
            metadata=verify_metadata,
        )
        return self.last_verification

    def escalate_to_handoff(
        self,
        *,
        handoff_builder: Callable[..., Any] | None = None,
        handoff_reason: str | None = None,
        customer_message: str | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> GuidedActionHandoff:
        if self.state != GuidedActionState.FAILED:
            raise ValueError(
                f"escalate_to_handoff() cannot run from state {self.state.value}")
        if self.can_retry:
            raise ValueError(
                "guided action still has retry attempts remaining")
        if handoff_builder is not None and not callable(handoff_builder):
            raise ValueError("handoff_builder must be callable when provided")

        normalized_reason = (
            " ".join(handoff_reason.split())
            if isinstance(handoff_reason, str)
            else _default_handoff_reason(self)
        )
        normalized_reason = _require_text(normalized_reason, "handoff_reason")
        normalized_customer_message = (
            " ".join(customer_message.split())
            if isinstance(customer_message, str)
            else "I could not confirm the fix after the guided steps, so I am connecting you to a specialist."
        )
        normalized_customer_message = _require_text(
            normalized_customer_message, "customer_message")
        context_card = _handoff_context_card(self)
        handoff_metadata = {
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "action_name": self.action_name,
            **dict(metadata or {}),
        }
        handoff_result = None
        if handoff_builder is not None:
            handoff_result = _normalize_optional_dict(
                handoff_builder(
                    customer_id=self.customer_id,
                    handoff_reason=normalized_reason,
                    context_card=context_card,
                    metadata=handoff_metadata,
                )
            )

        self.transition(
            GuidedActionState.ESCALATED,
            "guided action failed after maximum attempts; handoff required",
            metadata={
                "handoff_reason": normalized_reason,
                "customer_message": normalized_customer_message,
                "handoff_result": handoff_result,
                **handoff_metadata,
            },
            timestamp=timestamp,
        )
        self.handoff = GuidedActionHandoff(
            action_name=self.action_name,
            customer_id=self.customer_id,
            handoff_reason=normalized_reason,
            customer_message=normalized_customer_message,
            context_card=context_card,
            state=self.state,
            handoff_result=handoff_result,
            metadata=handoff_metadata,
        )
        return self.handoff

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_name": self.action_name,
            "customer_id": self.customer_id,
            "state": self.state.value,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "attempts_remaining": self.attempts_remaining,
            "can_retry": self.can_retry,
            "context": dict(self.context),
            "current_instruction": self.current_instruction.to_dict() if self.current_instruction else None,
            "last_verification": self.last_verification.to_dict() if self.last_verification else None,
            "handoff": self.handoff.to_dict() if self.handoff else None,
            "is_terminal": self.is_terminal,
            "transition_history": [transition.to_dict() for transition in self.transition_history],
            "audit_events": [event.to_dict() for event in self.audit_events],
        }


def _coerce_state(state: GuidedActionState | str) -> GuidedActionState:
    if isinstance(state, GuidedActionState):
        return state
    try:
        return GuidedActionState(str(state).strip().upper())
    except ValueError as exc:
        raise ValueError(f"unknown guided action state: {state}") from exc


def resolve_action_tool(
    action_name: str,
    *,
    tool_registry: dict[str, Callable[..., Any]] | None = None,
) -> GuidedActionTool:
    normalized_action = _require_text(action_name, "action_name")
    tool_name = ACTION_TO_TOOL_MAP.get(normalized_action)
    if tool_name is None:
        raise ValueError(
            f"no verification tool mapped for guided action: {normalized_action}")
    if tool_registry is not None and not isinstance(tool_registry, dict):
        raise ValueError("tool_registry must be a dict when provided")
    registry = tool_registry if tool_registry is not None else _default_tool_registry()
    tool = registry.get(tool_name)
    if not callable(tool):
        raise ValueError(
            f"mapped verification tool is not available: {tool_name}")
    return GuidedActionTool(action_name=normalized_action, tool_name=tool_name, tool=tool)


def guided_action_tool_map() -> dict[str, str]:
    return dict(ACTION_TO_TOOL_MAP)


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _default_tool_registry() -> dict[str, Callable[..., Any]]:
    from backend.tools import run_router_diagnostic

    return {
        "run_router_diagnostic": run_router_diagnostic,
    }


def _default_instruction_for(action_name: str, attempt_number: int) -> str:
    instructions = DEFAULT_ACTION_INSTRUCTIONS.get(action_name)
    if not instructions:
        return f"Complete the {action_name.replace('_', ' ')} step, then tell me when it is done."
    index = min(attempt_number - 1, len(instructions) - 1)
    return instructions[index]


def _single_step_instruction(instruction: str) -> str:
    normalized = _require_text(instruction, "instruction")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError(
            "instruction must be a single step, not a multi-line list")
    stripped = normalized.lstrip()
    if stripped.startswith(("-", "*", "1.", "2.", "3.")):
        raise ValueError(
            "instruction must be one single step, not a list item")
    return normalized


def _normalize_tool_result(raw_tool_result: Any) -> dict[str, Any]:
    if hasattr(raw_tool_result, "to_dict") and callable(raw_tool_result.to_dict):
        raw_tool_result = raw_tool_result.to_dict()
    if not isinstance(raw_tool_result, dict):
        raise ValueError(
            "verification_tool must return a dict or an object with to_dict()")
    return dict(raw_tool_result)


def _verification_error_result(exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "verification_error": True,
        "error_type": exc.__class__.__name__,
        "error": str(exc),
    }


def _normalize_optional_dict(raw_value: Any) -> dict[str, Any] | None:
    if raw_value is None:
        return None
    if hasattr(raw_value, "to_dict") and callable(raw_value.to_dict):
        raw_value = raw_value.to_dict()
    if not isinstance(raw_value, dict):
        raise ValueError(
            "handoff_builder must return a dict, None, or an object with to_dict()")
    return dict(raw_value)


def _default_verification_success(tool_result: dict[str, Any]) -> bool:
    if tool_result.get("customer_found") is False:
        return False
    if tool_result.get("account_active") is False:
        return False
    if "diagnostic_failure" in tool_result:
        return tool_result.get("diagnostic_failure") is False and tool_result.get("needs_technician") is not True
    if "ok" in tool_result:
        return bool(tool_result["ok"])
    if "success" in tool_result:
        return bool(tool_result["success"])
    if "resolved" in tool_result:
        return bool(tool_result["resolved"])
    return False


def _default_handoff_reason(coordinator: GuidedActionCoordinator) -> str:
    tool_name = coordinator.last_verification.tool_name if coordinator.last_verification else "verification tool"
    return (
        f"Guided action {coordinator.action_name} failed after {coordinator.attempt_count} attempt(s); "
        f"{tool_name} did not confirm resolution."
    )


def _handoff_context_card(coordinator: GuidedActionCoordinator) -> dict[str, Any]:
    return {
        "customer_id": coordinator.customer_id,
        "action_name": coordinator.action_name,
        "state": coordinator.state.value,
        "attempt_count": coordinator.attempt_count,
        "max_attempts": coordinator.max_attempts,
        "current_instruction": (
            coordinator.current_instruction.to_dict(
            ) if coordinator.current_instruction else None
        ),
        "last_verification": coordinator.last_verification.to_dict() if coordinator.last_verification else None,
        "transition_history": [transition.to_dict() for transition in coordinator.transition_history],
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
