from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
from uuid import uuid4

from .agent.policy_graph import PolicyActionBlocked, PolicyGraphValidator, compute_ujcs, default_policy_dags
from .agent.policy_retrieval import (
    decide_policy_retrieval,
    decompose_policy_to_strips,
    evaluate_policy_relevance,
)
from .agent.policy_store import DEFAULT_POLICY_DIR, PolicyDocument, load_policy_documents
from .db.init_db import DEFAULT_DB_PATH


@dataclass(frozen=True)
class CustomerProfile:
    customer_id: str
    name: str
    email: str
    location: str
    plan_id: str
    plan_name: str
    monthly_price: float
    speed_mbps: int
    risk_level: str
    preferred_language: str
    account_status: str
    churn_score: float
    identity_verified: bool
    account_active: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class InvoiceRecord:
    invoice_id: str
    customer_id: str
    amount: float
    date: str
    status: str
    payment_id: str | None
    payment_amount: float | None
    payment_date: str | None
    payment_method: str | None
    duplicate_flag: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DuplicateChargeResult:
    customer_id: str
    duplicate_confirmed: bool
    has_duplicate: bool
    duplicate_payment_ids: list[str]
    duplicate_amount: float | None
    payment_method: str | None
    payment_timestamps: list[str]
    invoice_id: str | None
    single_matching_invoice: bool
    lookback_days: int
    evidence: list[str]
    duplicate_groups: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OutageStatus:
    location: str
    customer_id: str | None
    has_outage_record: bool
    verified: bool
    outage_id: str | None
    start_time: str | None
    end_time: str | None
    duration_hours: float | None
    affected_customers: list[str]
    customer_affected: bool | None
    outage_cleared: bool
    affected_area: str | None
    checked_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RouterDiagnosticResult:
    customer_id: str
    customer_found: bool
    diagnostic_available: bool
    router_status: str | None
    signal_strength: int | None
    last_checked: str | None
    recommendation: str | None
    diagnostic_failure: bool
    needs_technician: bool
    account_active: bool | None
    checked_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PolicyRetrievalResult:
    policy_name: str
    query: str
    policy_id: str
    title: str
    version: int
    effective_date: str
    owner: str
    source_path: str
    text: str
    retrieve_decision: dict
    relevance: dict
    evidence_strips: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CreditApplicationResult:
    credit_id: str
    customer_id: str
    amount: float
    reason: str
    applied_to_invoice: str | None
    applied_at: str
    policy_name: str
    policy_action: str
    policy_action_args: dict
    policy_path: list[str]
    ujcs: float
    policy_status: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TicketCreationResult:
    ticket_id: str
    customer_id: str
    issue_type: str
    status: str
    priority: str
    created_at: str
    policy_name: str | None
    policy_action: str | None
    policy_action_args: dict
    policy_path: list[str]
    ujcs: float | None
    policy_status: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TechnicianScheduleResult:
    appointment_id: str
    customer_id: str
    time_slot: str
    slot_confirmed: bool
    technician_name: str
    ticket_id: str
    ticket_created: bool
    ticket_status: str
    scheduled_at: str
    policy_name: str
    policy_action: str
    policy_action_args: dict
    policy_path: list[str]
    ujcs: float
    policy_status: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PlanChangeResult:
    customer_id: str
    previous_plan_id: str
    previous_plan_name: str
    new_plan_id: str
    new_plan_name: str
    monthly_price_before: float
    monthly_price_after: float
    speed_mbps_before: int
    speed_mbps_after: int
    change_type: str
    effective_date: str
    fee_disclosure_required: bool
    cancellation_fee: float
    changed_at: str
    policy_name: str
    policy_action: str
    policy_action_args: dict
    policy_path: list[str]
    ujcs: float
    policy_status: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class HandoffSummaryResult:
    handoff_summary_id: str
    session_id: str
    customer_id: str
    generated_at: str
    reason_for_escalation: str
    recommended_opening: str
    customer: dict
    issues_detected: list[dict]
    issues_resolved: list[dict]
    issues_remaining: list[dict]
    emotion: str
    urgency: str
    slots_collected: dict
    tools_called: list[dict]
    policies_retrieved: list[str]
    policy_nodes_visited: list[str]
    evidence_used: list
    actions_taken: list
    memory_context: list[dict]
    context_card: dict

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ContextCardResult:
    context_card_id: str
    session_id: str
    customer_id: str
    generated_at: str
    case_id: str | None
    customer: dict
    issues_detected: list[dict]
    issues_resolved: list[dict]
    issues_remaining: list[dict]
    issues_summary: dict
    emotion: str
    urgency: str
    current_health_score: float | None
    relationship: dict
    slots_collected: dict
    tools_called: list[dict]
    policies_retrieved: list[str]
    policy_nodes_visited: list[str]
    policy_dag_path_so_far: dict
    evidence_used: list
    actions_taken: list
    audit: dict | None
    handoff_queue: dict | None
    reason_for_escalation: str
    recommended_opening: str
    memory_context: list[dict]
    last_customer_message: str | None
    source: str = "customer_context_card"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OpeningLineResult:
    opening_line_id: str
    opening_line: str
    customer_id: str | None
    customer_name: str | None
    issue_labels: list[str]
    reason_for_escalation: str
    has_remaining_issues: bool
    source: str = "human_agent_opening_line"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AuditLogResult:
    case_id: str
    customer_id: str
    session_id: str
    tools_called: list
    evidence_used: list
    action_taken: list
    policy_dag_path: list
    ujcs: float | None
    policy_status: str
    health_score: float | None
    handoff_required: bool
    created_at: str
    human_summary: str
    raw_json: dict
    inserted: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AuditLogDraft:
    case_id: str
    customer_id: str
    session_id: str
    tools_called: list
    evidence_used: list
    action_taken: list
    policy_dag_path: list
    ujcs: float | None
    policy_status: str
    health_score: float | None
    handoff_required: bool
    human_summary: str
    raw_json: dict
    source: str = "resolution_proof_trail"

    def to_dict(self) -> dict:
        return asdict(self)


def lookup_customer(customer_id: str, *, db_path: Path = DEFAULT_DB_PATH) -> dict | None:
    normalized_customer_id = customer_id.strip()
    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                c.customer_id,
                c.name,
                c.email,
                c.location,
                c.plan_id,
                p.plan_name,
                p.monthly_price,
                p.speed_mbps,
                c.risk_level,
                c.preferred_language,
                c.account_status,
                c.churn_score
            FROM customers c
            JOIN plans p ON p.plan_id = c.plan_id
            WHERE c.customer_id = ?
            """,
            (normalized_customer_id,),
        ).fetchone()

    if row is None:
        return None

    profile = CustomerProfile(
        customer_id=row["customer_id"],
        name=row["name"],
        email=row["email"],
        location=row["location"],
        plan_id=row["plan_id"],
        plan_name=row["plan_name"],
        monthly_price=float(row["monthly_price"]),
        speed_mbps=int(row["speed_mbps"]),
        risk_level=row["risk_level"],
        preferred_language=row["preferred_language"],
        account_status=row["account_status"],
        churn_score=float(row["churn_score"]),
        identity_verified=True,
        account_active=row["account_status"] == "active",
    )
    return profile.to_dict()


def get_invoice_history(
    customer_id: str,
    *,
    months: int = 3,
    db_path: Path = DEFAULT_DB_PATH,
    reference_date: date | str | None = None,
) -> list[dict]:
    normalized_customer_id = customer_id.strip()
    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")
    if months < 1:
        raise ValueError("months must be at least 1")

    anchor_date = _reference_date_string(reference_date)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                i.invoice_id,
                i.customer_id,
                i.amount,
                i.date,
                i.status,
                i.payment_id,
                p.amount AS payment_amount,
                p.date AS payment_date,
                p.method AS payment_method,
                COALESCE(p.duplicate_flag, 0) AS duplicate_flag
            FROM invoices i
            LEFT JOIN payments p ON p.payment_id = i.payment_id
            WHERE i.customer_id = ?
              AND date(i.date) >= date(?, '-' || ? || ' months')
            ORDER BY date(i.date) DESC, i.invoice_id DESC
            """,
            (normalized_customer_id, anchor_date, months),
        ).fetchall()

    return [
        InvoiceRecord(
            invoice_id=row["invoice_id"],
            customer_id=row["customer_id"],
            amount=float(row["amount"]),
            date=row["date"],
            status=row["status"],
            payment_id=row["payment_id"],
            payment_amount=float(row["payment_amount"]) if row["payment_amount"] is not None else None,
            payment_date=row["payment_date"],
            payment_method=row["payment_method"],
            duplicate_flag=bool(row["duplicate_flag"]),
        ).to_dict()
        for row in rows
    ]


def check_duplicate_charge(
    customer_id: str,
    *,
    lookback_days: int = 30,
    db_path: Path = DEFAULT_DB_PATH,
    reference_date: date | str | None = None,
) -> dict:
    normalized_customer_id = customer_id.strip()
    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")
    if lookback_days < 1:
        raise ValueError("lookback_days must be at least 1")

    anchor = _reference_datetime(reference_date)
    cutoff = anchor - timedelta(days=lookback_days)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        payment_rows = connection.execute(
            """
            SELECT payment_id, customer_id, amount, date, method, duplicate_flag
            FROM payments
            WHERE customer_id = ?
              AND datetime(date) >= datetime(?)
              AND datetime(date) <= datetime(?)
            ORDER BY datetime(date), payment_id
            """,
            (normalized_customer_id, cutoff.isoformat(), anchor.isoformat()),
        ).fetchall()
        invoice_rows = connection.execute(
            """
            SELECT invoice_id, amount, date, status, payment_id
            FROM invoices
            WHERE customer_id = ?
            """,
            (normalized_customer_id,),
        ).fetchall()

    payments = [_payment_dict(row) for row in payment_rows]
    invoices = [_invoice_match_dict(row) for row in invoice_rows]
    duplicate_groups = _detect_duplicate_payment_groups(payments, invoices)
    primary_group = duplicate_groups[0] if duplicate_groups else {}
    duplicate_payment_ids = list(primary_group.get("payment_ids", []))
    payment_timestamps = list(primary_group.get("payment_timestamps", []))
    invoice_id = primary_group.get("invoice_id")
    single_matching_invoice = bool(primary_group.get("single_matching_invoice", False))
    duplicate_confirmed = bool(duplicate_groups and single_matching_invoice)

    return DuplicateChargeResult(
        customer_id=normalized_customer_id,
        duplicate_confirmed=duplicate_confirmed,
        has_duplicate=duplicate_confirmed,
        duplicate_payment_ids=duplicate_payment_ids,
        duplicate_amount=primary_group.get("amount"),
        payment_method=primary_group.get("method"),
        payment_timestamps=payment_timestamps,
        invoice_id=invoice_id,
        single_matching_invoice=single_matching_invoice,
        lookback_days=lookback_days,
        evidence=list(primary_group.get("evidence", [])),
        duplicate_groups=duplicate_groups,
    ).to_dict()


def check_outage_status(
    location: str,
    *,
    customer_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    reference_date: date | str | None = None,
) -> dict:
    normalized_location = location.strip()
    if not normalized_location:
        raise ValueError("location must not be empty")
    normalized_customer_id = customer_id.strip() if isinstance(customer_id, str) else None
    if customer_id is not None and not normalized_customer_id:
        raise ValueError("customer_id must not be empty when provided")

    checked_at = _reference_datetime(reference_date)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT outage_id, location, start_time, end_time, duration_hours, verified, affected_customers
            FROM outages
            WHERE lower(location) = lower(?)
              AND datetime(start_time) <= datetime(?)
            ORDER BY datetime(start_time) DESC, outage_id DESC
            LIMIT 1
            """,
            (normalized_location, checked_at.isoformat()),
        ).fetchone()

    if row is None:
        return OutageStatus(
            location=normalized_location,
            customer_id=normalized_customer_id,
            has_outage_record=False,
            verified=False,
            outage_id=None,
            start_time=None,
            end_time=None,
            duration_hours=None,
            affected_customers=[],
            customer_affected=False if normalized_customer_id else None,
            outage_cleared=True,
            affected_area=None,
            checked_at=checked_at.isoformat(),
        ).to_dict()

    affected_customers = _json_list(row["affected_customers"])
    customer_affected = normalized_customer_id in affected_customers if normalized_customer_id else None
    end_time = row["end_time"]
    outage_cleared = end_time is not None and datetime.fromisoformat(end_time) <= checked_at
    return OutageStatus(
        location=row["location"],
        customer_id=normalized_customer_id,
        has_outage_record=True,
        verified=bool(row["verified"]),
        outage_id=row["outage_id"],
        start_time=row["start_time"],
        end_time=end_time,
        duration_hours=float(row["duration_hours"]) if row["duration_hours"] is not None else None,
        affected_customers=affected_customers,
        customer_affected=customer_affected,
        outage_cleared=outage_cleared,
        affected_area=row["location"],
        checked_at=checked_at.isoformat(),
    ).to_dict()


def run_router_diagnostic(
    customer_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    reference_date: date | str | None = None,
) -> dict:
    normalized_customer_id = customer_id.strip()
    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")

    checked_at = _reference_datetime(reference_date)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT
                c.customer_id,
                c.account_status,
                d.router_status,
                d.signal_strength,
                d.last_checked,
                d.recommendation
            FROM customers c
            LEFT JOIN diagnostics d ON d.customer_id = c.customer_id
            WHERE c.customer_id = ?
            """,
            (normalized_customer_id,),
        ).fetchone()

    if row is None:
        return RouterDiagnosticResult(
            customer_id=normalized_customer_id,
            customer_found=False,
            diagnostic_available=False,
            router_status=None,
            signal_strength=None,
            last_checked=None,
            recommendation=None,
            diagnostic_failure=False,
            needs_technician=False,
            account_active=None,
            checked_at=checked_at.isoformat(),
        ).to_dict()

    diagnostic_available = row["router_status"] is not None
    signal_strength = int(row["signal_strength"]) if row["signal_strength"] is not None else None
    diagnostic_failure = bool(
        diagnostic_available
        and (
            row["router_status"] in {"degraded", "offline"}
            or (signal_strength is not None and signal_strength < 50)
        )
    )
    recommendation = row["recommendation"] or _router_recommendation(
        router_status=row["router_status"],
        signal_strength=signal_strength,
        diagnostic_available=diagnostic_available,
    )
    return RouterDiagnosticResult(
        customer_id=normalized_customer_id,
        customer_found=True,
        diagnostic_available=diagnostic_available,
        router_status=row["router_status"],
        signal_strength=signal_strength,
        last_checked=row["last_checked"],
        recommendation=recommendation,
        diagnostic_failure=diagnostic_failure,
        needs_technician=diagnostic_failure,
        account_active=row["account_status"] == "active",
        checked_at=checked_at.isoformat(),
    ).to_dict()


def retrieve_policy(
    policy_name: str,
    *,
    query: str | None = None,
    policy_dir: Path = DEFAULT_POLICY_DIR,
    top_k: int = 3,
) -> dict | None:
    normalized_policy_name = policy_name.strip()
    if not normalized_policy_name:
        raise ValueError("policy_name must not be empty")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")

    document = _find_policy_document(normalized_policy_name, policy_dir=policy_dir)
    if document is None:
        return None

    normalized_query = " ".join((query or normalized_policy_name).split())
    if not normalized_query:
        raise ValueError("query must not be empty when provided")

    retrieve_decision = decide_policy_retrieval(normalized_query)
    relevance = evaluate_policy_relevance(normalized_query, document.text)
    evidence_strips = _top_policy_evidence_strips(
        query=normalized_query,
        document=document,
        top_k=top_k,
    )
    return PolicyRetrievalResult(
        policy_name=normalized_policy_name,
        query=normalized_query,
        policy_id=document.policy_id,
        title=document.title,
        version=document.version,
        effective_date=document.effective_date,
        owner=document.owner,
        source_path=document.source_path,
        text=document.text,
        retrieve_decision=retrieve_decision.to_dict(),
        relevance=relevance.to_dict(),
        evidence_strips=evidence_strips,
    ).to_dict()


def apply_credit(
    customer_id: str,
    amount: float,
    reason: str,
    *,
    policy_context: dict,
    policy_name: str = "service_credit_dag",
    applied_to_invoice: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict:
    normalized_customer_id = customer_id.strip()
    normalized_reason = " ".join(reason.split())
    normalized_policy_name = policy_name.strip()
    normalized_invoice_id = applied_to_invoice.strip() if isinstance(applied_to_invoice, str) else None
    numeric_amount = float(amount)

    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")
    if numeric_amount <= 0:
        raise ValueError("amount must be greater than 0")
    if not normalized_reason:
        raise ValueError("reason must not be empty")
    if not normalized_policy_name:
        raise ValueError("policy_name must not be empty")
    if not isinstance(policy_context, dict):
        raise ValueError("policy_context must be a dict")
    if applied_to_invoice is not None and not normalized_invoice_id:
        raise ValueError("applied_to_invoice must not be empty when provided")

    validation = PolicyGraphValidator().authorize_action(
        normalized_policy_name,
        "apply_credit",
        policy_context,
    )
    max_amount = validation.action_args.get("max_amount")
    if max_amount is not None and numeric_amount > float(max_amount):
        raise PolicyActionBlocked(
            f"credit amount {numeric_amount:g} exceeds policy cap {float(max_amount):g} "
            f"for path {validation.path}"
        )

    applied_at = datetime.utcnow().replace(microsecond=0).isoformat()
    credit_id = f"CR-{uuid4().hex[:12].upper()}"
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        customer_row = connection.execute(
            "SELECT customer_id FROM customers WHERE customer_id = ?",
            (normalized_customer_id,),
        ).fetchone()
        if customer_row is None:
            raise ValueError(f"customer {normalized_customer_id!r} not found")

        if normalized_invoice_id is not None:
            invoice_row = connection.execute(
                """
                SELECT invoice_id
                FROM invoices
                WHERE invoice_id = ?
                  AND customer_id = ?
                """,
                (normalized_invoice_id, normalized_customer_id),
            ).fetchone()
            if invoice_row is None:
                raise ValueError(
                    f"invoice {normalized_invoice_id!r} was not found for customer {normalized_customer_id!r}"
                )

        connection.execute(
            """
            INSERT INTO credits (
                credit_id,
                customer_id,
                amount,
                reason,
                policy_id,
                applied_at,
                applied_to_invoice
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                credit_id,
                normalized_customer_id,
                numeric_amount,
                normalized_reason,
                None,
                applied_at,
                normalized_invoice_id,
            ),
        )

    policy_status = "compliant" if validation.action == "apply_credit" else "non_compliant"
    return CreditApplicationResult(
        credit_id=credit_id,
        customer_id=normalized_customer_id,
        amount=numeric_amount,
        reason=normalized_reason,
        applied_to_invoice=normalized_invoice_id,
        applied_at=applied_at,
        policy_name=validation.policy_name,
        policy_action=validation.action,
        policy_action_args=validation.action_args,
        policy_path=validation.path,
        ujcs=validation.ujcs,
        policy_status=policy_status,
    ).to_dict()


def create_ticket(
    customer_id: str,
    issue_type: str,
    *,
    priority: str = "medium",
    status: str = "open",
    policy_name: str | None = None,
    policy_context: dict | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict:
    normalized_customer_id = customer_id.strip()
    normalized_issue_type = issue_type.strip()
    normalized_priority = priority.strip()
    normalized_status = status.strip()
    normalized_policy_name = policy_name.strip() if isinstance(policy_name, str) else None

    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")
    if not normalized_issue_type:
        raise ValueError("issue_type must not be empty")
    if normalized_priority not in {"low", "medium", "high", "critical"}:
        raise ValueError("priority must be one of low, medium, high, critical")
    if normalized_status not in {"open", "in_progress", "resolved", "escalated"}:
        raise ValueError("status must be one of open, in_progress, resolved, escalated")
    if policy_name is not None and not normalized_policy_name:
        raise ValueError("policy_name must not be empty when provided")
    if policy_context is not None and not isinstance(policy_context, dict):
        raise ValueError("policy_context must be a dict when provided")

    validation = None
    if normalized_policy_name is not None:
        if policy_context is None:
            raise ValueError("policy_context must be provided when policy_name is provided")
        validation = PolicyGraphValidator().authorize_action(
            normalized_policy_name,
            "create_ticket",
            policy_context,
        )
        expected_ticket_type = validation.action_args.get("ticket_type")
        if expected_ticket_type and normalized_issue_type != expected_ticket_type:
            raise PolicyActionBlocked(
                f"ticket type {normalized_issue_type!r} blocked by {normalized_policy_name}; "
                f"DAG requires {expected_ticket_type!r} via path {validation.path}"
            )

    ticket_id = f"TKT-{uuid4().hex[:12].upper()}"
    created_at = datetime.utcnow().replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        customer_row = connection.execute(
            "SELECT customer_id FROM customers WHERE customer_id = ?",
            (normalized_customer_id,),
        ).fetchone()
        if customer_row is None:
            raise ValueError(f"customer {normalized_customer_id!r} not found")

        connection.execute(
            """
            INSERT INTO tickets (
                ticket_id,
                customer_id,
                issue_type,
                status,
                priority,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                normalized_customer_id,
                normalized_issue_type,
                normalized_status,
                normalized_priority,
                created_at,
            ),
        )

    return TicketCreationResult(
        ticket_id=ticket_id,
        customer_id=normalized_customer_id,
        issue_type=normalized_issue_type,
        status=normalized_status,
        priority=normalized_priority,
        created_at=created_at,
        policy_name=validation.policy_name if validation else None,
        policy_action=validation.action if validation else None,
        policy_action_args=validation.action_args if validation else {},
        policy_path=validation.path if validation else [],
        ujcs=validation.ujcs if validation else None,
        policy_status="compliant" if validation else "pending",
    ).to_dict()


def schedule_technician(
    customer_id: str,
    time_slot: str,
    *,
    policy_context: dict,
    policy_name: str = "technician_dispatch_dag",
    ticket_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict:
    normalized_customer_id = customer_id.strip()
    normalized_time_slot = " ".join(time_slot.split())
    normalized_policy_name = policy_name.strip()
    normalized_ticket_id = ticket_id.strip() if isinstance(ticket_id, str) else None

    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")
    if not normalized_time_slot:
        raise ValueError("time_slot must not be empty")
    if not normalized_policy_name:
        raise ValueError("policy_name must not be empty")
    if not isinstance(policy_context, dict):
        raise ValueError("policy_context must be a dict")
    if ticket_id is not None and not normalized_ticket_id:
        raise ValueError("ticket_id must not be empty when provided")

    validation = PolicyGraphValidator().authorize_action(
        normalized_policy_name,
        "schedule_technician",
        policy_context,
    )
    if validation.action_args.get("requires_ticket") is not True:
        raise PolicyActionBlocked(
            f"technician scheduling blocked by {normalized_policy_name}; "
            f"policy path {validation.path} did not require a linked ticket"
        )

    scheduled_at = datetime.utcnow().replace(microsecond=0).isoformat()
    ticket_created = False
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        customer_row = connection.execute(
            "SELECT customer_id FROM customers WHERE customer_id = ?",
            (normalized_customer_id,),
        ).fetchone()
        if customer_row is None:
            raise ValueError(f"customer {normalized_customer_id!r} not found")

        if normalized_ticket_id is None:
            normalized_ticket_id = f"TKT-{uuid4().hex[:12].upper()}"
            ticket_created = True
            connection.execute(
                """
                INSERT INTO tickets (
                    ticket_id,
                    customer_id,
                    issue_type,
                    status,
                    priority,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_ticket_id,
                    normalized_customer_id,
                    "technician_dispatch",
                    "in_progress",
                    "high",
                    scheduled_at,
                ),
            )
        else:
            ticket_row = connection.execute(
                """
                SELECT ticket_id, customer_id
                FROM tickets
                WHERE ticket_id = ?
                """,
                (normalized_ticket_id,),
            ).fetchone()
            if ticket_row is None:
                raise ValueError(f"ticket {normalized_ticket_id!r} not found")
            if ticket_row["customer_id"] != normalized_customer_id:
                raise ValueError(
                    f"ticket {normalized_ticket_id!r} does not belong to customer {normalized_customer_id!r}"
                )
            connection.execute(
                """
                UPDATE tickets
                SET status = 'in_progress',
                    priority = CASE
                        WHEN priority IN ('critical', 'high') THEN priority
                        ELSE 'high'
                    END
                WHERE ticket_id = ?
                """,
                (normalized_ticket_id,),
            )

        ticket_status = connection.execute(
            "SELECT status FROM tickets WHERE ticket_id = ?",
            (normalized_ticket_id,),
        ).fetchone()["status"]

    return TechnicianScheduleResult(
        appointment_id=f"APT-{normalized_ticket_id.removeprefix('TKT-')}",
        customer_id=normalized_customer_id,
        time_slot=normalized_time_slot,
        slot_confirmed=True,
        technician_name=_technician_name_for(normalized_customer_id, normalized_time_slot),
        ticket_id=normalized_ticket_id,
        ticket_created=ticket_created,
        ticket_status=ticket_status,
        scheduled_at=scheduled_at,
        policy_name=validation.policy_name,
        policy_action=validation.action,
        policy_action_args=validation.action_args,
        policy_path=validation.path,
        ujcs=validation.ujcs,
        policy_status="compliant",
    ).to_dict()


def change_plan(
    customer_id: str,
    new_plan_id: str,
    *,
    policy_context: dict,
    policy_name: str = "plan_downgrade_dag",
    effective_date: date | str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict:
    normalized_customer_id = customer_id.strip()
    normalized_new_plan_id = new_plan_id.strip()
    normalized_policy_name = policy_name.strip()

    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")
    if not normalized_new_plan_id:
        raise ValueError("new_plan_id must not be empty")
    if not normalized_policy_name:
        raise ValueError("policy_name must not be empty")
    if not isinstance(policy_context, dict):
        raise ValueError("policy_context must be a dict")

    validation = PolicyGraphValidator().authorize_action(
        normalized_policy_name,
        "change_plan",
        policy_context,
    )
    effective_policy = str(validation.action_args.get("effective", "next_billing_cycle"))
    normalized_effective_date = _resolve_plan_effective_date(
        effective_date=effective_date,
        effective_policy=effective_policy,
    )
    changed_at = datetime.utcnow().replace(microsecond=0).isoformat()

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        customer_row = connection.execute(
            """
            SELECT
                c.customer_id,
                c.plan_id AS previous_plan_id,
                c.account_status,
                current_plan.plan_name AS previous_plan_name,
                current_plan.monthly_price AS previous_monthly_price,
                current_plan.speed_mbps AS previous_speed_mbps,
                current_plan.cancellation_fee AS previous_cancellation_fee
            FROM customers c
            JOIN plans current_plan ON current_plan.plan_id = c.plan_id
            WHERE c.customer_id = ?
            """,
            (normalized_customer_id,),
        ).fetchone()
        if customer_row is None:
            raise ValueError(f"customer {normalized_customer_id!r} not found")

        new_plan_row = connection.execute(
            """
            SELECT plan_id, plan_name, monthly_price, speed_mbps, cancellation_fee
            FROM plans
            WHERE plan_id = ?
            """,
            (normalized_new_plan_id,),
        ).fetchone()
        if new_plan_row is None:
            raise ValueError(f"plan {normalized_new_plan_id!r} not found")
        if customer_row["previous_plan_id"] == normalized_new_plan_id:
            raise ValueError(f"customer {normalized_customer_id!r} is already on plan {normalized_new_plan_id!r}")

        change_type = _plan_change_type(
            previous_price=float(customer_row["previous_monthly_price"]),
            new_price=float(new_plan_row["monthly_price"]),
            previous_speed=int(customer_row["previous_speed_mbps"]),
            new_speed=int(new_plan_row["speed_mbps"]),
        )
        expected_change_type = validation.action_args.get("change_type")
        if expected_change_type and expected_change_type != change_type:
            raise PolicyActionBlocked(
                f"plan change type {change_type!r} blocked by {normalized_policy_name}; "
                f"DAG requires {expected_change_type!r} via path {validation.path}"
            )

        connection.execute(
            """
            UPDATE customers
            SET plan_id = ?
            WHERE customer_id = ?
            """,
            (normalized_new_plan_id, normalized_customer_id),
        )

    fee_disclosure_required = bool(validation.action_args.get("fee_disclosure_required", False))
    return PlanChangeResult(
        customer_id=normalized_customer_id,
        previous_plan_id=customer_row["previous_plan_id"],
        previous_plan_name=customer_row["previous_plan_name"],
        new_plan_id=new_plan_row["plan_id"],
        new_plan_name=new_plan_row["plan_name"],
        monthly_price_before=float(customer_row["previous_monthly_price"]),
        monthly_price_after=float(new_plan_row["monthly_price"]),
        speed_mbps_before=int(customer_row["previous_speed_mbps"]),
        speed_mbps_after=int(new_plan_row["speed_mbps"]),
        change_type=change_type,
        effective_date=normalized_effective_date,
        fee_disclosure_required=fee_disclosure_required,
        cancellation_fee=float(customer_row["previous_cancellation_fee"]) if fee_disclosure_required else 0.0,
        changed_at=changed_at,
        policy_name=validation.policy_name,
        policy_action=validation.action,
        policy_action_args=validation.action_args,
        policy_path=validation.path,
        ujcs=validation.ujcs,
        policy_status="compliant",
    ).to_dict()


def generate_handoff_summary(
    conversation_id: str,
    *,
    handoff_reason: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict | None:
    context_card = generate_context_card(
        conversation_id,
        handoff_reason=handoff_reason,
        db_path=db_path,
    )
    if context_card is None:
        return None
    generated_at = datetime.utcnow().replace(microsecond=0).isoformat()

    return HandoffSummaryResult(
        handoff_summary_id=f"HND-SUM-{uuid4().hex[:12].upper()}",
        session_id=context_card["session_id"],
        customer_id=context_card["customer_id"],
        generated_at=generated_at,
        reason_for_escalation=context_card["reason_for_escalation"],
        recommended_opening=context_card["recommended_opening"],
        customer=context_card["customer"],
        issues_detected=context_card["issues_detected"],
        issues_resolved=context_card["issues_resolved"],
        issues_remaining=context_card["issues_remaining"],
        emotion=context_card["emotion"],
        urgency=context_card["urgency"],
        slots_collected=context_card["slots_collected"],
        tools_called=context_card["tools_called"],
        policies_retrieved=context_card["policies_retrieved"],
        policy_nodes_visited=context_card["policy_nodes_visited"],
        evidence_used=context_card["evidence_used"],
        actions_taken=context_card["actions_taken"],
        memory_context=context_card["memory_context"],
        context_card=context_card,
    ).to_dict()


def generate_context_card(
    conversation_id: str,
    *,
    handoff_reason: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict | None:
    normalized_session_id = conversation_id.strip()
    normalized_handoff_reason = " ".join(handoff_reason.split()) if isinstance(handoff_reason, str) else None
    if not normalized_session_id:
        raise ValueError("conversation_id must not be empty")
    if handoff_reason is not None and not normalized_handoff_reason:
        raise ValueError("handoff_reason must not be empty when provided")

    generated_at = datetime.utcnow().replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        conversation_row = connection.execute(
            """
            SELECT
                conv.session_id,
                conv.customer_id,
                conv.messages,
                conv.intents,
                conv.slots,
                conv.tools_called,
                conv.health_scores,
                conv.final_status,
                conv.relationship_score_start,
                conv.relationship_score_end,
                conv.relationship_delta,
                cust.name,
                cust.email,
                cust.location,
                cust.risk_level,
                cust.preferred_language,
                cust.account_status,
                cust.churn_score,
                plan.plan_id,
                plan.plan_name,
                plan.monthly_price,
                plan.speed_mbps
            FROM conversations conv
            JOIN customers cust ON cust.customer_id = conv.customer_id
            JOIN plans plan ON plan.plan_id = cust.plan_id
            WHERE conv.session_id = ?
            """,
            (normalized_session_id,),
        ).fetchone()
        if conversation_row is None:
            return None

        audit_row = connection.execute(
            """
            SELECT
                case_id,
                tools_called,
                evidence_used,
                action_taken,
                policy_dag_path,
                ujcs,
                policy_status,
                health_score,
                handoff_required,
                created_at
            FROM audit_logs
            WHERE session_id = ?
            ORDER BY datetime(created_at) DESC, case_id DESC
            LIMIT 1
            """,
            (normalized_session_id,),
        ).fetchone()
        handoff_row = connection.execute(
            """
            SELECT h.handoff_id, h.case_id, h.handoff_reason, h.status, h.created_at, h.assigned_to
            FROM human_handoff_queue h
            JOIN audit_logs a ON a.case_id = h.case_id
            WHERE a.session_id = ?
            ORDER BY datetime(h.created_at) DESC, h.handoff_id DESC
            LIMIT 1
            """,
            (normalized_session_id,),
        ).fetchone()
        memory_rows = connection.execute(
            """
            SELECT memory_id, memory_type, content, entity_tags, updated_at
            FROM memory_store
            WHERE customer_id = ?
            ORDER BY datetime(updated_at) DESC, memory_id DESC
            LIMIT 5
            """,
            (conversation_row["customer_id"],),
        ).fetchall()

    messages = _json_value(conversation_row["messages"], [])
    intents = [str(intent) for intent in _json_value(conversation_row["intents"], [])]
    slots = _json_value(conversation_row["slots"], {})
    conversation_tools = _normalize_tool_calls(_json_value(conversation_row["tools_called"], []))
    audit_tools = _normalize_tool_calls(_json_value(audit_row["tools_called"], []) if audit_row else [])
    tools_called = _dedupe_dicts(conversation_tools + audit_tools, key="name")
    evidence_used = _json_value(audit_row["evidence_used"], []) if audit_row else []
    actions_taken = _json_value(audit_row["action_taken"], []) if audit_row else []
    policy_nodes_visited = [str(node) for node in (_json_value(audit_row["policy_dag_path"], []) if audit_row else [])]
    policy_dag_path_so_far = _policy_dag_path_so_far(policy_nodes_visited, audit_row)
    health_scores = _json_value(conversation_row["health_scores"], [])
    latest_health_score = _latest_numeric(health_scores)
    if latest_health_score is None and audit_row and audit_row["health_score"] is not None:
        latest_health_score = float(audit_row["health_score"])
    emotion, urgency = _handoff_emotion(latest_health_score, conversation_row["relationship_score_end"])
    issues_detected = _handoff_issues(intents, actions_taken, conversation_row["final_status"])
    issues_resolved = [issue for issue in issues_detected if issue["status"] == "resolved"]
    issues_remaining = [issue for issue in issues_detected if issue["status"] != "resolved"]
    issues_summary = _issues_summary(issues_detected, issues_resolved, issues_remaining)
    reason = normalized_handoff_reason or _default_handoff_reason(
        final_status=conversation_row["final_status"],
        audit_row=audit_row,
        issues_remaining=issues_remaining,
    )
    customer = {
        "customer_id": conversation_row["customer_id"],
        "name": conversation_row["name"],
        "email": conversation_row["email"],
        "location": conversation_row["location"],
        "plan_id": conversation_row["plan_id"],
        "plan_name": conversation_row["plan_name"],
        "monthly_price": float(conversation_row["monthly_price"]),
        "speed_mbps": int(conversation_row["speed_mbps"]),
        "risk_level": conversation_row["risk_level"],
        "preferred_language": conversation_row["preferred_language"],
        "account_status": conversation_row["account_status"],
        "churn_score": float(conversation_row["churn_score"]),
    }
    memory_context = [
        {
            "memory_id": row["memory_id"],
            "memory_type": row["memory_type"],
            "content": row["content"],
            "entity_tags": _json_value(row["entity_tags"], []),
            "updated_at": row["updated_at"],
        }
        for row in memory_rows
    ]
    policies_retrieved = _policies_from_tool_calls(tools_called)
    recommended_opening = _opening_line_text(
        customer_name=customer["name"],
        issues_remaining=issues_remaining,
        reason=reason,
    )
    relationship = {
        "start": _optional_float(conversation_row["relationship_score_start"]),
        "end": _optional_float(conversation_row["relationship_score_end"]),
        "delta": _optional_float(conversation_row["relationship_delta"]),
    }
    audit = _audit_context(audit_row)
    handoff_queue = _handoff_queue_context(handoff_row)

    return ContextCardResult(
        context_card_id=f"CTX-{uuid4().hex[:12].upper()}",
        session_id=normalized_session_id,
        customer_id=conversation_row["customer_id"],
        generated_at=generated_at,
        case_id=audit["case_id"] if audit else None,
        customer=customer,
        issues_detected=issues_detected,
        issues_resolved=issues_resolved,
        issues_remaining=issues_remaining,
        issues_summary=issues_summary,
        emotion=emotion,
        urgency=urgency,
        current_health_score=latest_health_score,
        relationship=relationship,
        slots_collected=slots if isinstance(slots, dict) else {},
        tools_called=tools_called,
        policies_retrieved=policies_retrieved,
        policy_nodes_visited=policy_nodes_visited,
        policy_dag_path_so_far=policy_dag_path_so_far,
        evidence_used=evidence_used,
        actions_taken=actions_taken,
        audit=audit,
        handoff_queue=handoff_queue,
        reason_for_escalation=reason,
        recommended_opening=recommended_opening,
        memory_context=memory_context,
        last_customer_message=_last_message(messages, role="user"),
    ).to_dict()


def generate_opening_line(
    conversation_id: str | None = None,
    *,
    context_card: dict | None = None,
    handoff_reason: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict | None:
    if context_card is not None:
        if not isinstance(context_card, dict):
            raise ValueError("context_card must be a dict when provided")
        normalized_reason = _normalize_optional_text(handoff_reason, "handoff_reason")
        return _opening_line_result(context_card, handoff_reason=normalized_reason)

    if conversation_id is None:
        raise ValueError("conversation_id or context_card is required")
    if not isinstance(conversation_id, str):
        raise ValueError("conversation_id must be a string")
    if not conversation_id.strip():
        raise ValueError("conversation_id must not be empty")
    card = generate_context_card(
        conversation_id,
        handoff_reason=handoff_reason,
        db_path=db_path,
    )
    if card is None:
        return None
    return _opening_line_result(card)


def build_audit_log(
    case_id: str,
    *,
    customer_id: str,
    session_id: str,
    tools_called: list | None = None,
    evidence_used: list | None = None,
    action_taken: list | None = None,
    policy_dag_path: list | None = None,
    policy_name: str | None = None,
    tool_results: list | None = None,
    policy_result=None,
    context_card: dict | None = None,
    ujcs: float | None = None,
    policy_status: str | None = None,
    health_score: float | None = None,
    handoff_required: bool = False,
) -> dict:
    normalized_case_id = _require_audit_text(case_id, "case_id")
    normalized_customer_id = _require_audit_text(customer_id, "customer_id")
    normalized_session_id = _require_audit_text(session_id, "session_id")
    context_payload = _context_card_payload(context_card)
    tool_result_payloads = _json_ready_list(tool_results or [], "tool_results")
    policy_payload = _payload_dict(policy_result)

    tools_payload = _dedupe_payloads(
        _json_ready_list(tools_called or [], "tools_called")
        + _tools_from_tool_results(tool_result_payloads)
        + _normalize_tool_calls(context_payload.get("tools_called", []))
    )
    evidence_payload = _dedupe_payloads(
        _json_ready_list(evidence_used or [], "evidence_used")
        + _evidence_from_tool_results(tool_result_payloads)
        + _json_ready_list(context_payload.get("evidence_used", []), "context_card.evidence_used")
    )
    actions_payload = _dedupe_payloads(
        _json_ready_list(action_taken or [], "action_taken")
        + _actions_from_tool_results(tool_result_payloads)
        + _json_ready_list(context_payload.get("actions_taken", []), "context_card.actions_taken")
    )
    path_payload = _audit_policy_path(
        explicit_path=policy_dag_path,
        policy_payload=policy_payload,
        tool_results=tool_result_payloads,
        context_card=context_payload,
    )
    resolved_policy_name = _audit_policy_name(
        explicit_policy_name=policy_name,
        policy_payload=policy_payload,
        tool_results=tool_result_payloads,
        context_card=context_payload,
    )

    resolved_ujcs = _optional_float_in_range(
        _first_not_none(
            ujcs,
            policy_payload.get("ujcs"),
            _tool_result_value(tool_result_payloads, "ujcs"),
            _audit_context_value(context_payload, "ujcs"),
            _computed_ujcs(resolved_policy_name, path_payload),
        ),
        "ujcs",
        minimum=0,
        maximum=1,
    )
    resolved_health_score = _optional_float_in_range(
        _first_not_none(health_score, context_payload.get("current_health_score"), _audit_context_value(context_payload, "health_score")),
        "health_score",
        minimum=0,
        maximum=100,
    )
    resolved_policy_status = _normalize_policy_status(
        _first_not_none(
            policy_status,
            policy_payload.get("policy_status"),
            policy_payload.get("status"),
            _tool_result_value(tool_result_payloads, "policy_status"),
            _audit_context_value(context_payload, "policy_status"),
        ),
        resolved_ujcs,
    )
    resolved_handoff_required = bool(
        handoff_required
        or _audit_context_value(context_payload, "handoff_required")
        or context_payload.get("handoff_queue")
    )
    raw_json = {
        "case_id": normalized_case_id,
        "customer_id": normalized_customer_id,
        "session_id": normalized_session_id,
        "tools_called": tools_payload,
        "evidence_used": evidence_payload,
        "action_taken": actions_payload,
        "policy_dag_path": path_payload,
        "ujcs": resolved_ujcs,
        "policy_status": resolved_policy_status,
        "health_score": resolved_health_score,
        "handoff_required": resolved_handoff_required,
    }
    return AuditLogDraft(
        case_id=normalized_case_id,
        customer_id=normalized_customer_id,
        session_id=normalized_session_id,
        tools_called=tools_payload,
        evidence_used=evidence_payload,
        action_taken=actions_payload,
        policy_dag_path=path_payload,
        ujcs=resolved_ujcs,
        policy_status=resolved_policy_status,
        health_score=resolved_health_score,
        handoff_required=resolved_handoff_required,
        human_summary=_audit_human_summary(raw_json),
        raw_json=raw_json,
    ).to_dict()


def generate_audit_log(
    case_id: str,
    *,
    customer_id: str,
    session_id: str,
    tools_called: list,
    evidence_used: list,
    action_taken: list,
    policy_dag_path: list,
    policy_name: str | None = None,
    ujcs: float | None = None,
    policy_status: str | None = None,
    health_score: float | None = None,
    handoff_required: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict:
    draft = build_audit_log(
        case_id,
        customer_id=customer_id,
        session_id=session_id,
        tools_called=tools_called,
        evidence_used=evidence_used,
        action_taken=action_taken,
        policy_dag_path=policy_dag_path,
        policy_name=policy_name,
        ujcs=ujcs,
        policy_status=policy_status,
        health_score=health_score,
        handoff_required=handoff_required,
    )
    normalized_case_id = draft["case_id"]
    normalized_customer_id = draft["customer_id"]
    normalized_session_id = draft["session_id"]
    tools_payload = draft["tools_called"]
    evidence_payload = draft["evidence_used"]
    actions_payload = draft["action_taken"]
    path_payload = draft["policy_dag_path"]
    normalized_ujcs = draft["ujcs"]
    normalized_health_score = draft["health_score"]
    normalized_policy_status = draft["policy_status"]

    created_at = datetime.utcnow().replace(microsecond=0).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        customer_row = connection.execute(
            "SELECT customer_id FROM customers WHERE customer_id = ?",
            (normalized_customer_id,),
        ).fetchone()
        if customer_row is None:
            raise ValueError(f"customer {normalized_customer_id!r} not found")

        session_row = connection.execute(
            "SELECT customer_id FROM conversations WHERE session_id = ?",
            (normalized_session_id,),
        ).fetchone()
        if session_row is None:
            raise ValueError(f"session {normalized_session_id!r} not found")
        if session_row["customer_id"] != normalized_customer_id:
            raise ValueError(
                f"session {normalized_session_id!r} does not belong to customer {normalized_customer_id!r}"
            )

        existing = connection.execute(
            "SELECT case_id FROM audit_logs WHERE case_id = ?",
            (normalized_case_id,),
        ).fetchone()
        inserted = existing is None
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
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                normalized_case_id,
                normalized_customer_id,
                normalized_session_id,
                json.dumps(tools_payload),
                json.dumps(evidence_payload),
                json.dumps(actions_payload),
                json.dumps(path_payload),
                normalized_ujcs,
                normalized_policy_status,
                normalized_health_score,
                int(bool(handoff_required)),
                created_at,
            ),
        )
        row = connection.execute(
            """
            SELECT
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
            FROM audit_logs
            WHERE case_id = ?
            """,
            (normalized_case_id,),
        ).fetchone()

    raw_json = {
        "case_id": row["case_id"],
        "customer_id": row["customer_id"],
        "session_id": row["session_id"],
        "tools_called": _json_value(row["tools_called"], []),
        "evidence_used": _json_value(row["evidence_used"], []),
        "action_taken": _json_value(row["action_taken"], []),
        "policy_dag_path": _json_value(row["policy_dag_path"], []),
        "ujcs": float(row["ujcs"]) if row["ujcs"] is not None else None,
        "policy_status": row["policy_status"],
        "health_score": float(row["health_score"]) if row["health_score"] is not None else None,
        "handoff_required": bool(row["handoff_required"]),
        "created_at": row["created_at"],
    }
    human_summary = _audit_human_summary(raw_json)
    return AuditLogResult(
        case_id=raw_json["case_id"],
        customer_id=raw_json["customer_id"],
        session_id=raw_json["session_id"],
        tools_called=raw_json["tools_called"],
        evidence_used=raw_json["evidence_used"],
        action_taken=raw_json["action_taken"],
        policy_dag_path=raw_json["policy_dag_path"],
        ujcs=raw_json["ujcs"],
        policy_status=raw_json["policy_status"],
        health_score=raw_json["health_score"],
        handoff_required=raw_json["handoff_required"],
        created_at=raw_json["created_at"],
        human_summary=human_summary,
        raw_json=raw_json,
        inserted=inserted,
    ).to_dict()


def _reference_date_string(reference_date: date | str | None) -> str:
    if reference_date is None:
        return date.today().isoformat()
    if isinstance(reference_date, date):
        return reference_date.isoformat()
    normalized = reference_date.strip()
    if not normalized:
        raise ValueError("reference_date must not be empty")
    return normalized


def _reference_datetime(reference_date: date | str | None) -> datetime:
    reference = _reference_date_string(reference_date)
    parsed = datetime.fromisoformat(reference)
    if parsed.time() == time.min:
        return datetime.combine(parsed.date(), time.max.replace(microsecond=0))
    return parsed


def _payment_dict(row: sqlite3.Row) -> dict:
    return {
        "payment_id": row["payment_id"],
        "customer_id": row["customer_id"],
        "amount": float(row["amount"]),
        "date": row["date"],
        "method": row["method"],
        "duplicate_flag": bool(row["duplicate_flag"]),
        "timestamp": datetime.fromisoformat(row["date"]),
    }


def _invoice_match_dict(row: sqlite3.Row) -> dict:
    return {
        "invoice_id": row["invoice_id"],
        "amount": float(row["amount"]),
        "date": row["date"],
        "status": row["status"],
        "payment_id": row["payment_id"],
    }


def _detect_duplicate_payment_groups(payments: list[dict], invoices: list[dict]) -> list[dict]:
    groups = []
    used_pairs = set()
    for left_index, left in enumerate(payments):
        for right in payments[left_index + 1 :]:
            pair_key = tuple(sorted((left["payment_id"], right["payment_id"])))
            if pair_key in used_pairs:
                continue
            if left["amount"] != right["amount"]:
                continue
            if left["method"] != right["method"]:
                continue
            minutes_apart = abs((right["timestamp"] - left["timestamp"]).total_seconds()) / 60
            if minutes_apart > 10:
                continue

            matching_invoices = [
                invoice
                for invoice in invoices
                if invoice["amount"] == left["amount"]
                and invoice["date"] == left["timestamp"].date().isoformat()
            ]
            invoice_id = matching_invoices[0]["invoice_id"] if len(matching_invoices) == 1 else None
            groups.append(
                {
                    "payment_ids": [left["payment_id"], right["payment_id"]],
                    "amount": left["amount"],
                    "method": left["method"],
                    "payment_timestamps": [left["date"], right["date"]],
                    "minutes_apart": round(minutes_apart, 2),
                    "invoice_id": invoice_id,
                    "single_matching_invoice": len(matching_invoices) == 1,
                    "duplicate_flags": [left["duplicate_flag"], right["duplicate_flag"]],
                    "evidence": [
                        "same customer_id",
                        "same payment amount",
                        "same payment method",
                        "payment timestamps within 10 minutes",
                        "single matching invoice" if len(matching_invoices) == 1 else "matching invoice not unique",
                    ],
                }
            )
            used_pairs.add(pair_key)

    return sorted(
        groups,
        key=lambda group: (
            -int(group["single_matching_invoice"]),
            group["payment_timestamps"][0],
            group["payment_ids"][0],
        ),
    )


def _json_list(raw_value: str) -> list[str]:
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _json_value(raw_value: str | None, default):
    if raw_value is None:
        return default
    try:
        value = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return default
    return value


def _json_ready_list(value, field_name: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON serializable") from exc
    return value


def _require_audit_text(value, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _context_card_payload(context_card: dict | None) -> dict:
    if context_card is None:
        return {}
    if not isinstance(context_card, dict):
        raise ValueError("context_card must be a dict when provided")
    try:
        json.dumps(context_card)
    except (TypeError, ValueError) as exc:
        raise ValueError("context_card must be JSON serializable") from exc
    return context_card


def _payload_dict(value) -> dict:
    if value is None:
        return {}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    elif hasattr(value, "__dict__") and not isinstance(value, (str, int, float, bool)):
        value = vars(value)
    return value if isinstance(value, dict) else {}


def _dedupe_payloads(items: list) -> list:
    deduped = []
    seen = set()
    for item in items:
        key = _dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _dedupe_key(item) -> str:
    if isinstance(item, dict):
        tool_name = _clean_tool_name(item)
        if tool_name:
            return json.dumps({"tool_name": tool_name, "status": item.get("status")}, sort_keys=True)
    try:
        return json.dumps(item, sort_keys=True)
    except (TypeError, ValueError):
        return repr(item)


def _tools_from_tool_results(tool_results: list) -> list:
    tools = []
    for item in tool_results:
        payload = _payload_dict(item)
        name = _clean_tool_name(payload)
        if not name:
            continue
        entry = {"tool_name": name, "status": str(payload.get("status") or "ok")}
        args = payload.get("args") or payload.get("arguments")
        if isinstance(args, dict):
            entry["args"] = args
        result = payload.get("result")
        if isinstance(result, dict):
            entry["result"] = _audit_result_summary(result)
        tools.append(entry)
    return tools


def _clean_tool_name(payload: dict) -> str | None:
    value = payload.get("tool_name") or payload.get("name") or payload.get("tool")
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _audit_result_summary(result: dict) -> dict:
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
        "handoff_id",
    )
    summary = {key: result[key] for key in keep_keys if key in result}
    return summary or {"recorded": True}


def _evidence_from_tool_results(tool_results: list) -> list:
    evidence = []
    for item in tool_results:
        payload = _payload_dict(item)
        for source in (payload, _payload_dict(payload.get("result"))):
            evidence.extend(_list_field(source, "evidence"))
            evidence.extend(_list_field(source, "evidence_used"))
    return evidence


def _actions_from_tool_results(tool_results: list) -> list:
    actions = []
    for item in tool_results:
        payload = _payload_dict(item)
        for source in (payload, _payload_dict(payload.get("result"))):
            actions.extend(_list_field(source, "action_taken"))
            actions.extend(_list_field(source, "actions_taken"))
            actions.extend(_list_field(source, "actions"))
            action = source.get("action")
            if action:
                actions.append(action if isinstance(action, dict) else {"action": str(action)})
    return actions


def _list_field(payload: dict, field_name: str) -> list:
    value = payload.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        return [value]
    return value


def _audit_policy_path(*, explicit_path, policy_payload: dict, tool_results: list, context_card: dict) -> list:
    if explicit_path is not None:
        return _json_ready_list(explicit_path, "policy_dag_path")
    for payload in (policy_payload,):
        path = _path_from_payload(payload)
        if path:
            return path
    for item in tool_results:
        payload = _payload_dict(item)
        for source in (payload, _payload_dict(payload.get("result"))):
            path = _path_from_payload(source)
            if path:
                return path
    policy_path = _payload_dict(context_card.get("policy_dag_path_so_far")).get("nodes")
    if isinstance(policy_path, list):
        return [str(node) for node in policy_path]
    raw_nodes = context_card.get("policy_nodes_visited")
    if isinstance(raw_nodes, list):
        return [str(node) for node in raw_nodes]
    return []


def _audit_policy_name(
    *,
    explicit_policy_name: str | None,
    policy_payload: dict,
    tool_results: list,
    context_card: dict,
) -> str | None:
    if explicit_policy_name is not None:
        return _optional_policy_name(explicit_policy_name)
    for value in (
        policy_payload.get("policy_name"),
        policy_payload.get("dag_name"),
        policy_payload.get("name"),
        _tool_result_value(tool_results, "policy_name"),
        _tool_result_value(tool_results, "dag_name"),
        _audit_context_value(context_card, "policy_name"),
    ):
        normalized = _optional_policy_name(value)
        if normalized:
            return normalized
    return None


def _optional_policy_name(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _computed_ujcs(policy_name: str | None, path_payload: list) -> float | None:
    if not policy_name or not path_payload:
        return None
    dags = default_policy_dags()
    dag = dags.get(policy_name)
    if dag is None:
        return None
    return compute_ujcs([str(node) for node in path_payload], dag)


def _path_from_payload(payload: dict) -> list:
    for key in ("policy_dag_path", "policy_path", "path"):
        value = payload.get(key)
        if isinstance(value, list):
            return [str(node) for node in value]
    return []


def _first_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _tool_result_value(tool_results: list, field_name: str):
    for item in tool_results:
        payload = _payload_dict(item)
        for source in (payload, _payload_dict(payload.get("result"))):
            if source.get(field_name) is not None:
                return source[field_name]
    return None


def _audit_context_value(context_card: dict, field_name: str):
    audit = _payload_dict(context_card.get("audit"))
    return audit.get(field_name)


def _normalize_policy_status(value, ujcs: float | None) -> str:
    normalized = value.strip() if isinstance(value, str) else None
    if normalized is None:
        normalized = _policy_status_from_ujcs(ujcs)
    if normalized not in {"pending", "compliant", "non_compliant", "needs_review"}:
        raise ValueError("policy_status must be one of pending, compliant, non_compliant, needs_review")
    return normalized


def _optional_float_in_range(value, field_name: str, *, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if numeric < minimum or numeric > maximum:
        raise ValueError(f"{field_name} must be between {minimum:g} and {maximum:g}")
    return numeric


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _audit_context(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "case_id": row["case_id"],
        "ujcs": _optional_float(row["ujcs"]),
        "policy_status": row["policy_status"],
        "health_score": _optional_float(row["health_score"]),
        "handoff_required": bool(row["handoff_required"]),
        "created_at": row["created_at"],
    }


def _handoff_queue_context(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "handoff_id": row["handoff_id"],
        "case_id": row["case_id"],
        "handoff_reason": row["handoff_reason"],
        "status": row["status"],
        "created_at": row["created_at"],
        "assigned_to": row["assigned_to"],
    }


def _normalize_optional_text(value, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string when provided")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{field_name} must not be empty when provided")
    return normalized


def _policy_status_from_ujcs(ujcs: float | None) -> str:
    if ujcs is None:
        return "pending"
    if ujcs > 0.8:
        return "compliant"
    if ujcs == 0:
        return "non_compliant"
    return "needs_review"


def _audit_human_summary(raw_json: dict) -> str:
    tools = ", ".join(_tool_names_for_summary(raw_json["tools_called"])) or "no tools recorded"
    evidence_count = len(raw_json["evidence_used"])
    action_count = len(raw_json["action_taken"])
    ujcs_text = "not computed" if raw_json["ujcs"] is None else f"{raw_json['ujcs']:.4f}"
    handoff_text = "handoff required" if raw_json["handoff_required"] else "no handoff required"
    return (
        f"Case {raw_json['case_id']} for customer {raw_json['customer_id']} used {tools}; "
        f"{evidence_count} evidence item(s), {action_count} action(s), UJCS {ujcs_text}, "
        f"policy status {raw_json['policy_status']}, {handoff_text}."
    )


def _tool_names_for_summary(tools_called: list) -> list[str]:
    names = []
    for item in tools_called:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("tool_name") or item.get("tool")
            if name:
                names.append(str(name))
    return names


def _normalize_tool_calls(raw_tools) -> list[dict]:
    if not isinstance(raw_tools, list):
        return []
    normalized = []
    for item in raw_tools:
        if isinstance(item, str):
            name = item.strip()
            if name:
                normalized.append({"name": name})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool_name") or item.get("name") or item.get("tool") or "").strip()
        if not name:
            continue
        entry = {"name": name}
        result = item.get("result")
        if isinstance(result, dict):
            entry["result"] = result
        status = item.get("status")
        if status is not None:
            entry["status"] = str(status)
        normalized.append(entry)
    return normalized


def _dedupe_dicts(items: list[dict], *, key: str) -> list[dict]:
    seen = set()
    deduped = []
    for item in items:
        value = item.get(key)
        if value in seen:
            continue
        seen.add(value)
        deduped.append(item)
    return deduped


def _policies_from_tool_calls(tool_calls: list[dict]) -> list[str]:
    policies = []
    for call in tool_calls:
        result = call.get("result", {})
        if call.get("name") != "retrieve_policy" or not isinstance(result, dict):
            continue
        policy_id = result.get("policy_id")
        if isinstance(policy_id, str) and policy_id not in policies:
            policies.append(policy_id)
    return policies


def _latest_numeric(values) -> float | None:
    if not isinstance(values, list):
        return None
    for value in reversed(values):
        if isinstance(value, dict):
            value = value.get("score") or value.get("health_score")
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _handoff_emotion(health_score: float | None, relationship_score_end) -> tuple[str, str]:
    score = health_score
    if score is None:
        try:
            score = float(relationship_score_end)
        except (TypeError, ValueError):
            score = None
    if score is None:
        return "unknown", "medium"
    if score < 30:
        return "frustrated", "high"
    if score < 60:
        return "concerned", "medium"
    return "stable", "low"


def _handoff_issues(intents: list[str], actions_taken, final_status: str) -> list[dict]:
    action_status_by_intent = {}
    if isinstance(actions_taken, list):
        for action in actions_taken:
            if not isinstance(action, dict):
                continue
            intent = str(action.get("intent", "")).strip()
            status = str(action.get("status", "")).strip()
            if intent and status:
                action_status_by_intent[intent] = status
    issues = []
    for intent in intents:
        label = intent.replace("_", " ")
        resolved = action_status_by_intent.get(intent) == "resolved"
        status = "resolved" if resolved else "pending"
        if final_status == "resolved":
            status = "resolved"
        issues.append({"intent": intent, "label": label, "status": status})
    return issues


def _issues_summary(
    issues_detected: list[dict],
    issues_resolved: list[dict],
    issues_remaining: list[dict],
) -> dict:
    resolved_labels = _issue_labels(issues_resolved)
    remaining_labels = _issue_labels(issues_remaining)
    total_count = len(issues_detected)
    resolved_count = len(issues_resolved)
    remaining_count = len(issues_remaining)
    if total_count == 0:
        summary_text = "No customer issues were detected in this session."
    elif remaining_count == 0:
        summary_text = f"All {total_count} detected issue(s) are resolved."
    elif resolved_count == 0:
        summary_text = f"{remaining_count} issue(s) remain unresolved: {', '.join(remaining_labels)}."
    else:
        summary_text = (
            f"{resolved_count} of {total_count} issue(s) resolved; "
            f"{remaining_count} remain: {', '.join(remaining_labels)}."
        )
    return {
        "total_count": total_count,
        "resolved_count": resolved_count,
        "remaining_count": remaining_count,
        "all_resolved": total_count > 0 and remaining_count == 0,
        "has_remaining": remaining_count > 0,
        "resolved_labels": resolved_labels,
        "remaining_labels": remaining_labels,
        "summary_text": summary_text,
    }


def _issue_labels(issues: list[dict]) -> list[str]:
    labels = []
    for issue in issues:
        label = issue.get("label") if isinstance(issue, dict) else None
        if label is None:
            label = issue.get("intent") if isinstance(issue, dict) else None
        normalized = str(label).strip() if label is not None else ""
        if normalized:
            labels.append(normalized)
    return labels


def _policy_dag_path_so_far(policy_nodes_visited: list[str], audit_row) -> dict:
    nodes = [str(node) for node in policy_nodes_visited if str(node).strip()]
    current_node = nodes[-1] if nodes else None
    policy_status = audit_row["policy_status"] if audit_row else None
    ujcs = _optional_float(audit_row["ujcs"]) if audit_row else None
    return {
        "nodes": nodes,
        "node_count": len(nodes),
        "current_node": current_node,
        "has_started": bool(nodes),
        "is_complete": bool(nodes) and policy_status in {"compliant", "non_compliant", "needs_review"},
        "path_text": " -> ".join(nodes),
        "policy_status": policy_status,
        "ujcs": ujcs,
        "source": "audit_logs.policy_dag_path",
    }


def _opening_line_result(context_card: dict, *, handoff_reason: str | None = None) -> dict:
    customer = context_card.get("customer")
    if not isinstance(customer, dict):
        customer = {}
    issues_remaining = context_card.get("issues_remaining")
    if not isinstance(issues_remaining, list):
        issues_remaining = []
    reason = handoff_reason or context_card.get("reason_for_escalation") or "Customer needs specialist support."
    reason = str(reason).strip() or "Customer needs specialist support."
    customer_name = _clean_text(customer.get("name"))
    customer_id = _clean_text(customer.get("customer_id"))
    issue_labels = _issue_labels(issues_remaining)
    return OpeningLineResult(
        opening_line_id=f"OPN-{uuid4().hex[:12].upper()}",
        opening_line=_opening_line_text(
            customer_name=customer_name,
            issues_remaining=issues_remaining,
            reason=reason,
        ),
        customer_id=customer_id,
        customer_name=customer_name,
        issue_labels=issue_labels,
        reason_for_escalation=reason,
        has_remaining_issues=bool(issue_labels),
    ).to_dict()


def _opening_line_text(*, customer_name: str | None, issues_remaining: list[dict], reason: str) -> str:
    greeting = f"Hi {customer_name}" if customer_name else "Hi"
    labels = _issue_labels(issues_remaining)
    if labels:
        issue_text = _join_issue_labels(labels[:2])
        return f"{greeting}, I have your {issue_text} details and prior checks, so you do not have to repeat them."
    clean_reason = str(reason).strip().rstrip(".") or "the escalation reason"
    return f"{greeting}, I have the case context and escalation reason: {clean_reason}, so I can continue from the last step."


def _join_issue_labels(labels: list[str]) -> str:
    if not labels:
        return "case"
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def _clean_text(value) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _default_handoff_reason(*, final_status: str, audit_row, issues_remaining: list[dict]) -> str:
    if audit_row and bool(audit_row["handoff_required"]):
        return "Audit log marked this case for human handoff."
    if final_status == "escalated":
        return "Conversation reached escalated status."
    if issues_remaining:
        labels = ", ".join(issue["label"] for issue in issues_remaining[:3])
        return f"Unresolved customer issues remain: {labels}."
    return "Customer requested human assistance."


def _recommended_handoff_opening(*, customer_name: str, issues_remaining: list[dict], reason: str) -> str:
    return _opening_line_text(customer_name=customer_name, issues_remaining=issues_remaining, reason=reason)


def _last_message(messages, *, role: str) -> str | None:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") == role:
            content = message.get("content")
            return str(content) if content is not None else None
    return None


def _router_recommendation(
    *,
    router_status: str | None,
    signal_strength: int | None,
    diagnostic_available: bool,
) -> str:
    if not diagnostic_available:
        return "Diagnostic record unavailable; escalate or retry diagnostics."
    if router_status == "offline":
        return "Router is offline; guide power check and schedule technician if unresolved."
    if router_status == "degraded" or (signal_strength is not None and signal_strength < 50):
        return "Weak or degraded router signal; run guided reset before technician scheduling."
    return "Router diagnostic is healthy; continue outage or device troubleshooting."


def _find_policy_document(policy_name: str, *, policy_dir: Path) -> PolicyDocument | None:
    wanted = _policy_lookup_key(policy_name)
    for document in load_policy_documents(policy_dir):
        aliases = {
            _policy_lookup_key(document.policy_id),
            _policy_lookup_key(document.title),
            _policy_lookup_key(Path(document.source_path).stem),
        }
        if wanted in aliases:
            return document
    return None


def _top_policy_evidence_strips(*, query: str, document: PolicyDocument, top_k: int) -> list[dict]:
    strips = decompose_policy_to_strips(document.text, source_id=document.policy_id)
    scored = []
    for strip in strips:
        evaluation = evaluate_policy_relevance(query, strip.text)
        scored.append(
            {
                "strip_id": strip.strip_id,
                "source_id": strip.source_id,
                "text": strip.text,
                "token_count": strip.token_count,
                "relevance": evaluation.to_dict(),
            }
        )
    scored.sort(
        key=lambda item: (
            -item["relevance"]["score"],
            item["strip_id"],
        )
    )
    return scored[:top_k]


def _policy_lookup_key(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _technician_name_for(customer_id: str, time_slot: str) -> str:
    technician_names = ["Aarav Mehta", "Neha Rao", "Imran Khan", "Priya Menon", "Rohan Iyer"]
    index = sum(ord(character) for character in f"{customer_id}|{time_slot}") % len(technician_names)
    return technician_names[index]


def _resolve_plan_effective_date(*, effective_date: date | str | None, effective_policy: str) -> str:
    if effective_date is not None:
        return _reference_date_string(effective_date)
    today = date.today()
    if effective_policy == "next_billing_cycle":
        if today.month == 12:
            return date(today.year + 1, 1, 1).isoformat()
        return date(today.year, today.month + 1, 1).isoformat()
    return today.isoformat()


def _plan_change_type(
    *,
    previous_price: float,
    new_price: float,
    previous_speed: int,
    new_speed: int,
) -> str:
    if new_price < previous_price or new_speed < previous_speed:
        return "downgrade"
    if new_price > previous_price or new_speed > previous_speed:
        return "upgrade"
    return "lateral"
