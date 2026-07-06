from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.db.init_db import DEFAULT_DB_PATH

from .health import loop_penalty_component, sentiment_score_component, tool_call_successful


TRIGGER_ORDER = (
    "policy_exception",
    "score_below_30",
    "anger",
    "loop",
    "churn_risk",
    "tool_failure",
    "refund_over_500",
    "explicit_request",
)


@dataclass(frozen=True)
class HandoffTrigger:
    code: str
    label: str
    severity: str
    reason: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class HandoffTriggerDetection:
    should_handoff: bool
    triggers: list[HandoffTrigger]
    trigger_codes: list[str]
    highest_severity: str | None
    source: str = "handoff_trigger_detection"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class HandoffQueueEntry:
    handoff_id: str
    case_id: str
    customer_id: str
    context_card: dict[str, Any]
    handoff_reason: str
    status: str
    created_at: str
    assigned_to: str | None
    inserted: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class HandoffCustomerMessage:
    message: str
    handoff_id: str | None
    queue_status: str | None
    trigger_codes: list[str]
    includes_context_assurance: bool
    source: str = "handoff_customer_message"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class HandoffAuditEvent:
    case_id: str
    customer_id: str
    session_id: str
    handoff_id: str
    handoff_reason: str
    trigger_codes: list[str]
    queue_status: str
    customer_message: str
    action: dict[str, Any]
    evidence: dict[str, Any]
    audit_log: dict[str, Any]
    source: str = "handoff_audit_event"

    def to_dict(self) -> dict:
        return asdict(self)


def detect_handoff_triggers(
    *,
    policy_result: Any = None,
    health_score: Any = None,
    sentiment: Any = None,
    loop_penalty: Any = None,
    churn_score: Any = None,
    tool_calls: list[Any] | None = None,
    refund_amount: Any = None,
    user_message: str | None = None,
    messages: list[dict[str, object]] | None = None,
    handoff_requested: bool = False,
) -> HandoffTriggerDetection:
    triggers = []

    policy_trigger = _policy_exception_trigger(policy_result)
    if policy_trigger is not None:
        triggers.append(policy_trigger)

    health_trigger = _score_below_30_trigger(health_score)
    if health_trigger is not None:
        triggers.append(health_trigger)

    anger_trigger = _anger_trigger(
        sentiment=sentiment, messages=messages, user_message=user_message)
    if anger_trigger is not None:
        triggers.append(anger_trigger)

    loop_trigger = _loop_trigger(loop_penalty=loop_penalty, messages=messages)
    if loop_trigger is not None:
        triggers.append(loop_trigger)

    churn_trigger = _churn_risk_trigger(churn_score)
    if churn_trigger is not None:
        triggers.append(churn_trigger)

    tool_trigger = _tool_failure_trigger(tool_calls or [])
    if tool_trigger is not None:
        triggers.append(tool_trigger)

    refund_trigger = _refund_over_500_trigger(refund_amount)
    if refund_trigger is not None:
        triggers.append(refund_trigger)

    request_trigger = _explicit_request_trigger(
        user_message=user_message, handoff_requested=handoff_requested)
    if request_trigger is not None:
        triggers.append(request_trigger)

    triggers = sorted(
        triggers, key=lambda trigger: TRIGGER_ORDER.index(trigger.code))
    return HandoffTriggerDetection(
        should_handoff=bool(triggers),
        triggers=triggers,
        trigger_codes=[trigger.code for trigger in triggers],
        highest_severity=_highest_severity(
            trigger.severity for trigger in triggers),
    )


def insert_human_handoff_queue(
    *,
    case_id: str,
    customer_id: str,
    context_card: dict[str, Any],
    handoff_reason: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> HandoffQueueEntry:
    normalized_case_id = _require_text(case_id, "case_id")
    normalized_customer_id = _require_text(customer_id, "customer_id")
    normalized_reason = _require_text(handoff_reason, "handoff_reason")
    if not isinstance(context_card, dict):
        raise ValueError("context_card must be a dict")
    try:
        context_json = json.dumps(
            context_card, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("context_card must be JSON serializable") from exc

    created_at = datetime.utcnow().replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        _validate_handoff_references(
            connection, normalized_case_id, normalized_customer_id)

        existing = connection.execute(
            """
            SELECT handoff_id, case_id, customer_id, context_card, handoff_reason, status, created_at, assigned_to
            FROM human_handoff_queue
            WHERE case_id = ?
            ORDER BY datetime(created_at) DESC, handoff_id DESC
            LIMIT 1
            """,
            (normalized_case_id,),
        ).fetchone()
        if existing is not None:
            return _handoff_queue_entry_from_row(existing, inserted=False)

        handoff_id = f"HND-{uuid4().hex[:12].upper()}"
        connection.execute(
            """
            INSERT INTO human_handoff_queue(
                handoff_id,
                case_id,
                customer_id,
                context_card,
                handoff_reason,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handoff_id,
                normalized_case_id,
                normalized_customer_id,
                context_json,
                normalized_reason,
                "waiting",
                created_at,
            ),
        )
        row = connection.execute(
            """
            SELECT handoff_id, case_id, customer_id, context_card, handoff_reason, status, created_at, assigned_to
            FROM human_handoff_queue
            WHERE handoff_id = ?
            """,
            (handoff_id,),
        ).fetchone()
    return _handoff_queue_entry_from_row(row, inserted=True)


def generate_handoff_customer_message(
    *,
    queue_entry: Any = None,
    trigger_detection: Any = None,
    customer_name: str | None = None,
    issue_summary: str | None = None,
    estimated_wait: str | None = None,
) -> HandoffCustomerMessage:
    queue_payload = _object_payload(queue_entry)
    detection_payload = _object_payload(trigger_detection)
    context_card = queue_payload.get("context_card")
    if not isinstance(context_card, dict):
        context_card = {}

    resolved_customer = _clean_optional_text(customer_name)
    if resolved_customer is None:
        customer = context_card.get("customer")
        if isinstance(customer, dict):
            resolved_customer = _clean_optional_text(customer.get("name"))

    resolved_issue = _clean_optional_text(issue_summary)
    if resolved_issue is None:
        resolved_issue = _issue_summary_from_context(context_card)

    wait_text = _clean_optional_text(estimated_wait)
    handoff_id = _clean_optional_text(queue_payload.get("handoff_id"))
    queue_status = _clean_optional_text(queue_payload.get("status"))
    trigger_codes = _trigger_codes_from_payload(detection_payload)

    greeting = f"{resolved_customer}, I am" if resolved_customer else "I am"
    issue_text = f" for {resolved_issue}" if resolved_issue else ""
    message = (
        f"{greeting} connecting you to a specialist{issue_text}. "
        "I will pass along the context, checks, and notes we already have so you do not need to repeat yourself. "
        "They will continue from here."
    )
    if wait_text:
        message = f"{message} Expected wait: {wait_text}."

    return HandoffCustomerMessage(
        message=message,
        handoff_id=handoff_id,
        queue_status=queue_status,
        trigger_codes=trigger_codes,
        includes_context_assurance=True,
    )


def log_handoff_event_to_audit(
    *,
    case_id: str,
    customer_id: str,
    session_id: str,
    queue_entry: Any,
    customer_message: Any = None,
    trigger_detection: Any = None,
    tools_called: list[Any] | None = None,
    evidence_used: list[Any] | None = None,
    action_taken: list[Any] | None = None,
    policy_dag_path: list[Any] | None = None,
    ujcs: float | None = None,
    policy_status: str | None = None,
    health_score: float | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> HandoffAuditEvent:
    normalized_case_id = _require_text(case_id, "case_id")
    normalized_customer_id = _require_text(customer_id, "customer_id")
    normalized_session_id = _require_text(session_id, "session_id")
    queue_payload = _object_payload(queue_entry)
    handoff_id = _require_text(queue_payload.get(
        "handoff_id"), "queue_entry.handoff_id")
    queue_status = _require_text(
        queue_payload.get("status"), "queue_entry.status")
    handoff_reason = _require_text(queue_payload.get(
        "handoff_reason"), "queue_entry.handoff_reason")
    context_card = queue_payload.get("context_card")
    if not isinstance(context_card, dict):
        context_card = {}

    detection_payload = _object_payload(trigger_detection)
    trigger_codes = _trigger_codes_from_payload(detection_payload)
    message_text = _handoff_message_text(
        customer_message,
        queue_entry=queue_entry,
        trigger_detection=trigger_detection,
    )

    existing = _existing_audit_payload(db_path, normalized_case_id)
    tools_payload = _list_or_existing(tools_called, existing, "tools_called")
    evidence_payload = _list_or_existing(
        evidence_used, existing, "evidence_used")
    actions_payload = _list_or_existing(action_taken, existing, "action_taken")
    path_payload = _list_or_existing(
        policy_dag_path, existing, "policy_dag_path")
    resolved_ujcs = ujcs if ujcs is not None else existing.get("ujcs")
    resolved_policy_status = policy_status if policy_status is not None else existing.get(
        "policy_status")
    resolved_health_score = health_score if health_score is not None else existing.get(
        "health_score")

    handoff_tool = {
        "tool_name": "human_handoff",
        "status": "ok",
        "handoff_id": handoff_id,
        "queue_status": queue_status,
    }
    handoff_action = {
        "action": "human_handoff",
        "handoff_id": handoff_id,
        "status": queue_status,
        "reason": handoff_reason,
        "trigger_codes": trigger_codes,
        "customer_message": message_text,
    }
    handoff_evidence = {
        "type": "handoff_event",
        "handoff_id": handoff_id,
        "queue_status": queue_status,
        "trigger_codes": trigger_codes,
        "context_card": context_card,
    }

    from backend.tools import generate_audit_log

    audit_log = generate_audit_log(
        normalized_case_id,
        customer_id=normalized_customer_id,
        session_id=normalized_session_id,
        tools_called=_append_once(tools_payload, handoff_tool),
        evidence_used=_append_once(evidence_payload, handoff_evidence),
        action_taken=_append_once(actions_payload, handoff_action),
        policy_dag_path=path_payload,
        ujcs=resolved_ujcs,
        policy_status=resolved_policy_status,
        health_score=resolved_health_score,
        handoff_required=True,
        db_path=db_path,
    )
    return HandoffAuditEvent(
        case_id=normalized_case_id,
        customer_id=normalized_customer_id,
        session_id=normalized_session_id,
        handoff_id=handoff_id,
        handoff_reason=handoff_reason,
        trigger_codes=trigger_codes,
        queue_status=queue_status,
        customer_message=message_text,
        action=handoff_action,
        evidence=handoff_evidence,
        audit_log=audit_log,
    )


def _policy_exception_trigger(policy_result: Any) -> HandoffTrigger | None:
    if policy_result is None:
        return None
    payload = _object_payload(policy_result)
    action = _payload_text(payload, "action", "policy_action",
                           "recommended_action", "leaf_action")
    status = _payload_text(payload, "policy_status", "status", "result")
    reason = _payload_text(payload, "reason", "handoff_reason",
                           "message", default="Policy exception requires review.")
    exception_flag = bool(
        payload.get("policy_exception")
        or payload.get("exception")
        or payload.get("requires_handoff")
        or payload.get("blocked")
    )
    if (
        exception_flag
        or action == "handoff_human"
        or status in {"policy_exception", "exception", "needs_review", "non_compliant", "blocked"}
    ):
        return HandoffTrigger(
            code="policy_exception",
            label="Policy exception",
            severity="high",
            reason=reason,
            evidence=_compact_evidence(
                payload, ("policy_name", "policy_status", "status", "action", "reason")),
        )
    return None


def _score_below_30_trigger(health_score: Any) -> HandoffTrigger | None:
    score = _score_value(health_score, maximum=100)
    if score is None or score >= 30:
        return None
    return HandoffTrigger(
        code="score_below_30",
        label="Conversation health below 30",
        severity="critical",
        reason="Conversation health score dropped below the human handoff threshold.",
        evidence={"health_score": score, "threshold": 30},
    )


def _anger_trigger(*, sentiment: Any, messages: list[dict[str, object]] | None, user_message: str | None) -> HandoffTrigger | None:
    component = sentiment
    if component is None and messages:
        component = sentiment_score_component(messages)
    payload = _object_payload(component)
    label = _payload_text(payload, "label", "text") if component is not None else None
    score = _score_value(component, maximum=1)
    text = _normalized_text(user_message)
    anger_terms = ("angry", "furious", "terrible", "useless",
                   "ridiculous", "hate this", "stop the bot")
    text_is_angry = any(term in text for term in anger_terms)
    if label in {"angry", "furious", "hostile"} or (score is not None and score <= 0.1) or text_is_angry:
        return HandoffTrigger(
            code="anger",
            label="Customer anger",
            severity="high",
            reason="Customer sentiment indicates anger or hostile frustration.",
            evidence={"sentiment_label": label,
                      "sentiment_score": score, "matched_text": text_is_angry},
        )
    return None


def _loop_trigger(*, loop_penalty: Any, messages: list[dict[str, object]] | None) -> HandoffTrigger | None:
    component = loop_penalty
    if component is None and messages:
        component = loop_penalty_component(messages)
    payload = _object_payload(component)
    value = _score_value(component, maximum=1)
    repeated_count = _optional_int(payload.get("repeated_question_count"))
    if (value is not None and value >= 1.0) or (repeated_count is not None and repeated_count >= 3):
        return HandoffTrigger(
            code="loop",
            label="Repeated-question loop",
            severity="medium",
            reason="Customer is repeating the same question enough times to indicate a loop.",
            evidence={
                "loop_penalty": value,
                "repeated_question_count": repeated_count,
                "repeated_question": payload.get("repeated_question"),
            },
        )
    return None


def _churn_risk_trigger(churn_score: Any) -> HandoffTrigger | None:
    score = _score_value(churn_score, maximum=1)
    if score is None or score < 0.7:
        return None
    return HandoffTrigger(
        code="churn_risk",
        label="High churn risk",
        severity="high",
        reason="Customer churn risk is high enough to require human retention handling.",
        evidence={"churn_score": score, "threshold": 0.7},
    )


def _tool_failure_trigger(tool_calls: list[Any]) -> HandoffTrigger | None:
    if not isinstance(tool_calls, list):
        raise ValueError("tool_calls must be a list when provided")
    failed = []
    for item in tool_calls:
        payload = _object_payload(item)
        name = str(payload.get("tool_name") or payload.get("name")
                   or payload.get("tool") or "unknown").strip()
        if not _tool_call_successful(payload):
            failed.append({"name": name, "status": payload.get(
                "status"), "error": payload.get("error")})
    if not failed:
        return None
    return HandoffTrigger(
        code="tool_failure",
        label="Tool failure",
        severity="high",
        reason="A required backend tool failed or returned an error.",
        evidence={"failed_tools": failed},
    )


def _refund_over_500_trigger(refund_amount: Any) -> HandoffTrigger | None:
    amount = _money_value(refund_amount)
    if amount is None or amount <= 500:
        return None
    return HandoffTrigger(
        code="refund_over_500",
        label="Refund over INR 500",
        severity="high",
        reason="Refund amount exceeds the INR 500 automatic-review limit.",
        evidence={"refund_amount": amount, "threshold": 500},
    )


def _explicit_request_trigger(*, user_message: str | None, handoff_requested: bool) -> HandoffTrigger | None:
    text = _normalized_text(user_message)
    requested = bool(handoff_requested) or any(
        phrase in text
        for phrase in (
            "human agent",
            "real person",
            "talk to a person",
            "speak to someone",
            "supervisor",
            "manager",
            "escalate",
            "handoff",
        )
    )
    if not requested:
        return None
    return HandoffTrigger(
        code="explicit_request",
        label="Explicit human request",
        severity="critical",
        reason="Customer explicitly requested a human or escalation.",
        evidence={"handoff_requested": bool(
            handoff_requested), "message": user_message},
    )


def _validate_handoff_references(
    connection: sqlite3.Connection,
    case_id: str,
    customer_id: str,
) -> None:
    customer_row = connection.execute(
        "SELECT customer_id FROM customers WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()
    if customer_row is None:
        raise ValueError(f"customer {customer_id!r} not found")
    audit_row = connection.execute(
        "SELECT customer_id FROM audit_logs WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    if audit_row is None:
        raise ValueError(f"audit case {case_id!r} not found")
    if audit_row["customer_id"] != customer_id:
        raise ValueError(
            f"audit case {case_id!r} does not belong to customer {customer_id!r}")


def _handoff_queue_entry_from_row(row: sqlite3.Row, *, inserted: bool) -> HandoffQueueEntry:
    return HandoffQueueEntry(
        handoff_id=row["handoff_id"],
        case_id=row["case_id"],
        customer_id=row["customer_id"],
        context_card=_json_dict(row["context_card"]),
        handoff_reason=row["handoff_reason"],
        status=row["status"],
        created_at=row["created_at"],
        assigned_to=row["assigned_to"],
        inserted=inserted,
    )


def _json_dict(raw_value: str | None) -> dict[str, Any]:
    if raw_value is None:
        return {}
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_list(raw_value: str | None) -> list[Any]:
    if raw_value is None:
        return []
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _existing_audit_payload(db_path: Path, case_id: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT tools_called, evidence_used, action_taken, policy_dag_path, ujcs, policy_status, health_score
            FROM audit_logs
            WHERE case_id = ?
            """,
            (case_id,),
        ).fetchone()
    if row is None:
        return {}
    return {
        "tools_called": _json_list(row["tools_called"]),
        "evidence_used": _json_list(row["evidence_used"]),
        "action_taken": _json_list(row["action_taken"]),
        "policy_dag_path": _json_list(row["policy_dag_path"]),
        "ujcs": float(row["ujcs"]) if row["ujcs"] is not None else None,
        "policy_status": row["policy_status"],
        "health_score": float(row["health_score"]) if row["health_score"] is not None else None,
    }


def _list_or_existing(value: list[Any] | None, existing: dict[str, Any], field_name: str) -> list[Any]:
    if value is None:
        current = existing.get(field_name, [])
        return list(current) if isinstance(current, list) else []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list when provided")
    return list(value)


def _append_once(items: list[Any], item: dict[str, Any]) -> list[Any]:
    for existing in items:
        if existing == item:
            return items
    return [*items, item]


def _handoff_message_text(
    customer_message: Any,
    *,
    queue_entry: Any,
    trigger_detection: Any,
) -> str:
    if customer_message is None:
        customer_message = generate_handoff_customer_message(
            queue_entry=queue_entry,
            trigger_detection=trigger_detection,
        )
    payload = _object_payload(customer_message)
    message = payload.get("message")
    if message is None:
        message = payload.get("text")
    return _require_text(message, "customer_message.message")


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _issue_summary_from_context(context_card: dict[str, Any]) -> str | None:
    issues = context_card.get("issues_remaining")
    if not isinstance(issues, list) or not issues:
        issues = context_card.get("issues_detected")
    if not isinstance(issues, list):
        return None
    labels = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        label = _clean_optional_text(issue.get("label") or issue.get("intent"))
        if label:
            labels.append(label.replace("_", " "))
    if not labels:
        return None
    return ", ".join(labels[:2])


def _trigger_codes_from_payload(payload: dict[str, Any]) -> list[str]:
    raw_codes = payload.get("trigger_codes")
    if raw_codes is None and isinstance(payload.get("triggers"), list):
        raw_codes = [
            trigger.get("code")
            for trigger in payload["triggers"]
            if isinstance(trigger, dict)
        ]
    if not isinstance(raw_codes, list):
        return []
    codes = []
    seen = set()
    for code in raw_codes:
        normalized = _clean_optional_text(code)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        codes.append(normalized)
    return codes


def _object_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    elif hasattr(value, "__dict__") and not isinstance(value, (str, int, float, bool)):
        value = vars(value)
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return {"text": value}
    return {"value": value}


def _payload_text(payload: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        if key in payload and payload[key] is not None:
            return str(payload[key]).strip().lower()
    return default


def _compact_evidence(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload[key] for key in keys if key in payload and payload[key] is not None}


def _score_value(value: Any, *, maximum: float) -> float | None:
    payload = _object_payload(value)
    raw = payload.get("score")
    if raw is None:
        raw = payload.get("value", value)
    if isinstance(raw, dict):
        return None
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    if score < 0 or score > maximum:
        raise ValueError(f"score must be between 0 and {maximum:g}")
    return round(score, 2 if maximum == 100 else 4)


def _money_value(value: Any) -> float | None:
    if value is None:
        return None
    payload = _object_payload(value)
    raw = payload.get("amount")
    if raw is None:
        raw = payload.get("refund_amount", value)
    if isinstance(raw, str):
        raw = re.sub(r"[^0-9.]", "", raw)
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        raise ValueError("refund_amount must not be negative")
    return round(amount, 2)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tool_call_successful(tool_call: dict[str, Any]) -> bool:
    return tool_call_successful(tool_call)


def _normalized_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _highest_severity(severities) -> str | None:
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    highest = None
    for severity in severities:
        if highest is None or order.get(severity, -1) > order.get(highest, -1):
            highest = severity
    return highest
