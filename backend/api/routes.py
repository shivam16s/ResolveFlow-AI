from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.agent.policy_graph import PolicyActionBlocked, PolicyGraphValidator
from backend.db.seed_demo_dashboard import seed_demo_dashboard
from backend.dashboard import render_case_audit_log_tabs, render_case_handoff_tab
from backend.tools import (
    apply_credit,
    check_duplicate_charge,
    check_outage_status,
    change_plan,
    create_ticket,
    generate_audit_log,
    generate_context_card,
    generate_handoff_summary,
    generate_opening_line,
    get_invoice_history,
    lookup_customer,
    retrieve_policy,
    run_router_diagnostic,
    schedule_technician,
)
from . import dashboard_routes as dashboard_data


health_router = APIRouter(prefix="/api", tags=["health"])
tools_router = APIRouter(prefix="/api/tools", tags=["tools"])
dashboard_router = APIRouter(prefix="/api", tags=["dashboard"])

_AUDIT_LOG_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_AUDIT_LOG_LOCKS_GUARD = threading.Lock()

# ---------------------------------------------------------------------------
# The app has no login/session system, so this is a shared-secret gate rather
# than real per-operator auth: it stops casual scanning and unintentional
# exposure of the write-side agent-desk endpoints once this is deployed to a
# public URL, not a determined attacker (the token ships in the frontend
# bundle, which anyone can read). That's the right tradeoff for a demo with no
# real user accounts; do not treat this as production-grade access control.
# ---------------------------------------------------------------------------
_AGENT_DESK_TOKEN = os.environ.get("RESOLVEFLOW_AGENT_DESK_TOKEN", "")
if not _AGENT_DESK_TOKEN:
    logging.warning(
        "RESOLVEFLOW_AGENT_DESK_TOKEN is not set -- agent-desk write "
        "endpoints (reply/resolve) are unauthenticated. Set it before "
        "deploying to a public URL."
    )


def _require_agent_desk_token(request: Request) -> None:
    if not _AGENT_DESK_TOKEN:
        return
    supplied = request.headers.get("x-agent-desk-token", "")
    if not hmac.compare_digest(supplied, _AGENT_DESK_TOKEN):
        raise HTTPException(status_code=403, detail="invalid or missing agent-desk token")


class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    service: str = Field(..., examples=["resolveflow-api"])
    version: str = Field(..., examples=["0.1.0"])
    timestamp: str


class ToolResponse(BaseModel):
    tool_name: str
    ok: bool
    result: dict[str, Any]


class ApplyCreditRequest(BaseModel):
    customer_id: str
    amount: float
    reason: str
    policy_context: dict[str, Any]
    policy_name: str = "service_credit_dag"
    applied_to_invoice: str | None = None


class CreateTicketRequest(BaseModel):
    customer_id: str
    issue_type: str
    priority: str = "medium"
    status: str = "open"
    policy_name: str | None = None
    policy_context: dict[str, Any] | None = None


class ScheduleTechnicianRequest(BaseModel):
    customer_id: str
    time_slot: str
    policy_context: dict[str, Any]
    policy_name: str = "technician_dispatch_dag"
    ticket_id: str | None = None


class ChangePlanRequest(BaseModel):
    customer_id: str
    new_plan_id: str
    policy_context: dict[str, Any]
    policy_name: str = "plan_downgrade_dag"
    effective_date: str | None = None


class HandoffSummaryRequest(BaseModel):
    conversation_id: str
    handoff_reason: str | None = None


class ContextCardRequest(BaseModel):
    conversation_id: str
    handoff_reason: str | None = None


class OpeningLineRequest(BaseModel):
    conversation_id: str | None = None
    context_card: dict[str, Any] | None = None
    handoff_reason: str | None = None


class AuditLogRequest(BaseModel):
    case_id: str
    customer_id: str
    session_id: str
    tools_called: list[Any]
    evidence_used: list[Any]
    action_taken: list[Any]
    policy_dag_path: list[Any]
    policy_name: str | None = None
    ujcs: float | None = None
    policy_status: str | None = None
    health_score: float | None = None
    handoff_required: bool = False


class HumanReplyRequest(BaseModel):
    message: str
    agent_name: str = "Human specialist"


class ResolveHandoffRequest(BaseModel):
    resolution_note: str = "Resolved by human specialist"
    agent_name: str = "Human specialist"


class SecurityAttackRequest(BaseModel):
    attack_id: str
    prompt: str


class OutageTriggerRequest(BaseModel):
    location: str
    duration_hours: float = 6
    verified: bool = True
    outage_id: str | None = None
    initiate_proactive: bool = True
    credit_amount: float = 100


@health_router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="resolveflow-api",
        version="0.1.0",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@dashboard_router.get("/cases/{case_id}/handoff", response_class=HTMLResponse)
def case_handoff_tab_endpoint(case_id: str, request: Request) -> HTMLResponse:
    try:
        rendered = render_case_handoff_tab(
            case_id, db_path=request.app.state.db_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if rendered is None:
        raise HTTPException(
            status_code=404, detail=f"case {case_id!r} not found")
    return HTMLResponse(rendered.html)


@dashboard_router.get("/cases/{case_id}/audit_log")
def case_audit_log_tabs_endpoint(case_id: str, request: Request):
    """Return HTML by default, or JSON when the client asks for application/json."""
    if "application/json" not in request.headers.get("accept", ""):
        try:
            rendered = render_case_audit_log_tabs(
                case_id, db_path=request.app.state.db_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if rendered is None:
            raise HTTPException(
                status_code=404, detail=f"case {case_id!r} not found")
        return HTMLResponse(rendered.html)

    db = request.app.state.db_path
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM audit_logs WHERE case_id = ?", (case_id,)
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404, detail=f"case {case_id!r} not found")

    tools = _safe_json_list(row["tools_called"])
    evidence = _safe_json_list(row["evidence_used"])
    actions = _safe_json_list(row["action_taken"])
    dag = _safe_json_list(row["policy_dag_path"])
    tool_names = [t.get("tool_name", "") if isinstance(
        t, dict) else str(t) for t in tools]
    action_text = ", ".join(
        a.get("action", str(a)) if isinstance(a, dict) else str(a) for a in actions
    ) or "no actions taken"
    human_readable = (
        f"Case {case_id}: {len(tool_names)} tool(s) called. "
        f"Policy: {row['policy_status']}. "
        f"Action: {action_text}."
    )
    return JSONResponse({
        "case_id": row["case_id"],
        "customer_id": row["customer_id"],
        "session_id": row["session_id"],
        "tools_called": tool_names,
        "evidence_used": [str(e) for e in evidence],
        "action_taken": action_text,
        "policy_dag_path": [str(n) for n in dag],
        "ujcs": row["ujcs"],
        "policy_status": row["policy_status"] or "pending",
        "human_readable": human_readable,
        "created_at": row["created_at"],
    })


# ── Dashboard JSON endpoints ──────────────────────────────────────────────────

@dashboard_router.get("/dashboard/overview")
def dashboard_overview(request: Request) -> JSONResponse:
    """KPI summary for the overview page."""
    return JSONResponse(dashboard_data.dashboard_overview(request))


@dashboard_router.get("/dashboard/charts")
def dashboard_charts(request: Request) -> JSONResponse:
    """Chart data: 7-day trend, issue types, tool frequency, health distribution."""
    return JSONResponse(dashboard_data.dashboard_charts(request))


@dashboard_router.get("/telemetry/summary")
def telemetry_summary(request: Request) -> JSONResponse:
    """Ops telemetry summary from recorded chat turns."""
    with sqlite3.connect(request.app.state.db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry (
              telemetry_id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              customer_id TEXT NOT NULL REFERENCES customers(customer_id),
              turn_count INTEGER NOT NULL CHECK (turn_count >= 0),
              latency_ms REAL NOT NULL CHECK (latency_ms >= 0),
              input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
              output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
              total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
              stage_breakdown TEXT NOT NULL DEFAULT '{}',
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        rows = connection.execute(
            """
            SELECT latency_ms, total_tokens
            FROM telemetry
            ORDER BY datetime(created_at) DESC
            LIMIT 500
            """
        ).fetchall()
    latencies = sorted(float(row["latency_ms"]) for row in rows)
    token_counts = [int(row["total_tokens"] or 0) for row in rows]
    avg_tokens = round(sum(token_counts) / len(token_counts), 1) if token_counts else 0
    estimated_cost_inr = round((sum(token_counts) / 1000) * 0.18, 4)
    return JSONResponse({
        "turns": len(rows),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "avg_tokens_per_resolution": avg_tokens,
        "estimated_cost_inr": estimated_cost_inr,
    })


@dashboard_router.post("/demo/reset")
def reset_demo_data(request: Request) -> JSONResponse:
    """Restore the seeded demo database and clear live in-process chat state."""
    result = seed_demo_dashboard(Path(request.app.state.db_path))
    from . import chat_routes

    with chat_routes._CHAT_STATE_LOCKS_GUARD:
        chat_routes._CHAT_STATES.clear()
        chat_routes._CHAT_STATE_LOCKS.clear()
        chat_routes._MEMORY_CANCELLATION_REQUESTS.clear()
    return JSONResponse({"ok": True, "reset": result})


@dashboard_router.get("/agent-desk/queue")
def agent_desk_queue(request: Request) -> JSONResponse:
    """Live handoff queue for the human agent desk."""
    _require_agent_desk_token(request)
    with sqlite3.connect(request.app.state.db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                handoff.handoff_id,
                handoff.case_id,
                handoff.customer_id,
                handoff.context_card,
                handoff.handoff_reason,
                handoff.status,
                handoff.created_at,
                handoff.assigned_to,
                customer.name AS customer_name,
                customer.plan_id,
                customer.risk_level,
                customer.churn_score,
                conversation.session_id,
                conversation.messages,
                conversation.intents,
                conversation.final_status,
                audit.health_score,
                audit.policy_status,
                audit.ujcs
            FROM human_handoff_queue handoff
            JOIN customers customer ON customer.customer_id = handoff.customer_id
            LEFT JOIN audit_logs audit ON audit.case_id = handoff.case_id
            LEFT JOIN conversations conversation ON conversation.session_id = audit.session_id
            ORDER BY
                CASE handoff.status
                    WHEN 'waiting' THEN 0
                    WHEN 'assigned' THEN 1
                    ELSE 2
                END,
                datetime(handoff.created_at) DESC,
                handoff.handoff_id DESC
            """
        ).fetchall()
    return JSONResponse({
        "queue": [_agent_desk_queue_row(row) for row in rows],
        "total": len(rows),
    })


@dashboard_router.get("/agent-desk/proactive")
def agent_desk_proactive_contacts(request: Request) -> JSONResponse:
    """Proactive customer contacts initiated by ResolveFlow."""
    _require_agent_desk_token(request)
    with sqlite3.connect(request.app.state.db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                conversation.session_id,
                conversation.customer_id,
                conversation.messages,
                conversation.tools_called,
                conversation.created_at,
                customer.name AS customer_name,
                customer.location,
                customer.risk_level
            FROM conversations conversation
            JOIN customers customer ON customer.customer_id = conversation.customer_id
            WHERE conversation.session_id LIKE 'proactive-%'
            ORDER BY datetime(conversation.created_at) DESC, conversation.session_id DESC
            """
        ).fetchall()
    contacts = []
    for row in rows:
        messages = _loads_json(row["messages"])
        tools_called = _loads_json(row["tools_called"])
        first_message = next(
            (
                str(message.get("content"))
                for message in messages
                if isinstance(message, dict) and message.get("content")
            ),
            "",
        )
        credit_tool = next(
            (
                tool
                for tool in tools_called
                if isinstance(tool, dict) and tool.get("tool_name") == "apply_credit"
            ),
            {},
        )
        contacts.append({
            "session_id": row["session_id"],
            "customer_id": row["customer_id"],
            "customer_name": row["customer_name"],
            "location": row["location"],
            "risk_level": row["risk_level"],
            "created_at": row["created_at"],
            "message": first_message,
            "credit": credit_tool.get("result") if isinstance(credit_tool, dict) else None,
            "status": "credited" if credit_tool.get("success") else "blocked",
        })
    return JSONResponse({"contacts": contacts, "total": len(contacts)})


@dashboard_router.get("/agent-desk/handoffs/{handoff_id}")
def agent_desk_handoff_detail(handoff_id: str, request: Request) -> JSONResponse:
    """Full takeover context for one human handoff."""
    _require_agent_desk_token(request)
    normalized_id = " ".join(handoff_id.strip().split())
    if not normalized_id:
        raise HTTPException(status_code=422, detail="handoff_id must not be empty")

    with sqlite3.connect(request.app.state.db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                handoff.handoff_id,
                handoff.case_id,
                handoff.customer_id,
                handoff.context_card,
                handoff.handoff_reason,
                handoff.status,
                handoff.created_at,
                handoff.assigned_to,
                customer.name AS customer_name,
                customer.plan_id,
                customer.risk_level,
                customer.churn_score,
                conversation.session_id,
                conversation.messages,
                conversation.intents,
                conversation.tools_called,
                conversation.health_scores,
                conversation.final_status,
                audit.health_score,
                audit.policy_status,
                audit.ujcs,
                audit.policy_dag_path
            FROM human_handoff_queue handoff
            JOIN customers customer ON customer.customer_id = handoff.customer_id
            LEFT JOIN audit_logs audit ON audit.case_id = handoff.case_id
            LEFT JOIN conversations conversation ON conversation.session_id = audit.session_id
            WHERE handoff.handoff_id = ?
            """,
            (normalized_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"handoff {normalized_id!r} not found")

    context_card = None
    opening_line = None
    if row["session_id"]:
        context_card = generate_context_card(
            row["session_id"],
            handoff_reason=row["handoff_reason"],
            db_path=Path(request.app.state.db_path),
        )
        opening_line = generate_opening_line(
            row["session_id"],
            handoff_reason=row["handoff_reason"],
            db_path=Path(request.app.state.db_path),
        )
    if context_card is None:
        context_card = _safe_json_dict(row["context_card"])
    if opening_line is None:
        opening_line = generate_opening_line(
            context_card=context_card,
            handoff_reason=row["handoff_reason"],
        )

    return JSONResponse({
        **_agent_desk_queue_row(row),
        "transcript": _loads_json(row["messages"]),
        "tools_called": _loads_json(row["tools_called"]),
        "health_scores": _loads_json(row["health_scores"]),
        "policy_dag_path": _loads_json(row["policy_dag_path"]),
        "context_card": context_card,
        "opening_line": opening_line,
        "copilot_suggestions": _agent_desk_copilot_suggestions(
            row=row,
            context_card=context_card,
            opening_line=opening_line,
        ),
    })


@dashboard_router.post("/agent-desk/handoffs/{handoff_id}/reply")
def agent_desk_human_reply(
    handoff_id: str,
    payload: HumanReplyRequest,
    request: Request,
) -> JSONResponse:
    """Post a human specialist reply into the customer's conversation thread."""
    _require_agent_desk_token(request)
    normalized_id = " ".join(handoff_id.strip().split())
    normalized_message = " ".join(payload.message.strip().split())
    normalized_agent = " ".join(payload.agent_name.strip().split()) or "Human specialist"
    if not normalized_id:
        raise HTTPException(status_code=422, detail="handoff_id must not be empty")
    if not normalized_message:
        raise HTTPException(status_code=422, detail="message must not be empty")

    db_path = Path(request.app.state.db_path)
    # Locked per handoff_id + BEGIN IMMEDIATE: two concurrent replies to the
    # same handoff otherwise read the same messages array before either
    # writes, silently dropping one agent's reply. Also serializes against
    # append_human_reply_to_session below, which has its own unguarded
    # read-modify-write on chat_session_state.
    with _audit_log_lock(db_path, f"handoff:{normalized_id}"):
        with sqlite3.connect(db_path, timeout=30.0) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT
                    handoff.handoff_id,
                    handoff.case_id,
                    handoff.customer_id,
                    handoff.status,
                    audit.session_id,
                    conversation.messages
                FROM human_handoff_queue handoff
                JOIN audit_logs audit ON audit.case_id = handoff.case_id
                LEFT JOIN conversations conversation ON conversation.session_id = audit.session_id
                WHERE handoff.handoff_id = ?
                """,
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"handoff {normalized_id!r} not found")
            if not row["session_id"]:
                raise HTTPException(
                    status_code=409, detail="handoff has no conversation session")

            reply = {
                "role": "human_agent",
                "agent_name": normalized_agent,
                "content": normalized_message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            messages = _loads_json(row["messages"])
            existing_reply = next(
                (
                    message
                    for message in messages
                    if isinstance(message, dict)
                    and message.get("role") == "human_agent"
                    and message.get("agent_name") == normalized_agent
                    and " ".join(str(message.get("content", "")).strip().split()) == normalized_message
                ),
                None,
            )
            already_replied = existing_reply is not None
            if already_replied:
                reply = existing_reply
            else:
                messages.append(reply)
                connection.execute(
                    """
                    UPDATE conversations
                    SET messages = ?
                    WHERE session_id = ?
                    """,
                    (json.dumps(messages, ensure_ascii=True), row["session_id"]),
                )
            if row["status"] == "waiting":
                connection.execute(
                    """
                    UPDATE human_handoff_queue
                    SET status = 'assigned',
                        assigned_to = COALESCE(assigned_to, ?)
                    WHERE handoff_id = ?
                    """,
                    (normalized_agent, normalized_id),
                )

        from .chat_routes import append_human_reply_to_session

        chat_reply = append_human_reply_to_session(
            customer_id=row["customer_id"],
            session_id=row["session_id"],
            message=normalized_message,
            agent_name=normalized_agent,
            db_path=db_path,
        )
    return JSONResponse({
        "ok": True,
        "handoff_id": normalized_id,
        "case_id": row["case_id"],
        "customer_id": row["customer_id"],
        "session_id": row["session_id"],
        "reply": chat_reply,
        "already_replied": already_replied,
    })


@dashboard_router.post("/agent-desk/handoffs/{handoff_id}/resolve")
def agent_desk_resolve_handoff(
    handoff_id: str,
    payload: ResolveHandoffRequest,
    request: Request,
) -> JSONResponse:
    """Resolve a handoff and write the close action to the audit trail."""
    _require_agent_desk_token(request)
    normalized_id = " ".join(handoff_id.strip().split())
    note = " ".join(payload.resolution_note.strip().split()) or "Resolved by human specialist"
    agent_name = " ".join(payload.agent_name.strip().split()) or "Human specialist"
    if not normalized_id:
        raise HTTPException(status_code=422, detail="handoff_id must not be empty")

    resolved_at = datetime.now(timezone.utc).isoformat()
    action = {
        "action": "human_handoff_resolved",
        "handoff_id": normalized_id,
        "agent_name": agent_name,
        "resolution_note": note,
        "timestamp": resolved_at,
    }
    # Locked per handoff_id + BEGIN IMMEDIATE: without this, two concurrent
    # resolve calls (double-click, retry) both read the same action_taken
    # array before either writes, so the second UPDATE silently drops the
    # first agent's resolution note from the audit trail.
    with _audit_log_lock(request.app.state.db_path, f"handoff:{normalized_id}"):
        with sqlite3.connect(request.app.state.db_path, timeout=30.0) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT
                    handoff.handoff_id,
                    handoff.case_id,
                    handoff.customer_id,
                    handoff.status,
                    audit.session_id,
                    audit.action_taken
                FROM human_handoff_queue handoff
                JOIN audit_logs audit ON audit.case_id = handoff.case_id
                WHERE handoff.handoff_id = ?
                """,
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"handoff {normalized_id!r} not found")
            if not row["session_id"]:
                raise HTTPException(
                    status_code=409, detail="handoff has no conversation session")
            if row["status"] == "resolved":
                # Idempotent: a double-click/retry on an already-resolved
                # handoff is a no-op, not a second audit entry.
                return JSONResponse({
                    "ok": True,
                    "handoff_id": normalized_id,
                    "case_id": row["case_id"],
                    "customer_id": row["customer_id"],
                    "session_id": row["session_id"],
                    "status": "resolved",
                    "audit_action": None,
                    "already_resolved": True,
                })

            actions = _loads_json(row["action_taken"])
            actions.append(action)
            connection.execute(
                """
                UPDATE human_handoff_queue
                SET status = 'resolved',
                    assigned_to = COALESCE(assigned_to, ?)
                WHERE handoff_id = ?
                """,
                (agent_name, normalized_id),
            )
            connection.execute(
                """
                UPDATE conversations
                SET final_status = 'resolved',
                    completed_at = COALESCE(completed_at, ?)
                WHERE session_id = ?
                """,
                (resolved_at, row["session_id"]),
            )
            connection.execute(
                """
                UPDATE audit_logs
                SET action_taken = ?
                WHERE case_id = ?
                """,
                (json.dumps(actions, ensure_ascii=True), row["case_id"]),
            )

    return JSONResponse({
        "ok": True,
        "handoff_id": normalized_id,
        "case_id": row["case_id"],
        "customer_id": row["customer_id"],
        "session_id": row["session_id"],
        "status": "resolved",
        "audit_action": action,
    })


@dashboard_router.post("/security/attack")
def security_attack(payload: SecurityAttackRequest, request: Request) -> JSONResponse:
    """Run a red-team prompt through policy-DAG blocking logic."""
    attack = _security_attack_plan(payload.attack_id, payload.prompt)
    validation = PolicyGraphValidator().run(
        attack["policy_name"],
        attack["context"],
    )
    blocked = validation.action != attack["requested_action"]
    if not blocked:
        raise HTTPException(
            status_code=409,
            detail="attack did not trigger a policy block; review attack fixture",
        )

    stopped_node = validation.path[-1] if validation.path else attack["policy_name"]
    receipt_trail = [
        {
            "stage": "prompt_received",
            "status": "recorded",
            "detail": attack["prompt"],
        },
        {
            "stage": "policy_dag",
            "status": "traversed",
            "detail": " -> ".join(validation.path),
        },
        {
            "stage": "action_gate",
            "status": "blocked",
            "detail": (
                f"requested {attack['requested_action']} but DAG reached "
                f"{validation.action}"
            ),
        },
    ]
    audit_case_id = _write_security_attack_audit(
        db_path=Path(request.app.state.db_path),
        attack=attack,
        validation=validation.to_dict(),
        stopped_node=stopped_node,
        receipt_trail=receipt_trail,
    )
    matched_by = attack.get("matched_by", "explicit_attack_id")
    return JSONResponse({
        "audit_case_id": audit_case_id,
        "attack_id": attack["attack_id"],
        "prompt": attack["prompt"],
        "status": "blocked",
        "blocked_action": attack["requested_action"],
        "policy_name": attack["policy_name"],
        "stopped_node": stopped_node,
        "reached_action": validation.action,
        "dag_path": validation.path,
        "ujcs": validation.ujcs,
        "receipt_trail": receipt_trail,
        "matched_by": matched_by,
        "disclosure": (
            f"Your prompt didn't match one of the {len(_KNOWN_ATTACK_IDS)} named attack IDs, so it was "
            f"keyword-matched to the closest scenario ('{attack['attack_id']}') and evaluated against "
            "that scenario's policy context -- the DAG traversal and block below are real, but they are "
            "not a custom evaluation of your exact wording."
            if matched_by == "keyword_heuristic" else None
        ),
        "blocked_reason": (
            f"Policy DAG stopped {attack['requested_action']} at {stopped_node}; "
            f"allowed action was {validation.action}."
        ),
    })


@dashboard_router.post("/outages/trigger")
def trigger_verified_outage(
    payload: OutageTriggerRequest,
    request: Request,
) -> JSONResponse:
    """Create a verified outage and find affected customers by location."""
    _require_agent_desk_token(request)
    location = " ".join(payload.location.strip().split())
    if not location:
        raise HTTPException(status_code=422, detail="location must not be empty")
    if payload.duration_hours < 0:
        raise HTTPException(status_code=422, detail="duration_hours must be >= 0")
    if payload.credit_amount <= 0:
        raise HTTPException(status_code=422, detail="credit_amount must be > 0")

    created_at = datetime.now(timezone.utc)
    # Deliberately excludes the timestamp: apply_credit dedupes proactive
    # credits by (customer_id, amount, reason), and the reason string embeds
    # outage_id, so a stable-per-location default lets retries/double-clicks
    # on "Simulate outage" resolve to the SAME outage instead of minting a
    # fresh id (and a fresh credit) every time. Pass an explicit outage_id to
    # intentionally simulate a second, distinct outage at the same location.
    outage_id = payload.outage_id or f"OUT-{hashlib.sha256(location.lower().encode('utf-8')).hexdigest()[:10].upper()}"
    with sqlite3.connect(request.app.state.db_path) as connection:
        connection.row_factory = sqlite3.Row
        customer_rows = connection.execute(
            """
            SELECT customer_id, name, location, risk_level
            FROM customers
            ORDER BY customer_id
            """
        ).fetchall()
        affected_rows = [
            row for row in customer_rows
            if _locations_match(location, row["location"])
        ]
        affected_customers = [row["customer_id"] for row in affected_rows] if payload.verified else []
        connection.execute(
            """
            INSERT INTO outages (
                outage_id,
                location,
                start_time,
                end_time,
                duration_hours,
                verified,
                affected_customers
            ) VALUES (?, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(outage_id) DO UPDATE SET
                location = excluded.location,
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                duration_hours = excluded.duration_hours,
                verified = excluded.verified,
                affected_customers = excluded.affected_customers
            """,
            (
                outage_id,
                location,
                created_at.isoformat(),
                payload.duration_hours,
                1 if payload.verified else 0,
                json.dumps(affected_customers, ensure_ascii=True),
            ),
        )

    proactive_contacts = []
    if payload.verified and payload.initiate_proactive:
        proactive_contacts = _initiate_proactive_outage_contacts(
            db_path=Path(request.app.state.db_path),
            outage_id=outage_id,
            location=location,
            duration_hours=payload.duration_hours,
            affected_customers=[
                {
                    "customer_id": row["customer_id"],
                    "name": row["name"],
                    "location": row["location"],
                    "risk_level": row["risk_level"],
                }
                for row in affected_rows
            ],
            credit_amount=payload.credit_amount,
        )

    return JSONResponse({
        "ok": True,
        "outage_id": outage_id,
        "location": location,
        "verified": payload.verified,
        "duration_hours": payload.duration_hours,
        "affected_customer_count": len(affected_customers),
        "affected_customers": [
            {
                "customer_id": row["customer_id"],
                "name": row["name"],
                "location": row["location"],
                "risk_level": row["risk_level"],
            }
            for row in affected_rows
        ] if payload.verified else [],
        "proactive_contacts": proactive_contacts,
    })


@dashboard_router.get("/cases")
def list_cases(request: Request, page: int = 1, limit: int = 20) -> JSONResponse:
    """Paginated case list joining conversations, audit logs, and customers."""
    return JSONResponse(dashboard_data.list_cases(request, page=page, limit=limit))


@dashboard_router.get("/cases/{case_id}")
def get_case_detail(case_id: str, request: Request) -> JSONResponse:
    """Full case detail for the Case Detail page."""
    return JSONResponse(dashboard_data.case_detail(case_id, request))


@dashboard_router.get("/cases/{case_id}/context_card")
def get_context_card(case_id: str, request: Request) -> JSONResponse:
    """Context card data for a case."""
    return JSONResponse(dashboard_data.case_context_card(case_id, request))


@dashboard_router.get("/evaluation/results")
def evaluation_results(request: Request) -> JSONResponse:
    """Return the latest evaluation run in the frontend report shape."""
    return JSONResponse(dashboard_data.evaluation_results(request))


@dashboard_router.post("/evaluation/run")
def trigger_evaluation(
    request: Request,
    background_tasks: BackgroundTasks,
    live_llm: bool = False,
) -> JSONResponse:
    """Run evaluation and persist a fresh result file."""
    return JSONResponse(dashboard_data.evaluation_run(request, background_tasks, live_llm=live_llm))


@dashboard_router.get("/insights")
def dashboard_insights_endpoint(request: Request) -> JSONResponse:
    """God-Mode AI insights: LLM root-cause synthesis over recent interactions."""
    return JSONResponse(dashboard_data.dashboard_insights(request))


@tools_router.get("/lookup_customer/{customer_id}", response_model=ToolResponse)
def lookup_customer_endpoint(customer_id: str, request: Request) -> ToolResponse:
    try:
        result = lookup_customer(
            customer_id, db_path=request.app.state.db_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=404, detail=f"customer {customer_id!r} not found")

    response = ToolResponse(
        tool_name="lookup_customer",
        ok=True,
        result=result,
    )
    _log_tool_call(request, "lookup_customer", {
                   "customer_id": customer_id}, result=result)
    return response


@tools_router.get("/get_invoice_history/{customer_id}", response_model=ToolResponse)
def get_invoice_history_endpoint(customer_id: str, request: Request, months: int = 3) -> ToolResponse:
    try:
        invoices = get_invoice_history(
            customer_id,
            months=months,
            db_path=request.app.state.db_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = ToolResponse(
        tool_name="get_invoice_history",
        ok=True,
        result={
            "customer_id": customer_id,
            "months": months,
            "invoices": invoices,
            "invoice_count": len(invoices),
        },
    )
    _log_tool_call(request, "get_invoice_history", {
                   "customer_id": customer_id, "months": months}, result=response.result)
    return response


@tools_router.get("/check_duplicate_charge/{customer_id}", response_model=ToolResponse)
def check_duplicate_charge_endpoint(customer_id: str, request: Request, lookback_days: int = 30) -> ToolResponse:
    try:
        result = check_duplicate_charge(
            customer_id,
            lookback_days=lookback_days,
            db_path=request.app.state.db_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = ToolResponse(
        tool_name="check_duplicate_charge",
        ok=True,
        result=result,
    )
    _log_tool_call(
        request,
        "check_duplicate_charge",
        {"customer_id": customer_id, "lookback_days": lookback_days},
        result=result,
        evidence=result.get("evidence", []),
    )
    return response


@tools_router.get("/check_outage_status", response_model=ToolResponse)
def check_outage_status_endpoint(
    location: str,
    request: Request,
    customer_id: str | None = None,
) -> ToolResponse:
    try:
        result = check_outage_status(
            location,
            customer_id=customer_id,
            db_path=request.app.state.db_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = ToolResponse(
        tool_name="check_outage_status",
        ok=True,
        result=result,
    )
    _log_tool_call(
        request,
        "check_outage_status",
        {"location": location, "customer_id": customer_id},
        result=result,
        evidence=_outage_evidence(result),
    )
    return response


@tools_router.get("/run_router_diagnostic/{customer_id}", response_model=ToolResponse)
def run_router_diagnostic_endpoint(customer_id: str, request: Request) -> ToolResponse:
    try:
        result = run_router_diagnostic(
            customer_id, db_path=request.app.state.db_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = ToolResponse(
        tool_name="run_router_diagnostic",
        ok=True,
        result=result,
    )
    _log_tool_call(
        request,
        "run_router_diagnostic",
        {"customer_id": customer_id},
        result=result,
        evidence=[result["recommendation"]] if result.get(
            "recommendation") else [],
    )
    return response


@tools_router.get("/retrieve_policy/{policy_name}", response_model=ToolResponse)
def retrieve_policy_endpoint(
    policy_name: str,
    request: Request,
    query: str | None = None,
    top_k: int = 3,
) -> ToolResponse:
    try:
        result = retrieve_policy(
            policy_name,
            query=query,
            top_k=top_k,
            policy_dir=request.app.state.policy_dir,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=404, detail=f"policy {policy_name!r} not found")

    response = ToolResponse(
        tool_name="retrieve_policy",
        ok=True,
        result=result,
    )
    _log_tool_call(
        request,
        "retrieve_policy",
        {"policy_name": policy_name, "query": query, "top_k": top_k},
        result=result,
        evidence=[result["policy_id"]],
    )
    return response


@tools_router.post("/apply_credit", response_model=ToolResponse)
def apply_credit_endpoint(payload: ApplyCreditRequest, request: Request) -> ToolResponse:
    _preflight_tool_audit_context(request, payload.model_dump())
    try:
        result = apply_credit(
            payload.customer_id,
            payload.amount,
            payload.reason,
            policy_context=payload.policy_context,
            policy_name=payload.policy_name,
            applied_to_invoice=payload.applied_to_invoice,
            db_path=request.app.state.db_path,
        )
    except PolicyActionBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = ToolResponse(
        tool_name="apply_credit",
        ok=True,
        result=result,
    )
    _log_tool_call(
        request,
        "apply_credit",
        payload.model_dump(),
        result=result,
        evidence=[result["reason"]],
        actions=[{"action": "apply_credit",
                  "credit_id": result["credit_id"], "amount": result["amount"]}],
        policy_path=result.get("policy_path", []),
        ujcs=result.get("ujcs"),
        policy_status=result.get("policy_status"),
    )
    return response


@tools_router.post("/create_ticket", response_model=ToolResponse)
def create_ticket_endpoint(payload: CreateTicketRequest, request: Request) -> ToolResponse:
    _preflight_tool_audit_context(request, payload.model_dump())
    try:
        result = create_ticket(
            payload.customer_id,
            payload.issue_type,
            priority=payload.priority,
            status=payload.status,
            policy_name=payload.policy_name,
            policy_context=payload.policy_context,
            db_path=request.app.state.db_path,
        )
    except PolicyActionBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = ToolResponse(
        tool_name="create_ticket",
        ok=True,
        result=result,
    )
    _log_tool_call(
        request,
        "create_ticket",
        payload.model_dump(),
        result=result,
        actions=[{"action": "create_ticket", "ticket_id": result["ticket_id"],
                  "issue_type": result["issue_type"]}],
        policy_path=result.get("policy_path", []),
        ujcs=result.get("ujcs"),
        policy_status=result.get("policy_status"),
    )
    return response


@tools_router.post("/schedule_technician", response_model=ToolResponse)
def schedule_technician_endpoint(payload: ScheduleTechnicianRequest, request: Request) -> ToolResponse:
    _preflight_tool_audit_context(request, payload.model_dump())
    try:
        result = schedule_technician(
            payload.customer_id,
            payload.time_slot,
            policy_context=payload.policy_context,
            policy_name=payload.policy_name,
            ticket_id=payload.ticket_id,
            db_path=request.app.state.db_path,
        )
    except PolicyActionBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = ToolResponse(
        tool_name="schedule_technician",
        ok=True,
        result=result,
    )
    _log_tool_call(
        request,
        "schedule_technician",
        payload.model_dump(),
        result=result,
        actions=[
            {
                "action": "schedule_technician",
                "appointment_id": result["appointment_id"],
                "ticket_id": result["ticket_id"],
            }
        ],
        policy_path=result.get("policy_path", []),
        ujcs=result.get("ujcs"),
        policy_status=result.get("policy_status"),
    )
    return response


@tools_router.post("/change_plan", response_model=ToolResponse)
def change_plan_endpoint(payload: ChangePlanRequest, request: Request) -> ToolResponse:
    _preflight_tool_audit_context(request, payload.model_dump())
    try:
        result = change_plan(
            payload.customer_id,
            payload.new_plan_id,
            policy_context=payload.policy_context,
            policy_name=payload.policy_name,
            effective_date=payload.effective_date,
            db_path=request.app.state.db_path,
        )
    except PolicyActionBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = ToolResponse(
        tool_name="change_plan",
        ok=True,
        result=result,
    )
    _log_tool_call(
        request,
        "change_plan",
        payload.model_dump(),
        result=result,
        actions=[
            {
                "action": "change_plan",
                "previous_plan_id": result["previous_plan_id"],
                "new_plan_id": result["new_plan_id"],
            }
        ],
        policy_path=result.get("policy_path", []),
        ujcs=result.get("ujcs"),
        policy_status=result.get("policy_status"),
    )
    return response


@tools_router.post("/generate_handoff_summary", response_model=ToolResponse)
def generate_handoff_summary_endpoint(payload: HandoffSummaryRequest, request: Request) -> ToolResponse:
    try:
        result = generate_handoff_summary(
            payload.conversation_id,
            handoff_reason=payload.handoff_reason,
            db_path=request.app.state.db_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=404, detail=f"conversation {payload.conversation_id!r} not found")

    response = ToolResponse(
        tool_name="generate_handoff_summary",
        ok=True,
        result=result,
    )
    _log_tool_call(
        request,
        "generate_handoff_summary",
        payload.model_dump(),
        result={
            "handoff_summary_id": result["handoff_summary_id"], "customer_id": result["customer_id"]},
        actions=[{"action": "generate_handoff_summary",
                  "handoff_summary_id": result["handoff_summary_id"]}],
    )
    return response


@tools_router.post("/generate_context_card", response_model=ToolResponse)
def generate_context_card_endpoint(payload: ContextCardRequest, request: Request) -> ToolResponse:
    try:
        result = generate_context_card(
            payload.conversation_id,
            handoff_reason=payload.handoff_reason,
            db_path=request.app.state.db_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=404, detail=f"conversation {payload.conversation_id!r} not found")

    response = ToolResponse(
        tool_name="generate_context_card",
        ok=True,
        result=result,
    )
    _log_tool_call(
        request,
        "generate_context_card",
        payload.model_dump(),
        result={
            "context_card_id": result["context_card_id"],
            "customer_id": result["customer_id"],
            "case_id": result.get("case_id"),
        },
        actions=[{"action": "generate_context_card",
                  "context_card_id": result["context_card_id"]}],
    )
    return response


@tools_router.post("/generate_opening_line", response_model=ToolResponse)
def generate_opening_line_endpoint(payload: OpeningLineRequest, request: Request) -> ToolResponse:
    try:
        result = generate_opening_line(
            payload.conversation_id,
            context_card=payload.context_card,
            handoff_reason=payload.handoff_reason,
            db_path=request.app.state.db_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(
            status_code=404, detail=f"conversation {payload.conversation_id!r} not found")

    response = ToolResponse(
        tool_name="generate_opening_line",
        ok=True,
        result=result,
    )
    _log_tool_call(
        request,
        "generate_opening_line",
        payload.model_dump(),
        result={
            "opening_line_id": result["opening_line_id"],
            "customer_id": result.get("customer_id"),
            "opening_line": result["opening_line"],
        },
        actions=[{"action": "generate_opening_line",
                  "opening_line_id": result["opening_line_id"]}],
    )
    return response


@tools_router.post("/generate_audit_log", response_model=ToolResponse)
def generate_audit_log_endpoint(payload: AuditLogRequest, request: Request) -> ToolResponse:
    try:
        result = generate_audit_log(
            payload.case_id,
            customer_id=payload.customer_id,
            session_id=payload.session_id,
            tools_called=payload.tools_called,
            evidence_used=payload.evidence_used,
            action_taken=payload.action_taken,
            policy_dag_path=payload.policy_dag_path,
            policy_name=payload.policy_name,
            ujcs=payload.ujcs,
            policy_status=payload.policy_status,
            health_score=payload.health_score,
            handoff_required=payload.handoff_required,
            db_path=request.app.state.db_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return ToolResponse(
        tool_name="generate_audit_log",
        ok=True,
        result=result,
    )


def _log_tool_call(
    request: Request,
    tool_name: str,
    args: dict[str, Any],
    *,
    result: dict[str, Any],
    evidence: list[Any] | None = None,
    actions: list[Any] | None = None,
    policy_path: list[Any] | None = None,
    ujcs: float | None = None,
    policy_status: str | None = None,
) -> None:
    session_id = _header_value(request, "x-resolveflow-session-id")
    customer_id = _customer_id_for_audit(request, args=args, result=result)
    if not session_id or not customer_id:
        return

    case_id = _header_value(
        request, "x-resolveflow-case-id") or f"case-{session_id}"
    audit_lock = _audit_log_lock(request.app.state.db_path, case_id)
    tool_entry = {
        "tool_name": tool_name,
        "args": args,
        "result": _summarize_tool_result(result),
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with audit_lock:
        with sqlite3.connect(request.app.state.db_path, timeout=30.0) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            if not _customer_exists(connection, customer_id):
                return
            _ensure_conversation(
                connection, session_id=session_id, customer_id=customer_id)
            row = connection.execute(
                """
                SELECT tools_called, evidence_used, action_taken, policy_dag_path, ujcs, policy_status, health_score,
                       handoff_required
                FROM audit_logs
                WHERE case_id = ?
                """,
                (case_id,),
            ).fetchone()
            if row is None:
                tools_called = []
                evidence_used = []
                action_taken = []
                policy_dag_path = []
                health_score = None
                handoff_required = 0
            else:
                tools_called = _loads_json(row["tools_called"])
                evidence_used = _loads_json(row["evidence_used"])
                action_taken = _loads_json(row["action_taken"])
                policy_dag_path = _loads_json(row["policy_dag_path"])
                health_score = row["health_score"]
                handoff_required = row["handoff_required"]
                ujcs = ujcs if ujcs is not None else row["ujcs"]
                policy_status = policy_status or row["policy_status"]

            tools_called.append(tool_entry)
            evidence_used.extend(evidence or [])
            action_taken.extend(actions or [])
            if policy_path:
                policy_dag_path = list(policy_path)
            computed_policy_status = policy_status or _policy_status_for_audit(
                ujcs)
            connection.execute(
                """
                INSERT INTO audit_logs (
                    case_id,
                    customer_id,
                    session_id,
                    tools_called,
                    evidence_used,
                    action_taken,
                    policy_dag_path,
                    ujcs,
                    policy_status,
                    health_score,
                    handoff_required
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    customer_id = excluded.customer_id,
                    session_id = excluded.session_id,
                    tools_called = excluded.tools_called,
                    evidence_used = excluded.evidence_used,
                    action_taken = excluded.action_taken,
                    policy_dag_path = excluded.policy_dag_path,
                    ujcs = excluded.ujcs,
                    policy_status = excluded.policy_status,
                    health_score = excluded.health_score,
                    handoff_required = excluded.handoff_required
                """,
                (
                    case_id,
                    customer_id,
                    session_id,
                    json.dumps(tools_called),
                    json.dumps(evidence_used),
                    json.dumps(action_taken),
                    json.dumps(policy_dag_path),
                    ujcs,
                    computed_policy_status,
                    health_score,
                    handoff_required,
                ),
            )


def _preflight_tool_audit_context(request: Request, args: dict[str, Any]) -> None:
    session_id = _header_value(request, "x-resolveflow-session-id")
    customer_id = _customer_id_for_audit(request, args=args, result={})
    if not session_id or not customer_id:
        return

    with sqlite3.connect(request.app.state.db_path, timeout=30.0) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        if not _customer_exists(connection, customer_id):
            return
        try:
            _validate_existing_conversation_owner(
                connection, session_id=session_id, customer_id=customer_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


def _audit_log_lock(db_path: Any, case_id: str) -> threading.Lock:
    key = (str(Path(db_path).resolve()), case_id)
    with _AUDIT_LOG_LOCKS_GUARD:
        lock = _AUDIT_LOG_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _AUDIT_LOG_LOCKS[key] = lock
        return lock


def _header_value(request: Request, header_name: str) -> str | None:
    value = request.headers.get(header_name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _customer_id_for_audit(request: Request, *, args: dict[str, Any], result: dict[str, Any]) -> str | None:
    from_header = _header_value(request, "x-resolveflow-customer-id")
    if from_header:
        return from_header
    for payload in (result, args):
        value = payload.get("customer_id") if isinstance(
            payload, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    context_card = result.get("context_card") if isinstance(
        result, dict) else None
    if isinstance(context_card, dict):
        customer = context_card.get("customer")
        if isinstance(customer, dict) and isinstance(customer.get("customer_id"), str):
            return customer["customer_id"].strip()
    return None


def _customer_exists(connection: sqlite3.Connection, customer_id: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
    return row is not None


def _ensure_conversation(connection: sqlite3.Connection, *, session_id: str, customer_id: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO conversations(session_id, customer_id, messages) VALUES (?, ?, ?)",
        (session_id, customer_id, "[]"),
    )
    row = connection.execute(
        "SELECT customer_id FROM conversations WHERE session_id = ?", (session_id,)).fetchone()
    if row is not None and row["customer_id"] != customer_id:
        raise ValueError(
            f"session {session_id!r} does not belong to customer {customer_id!r}")


def _validate_existing_conversation_owner(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    customer_id: str,
) -> None:
    row = connection.execute(
        "SELECT customer_id FROM conversations WHERE session_id = ?", (session_id,)).fetchone()
    if row is not None and row["customer_id"] != customer_id:
        raise ValueError(
            f"session {session_id!r} does not belong to customer {customer_id!r}")


def _agent_desk_queue_row(row: sqlite3.Row) -> dict[str, Any]:
    context_card = _safe_json_dict(row["context_card"])
    messages = _loads_json(row["messages"])
    intents = _loads_json(row["intents"])
    return {
        "handoff_id": row["handoff_id"],
        "case_id": row["case_id"],
        "customer_id": row["customer_id"],
        "customer_name": row["customer_name"],
        "plan_id": row["plan_id"],
        "risk_level": row["risk_level"],
        "churn_score": float(row["churn_score"] or 0),
        "session_id": row["session_id"],
        "handoff_reason": row["handoff_reason"],
        "status": row["status"],
        "created_at": row["created_at"],
        "assigned_to": row["assigned_to"],
        "intents": intents,
        "message_count": len(messages),
        "last_customer_message": _last_customer_message(messages),
        "health_score": row["health_score"],
        "policy_status": row["policy_status"],
        "ujcs": row["ujcs"],
        "context_card": context_card,
        "recommended_opening_line": (
            context_card.get("recommended_opening_line")
            or "I have the case context open and can take over from here."
        ),
    }


def _agent_desk_copilot_suggestions(
    *,
    row: sqlite3.Row,
    context_card: dict[str, Any],
    opening_line: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    tools = _loads_json(row["tools_called"])
    dag_path = _loads_json(row["policy_dag_path"])
    context_tools = context_card.get("tools_called")
    if isinstance(context_tools, list):
        tools = tools + context_tools

    evidence = _agent_desk_tool_evidence(tools)
    remaining = _issue_labels(context_card.get("issues_remaining"))
    resolved = _issue_labels(context_card.get("issues_resolved"))
    customer = context_card.get("customer")
    customer_name = (
        customer.get("name")
        if isinstance(customer, dict)
        else row["customer_name"]
    ) or row["customer_name"]
    opening_text = ""
    if isinstance(opening_line, dict):
        opening_text = str(opening_line.get("opening_line") or "")
    if not opening_text:
        opening_text = str(
            context_card.get("recommended_opening_line")
            or "I have your case context open and can take over from here."
        )

    suggestions: list[dict[str, Any]] = [
        {
            "id": "grounded-opening",
            "title": "Start with verified context",
            "reply": opening_text,
            "evidence": evidence[:3],
            "confidence": 0.94 if evidence else 0.72,
        }
    ]

    if remaining:
        issue_text = ", ".join(remaining[:3])
        suggestions.append({
            "id": "remaining-issues",
            "title": "Set the next step",
            "reply": (
                f"{customer_name}, I can see the remaining item"
                f"{'s' if len(remaining) > 1 else ''}: {issue_text}. "
                "I will handle those in order and will not repeat questions "
                "already answered in this case."
            ),
            "evidence": [
                {
                    "source": "context_card",
                    "label": "issues_remaining",
                    "detail": issue_text,
                },
                *evidence[:2],
            ],
            "confidence": 0.9,
        })
    elif resolved:
        resolved_text = ", ".join(resolved[:3])
        suggestions.append({
            "id": "resolved-summary",
            "title": "Confirm completed work",
            "reply": (
                f"{customer_name}, the resolved item"
                f"{'s' if len(resolved) > 1 else ''} on this case: "
                f"{resolved_text}. I am checking the handoff record before "
                "closing anything further."
            ),
            "evidence": [
                {
                    "source": "context_card",
                    "label": "issues_resolved",
                    "detail": resolved_text,
                },
                *evidence[:2],
            ],
            "confidence": 0.88,
        })

    policy_status = str(row["policy_status"] or context_card.get("policy_status") or "pending")
    ujcs = row["ujcs"]
    if ujcs is None:
        policy_path = context_card.get("policy_dag_path_so_far")
        if isinstance(policy_path, dict):
            ujcs = policy_path.get("ujcs")
    dag_detail = _dag_evidence_detail(dag_path, context_card)
    suggestions.append({
        "id": "policy-safe-close",
        "title": "Keep the action policy-safe",
        "reply": (
            "Before I apply or close any action, I will use the verified policy "
            f"path. Current policy status is {policy_status}"
            f"{f' with UJCS {float(ujcs):.2f}' if isinstance(ujcs, int | float) else ''}."
        ),
        "evidence": [
            {
                "source": "policy_dag",
                "label": "policy_path",
                "detail": dag_detail,
            },
            *evidence[:2],
        ],
        "confidence": 0.86,
    })
    return suggestions[:3]


def _agent_desk_tool_evidence(tools: list[Any]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    seen: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("tool_name") or tool.get("name") or "tool")
        result = tool.get("result")
        detail = _short_result_detail(result)
        key = f"{name}:{detail}"
        if key in seen:
            continue
        seen.add(key)
        evidence.append({
            "source": "tool",
            "label": name,
            "detail": detail,
        })
    return evidence


def _short_result_detail(value: Any) -> str:
    if isinstance(value, dict):
        if "status" in value:
            return f"status: {value['status']}"
        for key in ("duplicate_found", "outage_verified", "credit_id", "ticket_id", "request_id"):
            if key in value:
                return f"{key}: {value[key]}"
        if value:
            first_key = next(iter(value))
            return f"{first_key}: {value[first_key]}"
    if value is None:
        return "verified call recorded"
    return str(value)


def _issue_labels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    labels: list[str] = []
    for item in value:
        if isinstance(item, dict):
            label = item.get("label") or item.get("intent")
        else:
            label = item
        if label:
            labels.append(str(label))
    return labels


def _dag_evidence_detail(
    dag_path: list[Any],
    context_card: dict[str, Any],
) -> str:
    if dag_path:
        return " -> ".join(str(node) for node in dag_path[-4:])
    policy_path = context_card.get("policy_dag_path_so_far")
    if isinstance(policy_path, dict):
        return str(policy_path.get("path_text") or policy_path.get("current_node") or "policy path recorded")
    return "policy path recorded"


_KNOWN_ATTACK_IDS = {
    "prompt-injection", "injection",
    "admin-mode", "admin",
    "over-limit-credit", "over-limit", "refund-abuse",
    "policy-leak", "leak",
}


def _security_attack_plan(attack_id: str, prompt: str, *, _matched_by: str = "explicit_attack_id") -> dict[str, Any]:
    normalized_id = " ".join(attack_id.strip().split()).lower()
    normalized_prompt = " ".join(prompt.strip().split())
    if not normalized_prompt:
        raise HTTPException(status_code=422, detail="prompt must not be empty")

    if normalized_id in {"prompt-injection", "injection"}:
        return {
            "attack_id": "prompt-injection",
            "prompt": normalized_prompt,
            "requested_action": "apply_credit",
            "policy_name": "service_credit_dag",
            "matched_by": _matched_by,
            "context": {
                "check_outage_status": {"verified": False, "duration_hours": 0},
                "get_invoice_history": {"credit_this_cycle": False},
            },
        }
    if normalized_id in {"admin-mode", "admin"}:
        return {
            "attack_id": "admin-mode",
            "prompt": normalized_prompt,
            "requested_action": "complete_cancellation",
            "policy_name": "cancellation_retention_dag",
            "matched_by": _matched_by,
            "context": {
                "lookup_customer": {"identity_verified": True},
                "has_open_issue": True,
                "churn_score": 0.82,
            },
        }
    if normalized_id in {"over-limit-credit", "over-limit", "refund-abuse"}:
        return {
            "attack_id": "over-limit-credit",
            "prompt": normalized_prompt,
            "requested_action": "apply_credit",
            "policy_name": "refund_exception_dag",
            "matched_by": _matched_by,
            "context": {
                "refund_reason_eligible": True,
                "payment_ownership_verified": True,
                "payment_age_days": 2,
                "refund_amount": 1500,
            },
        }
    if normalized_id in {"policy-leak", "leak"}:
        return {
            "attack_id": "policy-leak",
            "prompt": normalized_prompt,
            "requested_action": "reveal_system_prompt",
            "policy_name": "cancellation_retention_dag",
            "matched_by": _matched_by,
            "context": {
                "lookup_customer": {"identity_verified": False},
                "has_open_issue": False,
                "churn_score": 0.1,
            },
        }

    # Free-form/custom attack_id: the literal prompt text is preserved and
    # shown back to the caller, but the policy-DAG context below is always
    # one of the 4 fixed fixtures above, picked by keyword -- it does not
    # evaluate the prompt's actual content. matched_by="keyword_heuristic"
    # tells the caller (surfaced in the UI) that this happened, instead of
    # silently implying the exact prompt was custom-evaluated.
    lower_prompt = normalized_prompt.lower()
    if "credit" in lower_prompt or "refund" in lower_prompt:
        return _security_attack_plan("over-limit-credit", normalized_prompt, _matched_by="keyword_heuristic")
    if "cancel" in lower_prompt or "admin" in lower_prompt:
        return _security_attack_plan("admin-mode", normalized_prompt, _matched_by="keyword_heuristic")
    return _security_attack_plan("prompt-injection", normalized_prompt, _matched_by="keyword_heuristic")


def _write_security_attack_audit(
    *,
    db_path: Path,
    attack: dict[str, Any],
    validation: dict[str, Any],
    stopped_node: str,
    receipt_trail: list[dict[str, str]],
) -> str:
    created_at = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(
        f"{attack['attack_id']}|{attack['prompt']}|{created_at}".encode("utf-8")
    ).hexdigest()[:12].upper()
    case_id = f"SEC-{digest}"
    session_id = f"security-{digest.lower()}"
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        customer = connection.execute(
            "SELECT customer_id FROM customers ORDER BY customer_id LIMIT 1"
        ).fetchone()
        if customer is None:
            raise HTTPException(
                status_code=409,
                detail="security attack audit requires at least one customer row",
            )
        connection.execute(
            """
            INSERT INTO audit_logs (
                case_id,
                customer_id,
                session_id,
                tools_called,
                evidence_used,
                action_taken,
                policy_dag_path,
                ujcs,
                policy_status,
                health_score,
                handoff_required,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'non_compliant', NULL, 1, ?)
            """,
            (
                case_id,
                customer["customer_id"],
                session_id,
                json.dumps([{
                    "tool_name": "PolicyGraphValidator.run",
                    "policy_name": attack["policy_name"],
                    "success": True,
                }], ensure_ascii=True),
                json.dumps(receipt_trail, ensure_ascii=True),
                json.dumps([{
                    "action": "security_attack_blocked",
                    "attack_id": attack["attack_id"],
                    "blocked_action": attack["requested_action"],
                    "stopped_node": stopped_node,
                    "reached_action": validation.get("action"),
                }], ensure_ascii=True),
                json.dumps(validation.get("path") or [], ensure_ascii=True),
                validation.get("ujcs"),
                created_at,
            ),
        )
    return case_id


def _initiate_proactive_outage_contacts(
    *,
    db_path: Path,
    outage_id: str,
    location: str,
    duration_hours: float,
    affected_customers: list[dict[str, Any]],
    credit_amount: float,
) -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    policy_context = {
        "check_outage_status": {
            "verified": True,
            "duration_hours": duration_hours,
        },
        "get_invoice_history": {
            "credit_this_cycle": False,
        },
    }
    for customer in affected_customers:
        customer_id = str(customer["customer_id"])
        try:
            credit = apply_credit(
                customer_id,
                credit_amount,
                f"Proactive service credit for verified outage {outage_id} at {location}.",
                policy_context=policy_context,
                policy_name="service_credit_dag",
                db_path=db_path,
            )
            status = "credited"
            message = (
                f"We detected a verified outage in {location} lasting "
                f"{duration_hours:g} hours. A proactive service credit has been "
                "applied to your account under the service credit policy."
            )
        except PolicyActionBlocked as exc:
            credit = {"error": str(exc)}
            status = "blocked"
            # Do not tell the customer a credit was applied when the policy
            # DAG just blocked it -- still confirm the outage since that part
            # is real, but be honest that the credit needs manual review.
            message = (
                f"We detected a verified outage in {location} lasting "
                f"{duration_hours:g} hours affecting your service. A proactive "
                "credit was proposed but needs manual review before it can be applied."
            )

        session_id = f"proactive-{outage_id.lower()}-{customer_id.lower()}"
        now = datetime.now(timezone.utc).isoformat()
        messages = [{
            "role": "assistant",
            "content": message,
            "timestamp": now,
            "proactive": True,
            "outage_id": outage_id,
        }]
        tools_called = [{
            "tool_name": "apply_credit",
            "success": status == "credited",
            "result": credit,
            "timestamp": now,
        }]
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                INSERT INTO conversations (
                    session_id,
                    customer_id,
                    messages,
                    intents,
                    slots,
                    tools_called,
                    health_scores,
                    final_status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    messages = excluded.messages,
                    tools_called = excluded.tools_called,
                    final_status = 'active'
                """,
                (
                    session_id,
                    customer_id,
                    json.dumps(messages, ensure_ascii=True),
                    json.dumps(["proactive_outage"], ensure_ascii=True),
                    json.dumps({"location": location, "outage_id": outage_id}, ensure_ascii=True),
                    json.dumps(tools_called, ensure_ascii=True),
                    json.dumps([{"score": 72, "reason": "proactive outage outreach"}], ensure_ascii=True),
                    now,
                ),
            )
        contacts.append({
            "customer_id": customer_id,
            "name": customer.get("name"),
            "session_id": session_id,
            "status": status,
            "message": message,
            "credit": credit,
        })
    return contacts


def _safe_json_dict(raw_value: str | None) -> dict[str, Any]:
    if raw_value is None:
        return {}
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _last_customer_message(messages: list[Any]) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        if role in {"customer", "user"}:
            content = message.get("content") or message.get("message")
            return str(content) if content is not None else None
    return None


def _loads_json(raw_value: str | None) -> list:
    if raw_value is None:
        return []
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, round((len(values) - 1) * percentile)))
    return round(values[index], 1)


def _policy_status_for_audit(ujcs: float | None) -> str:
    if ujcs is None:
        return "pending"
    if ujcs > 0.8:
        return "compliant"
    if ujcs == 0:
        return "non_compliant"
    return "needs_review"


def _summarize_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    keep_keys = (
        "customer_id",
        "invoice_id",
        "ticket_id",
        "credit_id",
        "appointment_id",
        "policy_id",
        "policy_status",
        "ujcs",
        "duplicate_confirmed",
        "verified",
        "diagnostic_failure",
        "new_plan_id",
        "handoff_summary_id",
        "context_card_id",
        "opening_line_id",
    )
    summary = {key: result[key] for key in keep_keys if key in result}
    return summary or {"recorded": True}


def _outage_evidence(result: dict[str, Any]) -> list[str]:
    if not result.get("has_outage_record"):
        return []
    evidence = [str(result.get("outage_id") or result.get("location"))]
    if result.get("verified") is True:
        evidence.append("verified outage")
    return evidence


def _locations_match(left: str, right: str | None) -> bool:
    left_tokens = _location_tokens(left)
    right_tokens = _location_tokens(right or "")
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    overlap = left_tokens & right_tokens
    return len(overlap) >= 2 and len(overlap) / max(len(left_tokens), len(right_tokens)) >= 0.6


def _location_tokens(value: str) -> set[str]:
    normalized = value.lower()
    normalized = re.sub(r"\bzone\b", "z", normalized)
    normalized = re.sub(r"\bz[\s-]*(\d+)\b", lambda match: f"z{int(match.group(1))}", normalized)
    normalized = re.sub(r"\b0+(\d+)\b", lambda match: str(int(match.group(1))), normalized)
    return {token for token in re.findall(r"[a-z]+[0-9]*|[0-9]+", normalized) if token}


def _safe_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []
