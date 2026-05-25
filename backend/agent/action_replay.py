from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .llm_client import GeminiClientError, LLMClient


@dataclass(frozen=True)
class ActionCandidate:
    action: str
    customer_id: str
    target_id: str | None = None
    amount: float | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "customer_id": self.customer_id,
            "target_id": self.target_id,
            "amount": self.amount,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TakenAction:
    action: str
    customer_id: str
    target_id: str | None = None
    amount: float | None = None
    reason: str | None = None
    source: str = "unknown"
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "customer_id": self.customer_id,
            "target_id": self.target_id,
            "amount": self.amount,
            "reason": self.reason,
            "source": self.source,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class ActionReplayDecision:
    requested_action: str
    already_taken: bool
    confidence: float
    reason: str
    checked_with_llm: bool
    matched_action: TakenAction | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_action": self.requested_action,
            "already_taken": self.already_taken,
            "confidence": self.confidence,
            "reason": self.reason,
            "checked_with_llm": self.checked_with_llm,
            "matched_action": self.matched_action.to_dict() if self.matched_action else None,
        }


LLMFactory = Callable[[], Any]


def load_taken_actions(
    customer_id: str,
    *,
    db_path: Path,
    extra_actions: list[TakenAction] | None = None,
) -> list[TakenAction]:
    actions: list[TakenAction] = list(extra_actions or [])
    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        actions.extend(_credit_actions(connection, customer_id))
        actions.extend(_ticket_actions(connection, customer_id))
        actions.extend(_audit_actions(connection, customer_id))
    finally:
        connection.close()
    return actions


def confirm_action_replay(
    message: str,
    candidate: ActionCandidate,
    taken_actions: list[TakenAction],
    *,
    llm_factory: LLMFactory | None = None,
) -> ActionReplayDecision:
    same_action = [action for action in taken_actions if action.action == candidate.action]
    if not same_action:
        return ActionReplayDecision(
            requested_action=candidate.action,
            already_taken=False,
            confidence=1.0,
            reason="no previous matching action type for this customer",
            checked_with_llm=False,
        )

    llm = llm_factory
    if llm is None:
        llm = lambda: LLMClient(model="secondary", timeout_seconds=8)

    deterministic_match = _best_deterministic_match(candidate, same_action)
    try:
        payload = _ask_llm_if_same_action(message, candidate, same_action, llm)
    except (GeminiClientError, OSError, ValueError, RuntimeError):
        if deterministic_match is not None:
            return ActionReplayDecision(
                requested_action=candidate.action,
                already_taken=True,
                confidence=0.92,
                reason="LLM unavailable; exact action evidence already exists",
                checked_with_llm=False,
                matched_action=deterministic_match,
            )
        return _fallback_semantic_decision(candidate, same_action)

    matched = _match_by_index(same_action, payload.get("matched_index"))
    already_taken = bool(payload.get("same_action")) and matched is not None
    if not already_taken and deterministic_match is not None:
        return ActionReplayDecision(
            requested_action=candidate.action,
            already_taken=True,
            confidence=max(0.92, _clamp_confidence(payload.get("confidence"))),
            reason="LLM checked request; exact prior action evidence already exists",
            checked_with_llm=True,
            matched_action=deterministic_match,
        )
    return ActionReplayDecision(
        requested_action=candidate.action,
        already_taken=already_taken,
        confidence=_clamp_confidence(payload.get("confidence")),
        reason=str(payload.get("reason") or "LLM semantic action replay check"),
        checked_with_llm=True,
        matched_action=matched,
    )


def _credit_actions(connection: sqlite3.Connection, customer_id: str) -> list[TakenAction]:
    rows = connection.execute(
        """
        SELECT credit_id, customer_id, amount, reason, applied_to_invoice, applied_at
        FROM credits
        WHERE customer_id = ?
        ORDER BY datetime(applied_at) DESC
        LIMIT 20
        """,
        (customer_id,),
    ).fetchall()
    return [
        TakenAction(
            action="apply_credit",
            customer_id=row["customer_id"],
            target_id=row["applied_to_invoice"] or row["credit_id"],
            amount=float(row["amount"]),
            reason=row["reason"],
            source="credits",
            summary=f"credit {row['credit_id']} for INR {float(row['amount']):g}",
        )
        for row in rows
    ]


def _ticket_actions(connection: sqlite3.Connection, customer_id: str) -> list[TakenAction]:
    rows = connection.execute(
        """
        SELECT ticket_id, customer_id, issue_type, priority, status
        FROM tickets
        WHERE customer_id = ?
        ORDER BY datetime(created_at) DESC
        LIMIT 20
        """,
        (customer_id,),
    ).fetchall()
    return [
        TakenAction(
            action="create_ticket",
            customer_id=row["customer_id"],
            target_id=row["ticket_id"],
            reason=row["issue_type"],
            source="tickets",
            summary=f"{row['issue_type']} ticket {row['ticket_id']} is {row['status']}",
        )
        for row in rows
    ]


def _audit_actions(connection: sqlite3.Connection, customer_id: str) -> list[TakenAction]:
    rows = connection.execute(
        """
        SELECT action_taken
        FROM audit_logs
        WHERE customer_id = ?
        ORDER BY datetime(created_at) DESC
        LIMIT 20
        """,
        (customer_id,),
    ).fetchall()
    actions: list[TakenAction] = []
    for row in rows:
        for payload in _json_list(row["action_taken"]):
            if not isinstance(payload, dict):
                continue
            name = str(payload.get("action") or "")
            if not name:
                continue
            actions.append(
                TakenAction(
                    action=name,
                    customer_id=customer_id,
                    target_id=_first_text(payload, "credit_id", "ticket_id", "invoice_id", "id"),
                    amount=_float_or_none(payload.get("amount")),
                    reason=_first_text(payload, "reason", "issue_type", "status"),
                    source="audit_logs",
                    summary=f"{name} recorded in audit log",
                )
            )
    return actions


def _ask_llm_if_same_action(
    message: str,
    candidate: ActionCandidate,
    taken_actions: list[TakenAction],
    llm_factory: LLMFactory,
) -> dict[str, Any]:
    prompt = (
        "You are an action replay guard for a telecom support agent.\n"
        "Decide whether the new customer request is asking for an action that has already been taken.\n"
        "Only compare operational actions, not empathy or explanation. Return strict JSON.\n\n"
        f"Customer message: {message}\n"
        f"Requested action candidate: {json.dumps(candidate.to_dict(), ensure_ascii=True)}\n"
        f"Previously taken actions: {json.dumps([a.to_dict() for a in taken_actions], ensure_ascii=True)}\n\n"
        "Return this JSON shape only:\n"
        '{"same_action": true|false, "matched_index": number|null, "confidence": 0.0-1.0, "reason": "short"}'
    )
    raw = llm_factory().generate(prompt, temperature=0.0, max_output_tokens=350)
    value = json.loads(_strip_json_fence(raw))
    if not isinstance(value, dict):
        raise ValueError("LLM replay response was not an object")
    return value


def _best_deterministic_match(candidate: ActionCandidate, actions: list[TakenAction]) -> TakenAction | None:
    for action in actions:
        if action.customer_id != candidate.customer_id:
            continue
        if candidate.target_id and action.target_id and candidate.target_id == action.target_id:
            return action
        if candidate.amount is not None and action.amount is not None and abs(candidate.amount - action.amount) < 0.01:
            candidate_reason = (candidate.reason or "").lower()
            action_reason = (action.reason or "").lower()
            if candidate_reason and (candidate_reason in action_reason or action_reason in candidate_reason):
                return action
    return None


def _fallback_semantic_decision(candidate: ActionCandidate, actions: list[TakenAction]) -> ActionReplayDecision:
    match = None
    for action in actions:
        if action.customer_id != candidate.customer_id:
            continue
        if candidate.reason and action.reason and _token_overlap(candidate.reason, action.reason) >= 0.5:
            match = action
            break

    return ActionReplayDecision(
        requested_action=candidate.action,
        already_taken=match is not None,
        confidence=0.72 if match else 0.55,
        reason="LLM unavailable; deterministic semantic fallback used",
        checked_with_llm=False,
        matched_action=match,
    )


def _match_by_index(actions: list[TakenAction], index: Any) -> TakenAction | None:
    if not isinstance(index, int):
        return None
    if index < 0 or index >= len(actions):
        return None
    return actions[index]


def _token_overlap(left: str, right: str) -> float:
    left_tokens = {token for token in left.lower().replace("_", " ").split() if token}
    right_tokens = {token for token in right.lower().replace("_", " ").split() if token}
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()
    return text


def _json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_confidence(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, numeric))
