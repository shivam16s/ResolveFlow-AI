from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.agent.policy_graph import PolicyActionBlocked
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
def trigger_evaluation(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """Run evaluation and persist a fresh result file."""
    _ = background_tasks
    return JSONResponse(dashboard_data.evaluation_run(request))


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
    tool_entry = {
        "tool_name": tool_name,
        "args": args,
        "result": _summarize_tool_result(result),
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with sqlite3.connect(request.app.state.db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
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
    row = connection.execute(
        "SELECT customer_id FROM conversations WHERE session_id = ?", (session_id,)).fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO conversations(session_id, customer_id, messages) VALUES (?, ?, ?)",
            (session_id, customer_id, "[]"),
        )
        return
    if row["customer_id"] != customer_id:
        raise ValueError(
            f"session {session_id!r} does not belong to customer {customer_id!r}")


def _loads_json(raw_value: str | None) -> list:
    if raw_value is None:
        return []
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


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


def _safe_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []
