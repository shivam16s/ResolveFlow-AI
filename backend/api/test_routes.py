from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from backend.agent import (
    ActionCandidate,
    TakenAction,
    casa_empathy_sequence,
    confirm_action_replay,
    IntentClassifier,
    build_issue_queue,
    generate_acknowledgment,
    load_taken_actions,
)
from backend.agent.policy_graph import PolicyActionBlocked, PolicyGraphValidator
from backend.tools import (
    check_duplicate_charge,
    check_outage_status,
    get_invoice_history,
    lookup_customer,
    retrieve_policy,
    run_router_diagnostic,
)


router = APIRouter(prefix="/api/test", tags=["test-demo"])

_DEMO_TAKEN_ACTIONS: dict[str, list[TakenAction]] = {}


@router.get("/chat/message/stream")
def test_chat_message_stream(
    request: Request,
    customer_id: str = Query(..., min_length=1),
    message: str = Query(..., min_length=1),
) -> StreamingResponse:
    async def generate():
        db_path = Path(request.app.state.db_path)
        policy_dir = Path(request.app.state.policy_dir)
        context: dict[str, Any] = {"customer_id": customer_id, "message": message}

        async for chunk in _run_demo_pipeline(context, db_path=db_path, policy_dir=policy_dir):
            yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")


async def _run_demo_pipeline(context: dict[str, Any], *, db_path: Path, policy_dir: Path):
    message = str(context["message"])
    customer_id = str(context["customer_id"])

    yield _event("intent", "running")
    classification = IntentClassifier().classify(message)
    emotion = _effective_emotion(message, classification.emotion)
    issue_queue = build_issue_queue(classification)
    intents = [issue.intent for issue in issue_queue]
    context["classification"] = classification.to_dict()
    context["classification"]["emotion"] = emotion
    context["intents"] = intents
    await asyncio.sleep(0.35)
    yield _event(
        "intent",
        "done",
        {
            "intents": intents,
            "emotion": emotion,
            "confidence": classification.intent_confidence,
            "queue": intents,
        },
    )

    yield _event("memory", "running")
    customer = lookup_customer(customer_id, db_path=db_path)
    memory = _memory_summary(customer_id, customer, db_path=db_path)
    context["customer"] = customer
    await asyncio.sleep(0.45)
    yield _event("memory", "done", memory)

    yield _event("policy", "running")
    policy_names = _policy_names_for_intents(intents)
    policy_results = []
    for policy_name in policy_names:
        policy = retrieve_policy(policy_name, query=message, policy_dir=policy_dir)
        if policy is not None:
            policy_results.append(
                {
                    "policy_name": policy["policy_name"],
                    "policy_id": policy["policy_id"],
                    "confidence": policy["relevance"]["score"],
                    "crag_path": policy["relevance"]["route"].upper(),
                }
            )
    context["policies"] = policy_results
    await asyncio.sleep(0.35)
    yield _event("policy", "done", {"policies": policy_results})

    yield _event("tools", "running")
    tool_results = await _run_tools(
        customer_id=customer_id,
        message=message,
        intents=intents,
        customer=customer,
        db_path=db_path,
    )
    context["tool_results"] = tool_results
    await asyncio.sleep(0.35)
    yield _event("tools", "done", {"tools": tool_results})

    yield _event("dag", "running")
    dag = _validate_demo_dag(tool_results)
    context["dag"] = dag
    await asyncio.sleep(0.35)
    yield _event("dag", "done", dag)

    yield _event("response", "running")
    response = _demo_response(context)
    effective_classification = classification.to_dict()
    effective_classification["emotion"] = emotion
    health_score = _health_score_for(effective_classification, intents)
    relationship_start = _relationship_start(customer)
    relationship_end = _relationship_end(customer, intents, effective_classification)
    await asyncio.sleep(0.25)
    yield _event(
        "response",
        "done",
        {
            "text": response,
            "health_score": health_score,
            "relationship_start": relationship_start,
            "relationship_end": relationship_end,
            "acknowledgment": generate_acknowledgment(issue_queue),
            "emotion": emotion,
            "empathy_mode": _empathy_mode_for(effective_classification, relationship_start),
        },
    )


async def _run_tools(
    *,
    customer_id: str,
    message: str,
    intents: list[str],
    customer: dict | None,
    db_path: Path,
) -> list[dict[str, Any]]:
    tools = []

    def add(name: str, ok: bool, summary: str, result: dict | list | None = None) -> None:
        tools.append({"tool_name": name, "ok": ok, "summary": summary, "result": result or {}})

    add("lookup_customer", customer is not None, _customer_summary(customer), customer)
    await asyncio.sleep(0.18)

    invoices = []
    if {"billing_dispute", "duplicate_charge", "refund_request"} & set(intents):
        invoices = get_invoice_history(customer_id, months=3, db_path=db_path)
        add("get_invoice_history", True, f"{len(invoices)} invoices loaded", {"invoice_count": len(invoices)})
        await asyncio.sleep(0.18)

    duplicate = None
    if {"duplicate_charge", "billing_dispute", "refund_request"} & set(intents):
        duplicate = check_duplicate_charge(customer_id, db_path=db_path)
        amount = duplicate.get("duplicate_amount")
        summary = f"duplicate found INR {amount:g}" if duplicate.get("duplicate_confirmed") and amount else "no confirmed duplicate"
        add("check_duplicate_charge", True, summary, duplicate)
        await asyncio.sleep(0.18)

    outage = None
    if {"service_outage", "router_issue"} & set(intents):
        location = str((customer or {}).get("location") or "")
        if location:
            outage = check_outage_status(location, customer_id=customer_id, db_path=db_path)
            duration = outage.get("duration_hours")
            if outage.get("verified") and duration:
                summary = f"verified outage {float(duration):g} hrs"
            else:
                summary = "no verified active outage"
            add("check_outage_status", True, summary, outage)
            await asyncio.sleep(0.18)

    if "router_issue" in intents:
        diagnostic = run_router_diagnostic(customer_id, db_path=db_path)
        add("run_router_diagnostic", True, str(diagnostic.get("recommendation") or "diagnostic complete"), diagnostic)
        await asyncio.sleep(0.18)

    if duplicate and duplicate.get("duplicate_confirmed"):
        amount = float(duplicate.get("duplicate_amount") or 0)
        candidate = ActionCandidate(
            action="apply_credit",
            customer_id=customer_id,
            target_id=str(duplicate.get("invoice_id") or ""),
            amount=amount,
            reason="duplicate_charge_credit",
        )
        replay = _check_action_replay(message, candidate, db_path=db_path)
        if replay.already_taken:
            add("apply_credit", True, _already_taken_summary(replay), {"mode": "already_taken", **replay.to_dict()})
        else:
            _remember_demo_action(
                TakenAction(
                    action="apply_credit",
                    customer_id=customer_id,
                    target_id=candidate.target_id,
                    amount=amount,
                    reason=candidate.reason,
                    source="demo_session",
                    summary=f"demo dry-run credit INR {amount:g} for invoice {duplicate.get('invoice_id')}",
                )
            )
            add(
                "apply_credit",
                True,
                f"demo dry-run INR {amount:g} approved",
                {
                    "amount": amount,
                    "invoice_id": duplicate.get("invoice_id"),
                    "mode": "demo_dry_run",
                    "replay_guard": replay.to_dict(),
                },
            )
    elif outage and outage.get("verified"):
        amount = 500 if float(outage.get("duration_hours") or 0) >= 6 else 100
        candidate = ActionCandidate(
            action="apply_credit",
            customer_id=customer_id,
            target_id=str(outage.get("outage_id") or outage.get("location") or ""),
            amount=amount,
            reason="service_outage_credit",
        )
        replay = _check_action_replay(message, candidate, db_path=db_path)
        if replay.already_taken:
            add("apply_credit", True, _already_taken_summary(replay), {"mode": "already_taken", **replay.to_dict()})
        else:
            _remember_demo_action(
                TakenAction(
                    action="apply_credit",
                    customer_id=customer_id,
                    target_id=candidate.target_id,
                    amount=amount,
                    reason=candidate.reason,
                    source="demo_session",
                    summary=f"demo dry-run service credit INR {amount:g}",
                )
            )
            add(
                "apply_credit",
                True,
                f"demo dry-run INR {amount:g} approved",
                {"amount": amount, "mode": "demo_dry_run", "replay_guard": replay.to_dict()},
            )

    return tools


def _validate_demo_dag(tool_results: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {tool["tool_name"]: tool.get("result") or {} for tool in tool_results}
    outage = by_name.get("check_outage_status")
    duplicate = by_name.get("check_duplicate_charge")

    if isinstance(outage, dict) and outage.get("verified"):
        context = {
            "check_outage_status": {
                "verified": bool(outage.get("verified")),
                "duration_hours": float(outage.get("duration_hours") or 0),
            },
            "get_invoice_history": {"credit_this_cycle": False},
        }
        try:
            validation = PolicyGraphValidator().authorize_action("service_credit_dag", "apply_credit", context)
            return {
                "dag_name": "service_credit_dag",
                "path": validation.path,
                "ujcs": validation.ujcs,
                "policy_status": "compliant" if validation.action == "apply_credit" else "needs_review",
                "action": validation.action,
            }
        except PolicyActionBlocked as exc:
            return {"dag_name": "service_credit_dag", "path": [], "ujcs": 0.0, "policy_status": "blocked", "reason": str(exc)}

    if isinstance(duplicate, dict) and duplicate.get("duplicate_confirmed"):
        context = {
            "check_duplicate_charge": {"duplicate_confirmed": True},
            "get_invoice_history": {"single_matching_invoice": True},
            "payment_age_days": 6,
            "duplicate_amount": float(duplicate.get("duplicate_amount") or 0),
        }
        validation = PolicyGraphValidator().run("duplicate_charge_refund_dag", context)
        return {
            "dag_name": "duplicate_charge_refund_dag",
            "path": validation.path,
            "ujcs": validation.ujcs,
            "policy_status": "compliant" if validation.ujcs >= 0.8 else "needs_review",
            "action": validation.action,
        }

    return {"dag_name": "none", "path": [], "ujcs": 0.0, "policy_status": "pending", "action": None}


def _memory_summary(customer_id: str, customer: dict | None, *, db_path: Path) -> dict[str, Any]:
    prior_context = []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT session_id, final_status, created_at, intents
            FROM conversations
            WHERE customer_id = ?
            ORDER BY datetime(created_at) DESC
            LIMIT 2
            """,
            (customer_id,),
        ).fetchall()
    for row in rows:
        prior_context.append(
            {
                "session_id": row["session_id"],
                "status": row["final_status"],
                "created_at": row["created_at"],
                "intents": _json_list(row["intents"]),
            }
        )

    if customer is None:
        return {"episodic": prior_context, "stable": [], "risk": "unknown"}

    stable = [
        f"{customer['plan_name']} · {customer['speed_mbps']} Mbps",
        str(customer["location"]),
        f"churn score {float(customer['churn_score']):.2f}",
    ]
    return {"episodic": prior_context, "stable": stable, "risk": customer["risk_level"]}


def _policy_names_for_intents(intents: list[str]) -> list[str]:
    names = []
    if {"billing_dispute", "duplicate_charge"} & set(intents):
        names.append("duplicate_charge_policy")
    if "service_outage" in intents:
        names.append("service_credit_policy")
    if "cancellation_intent" in intents:
        names.append("cancellation_policy")
    if "plan_change" in intents:
        names.append("plan_change_policy")
    if "technician_request" in intents or "router_issue" in intents:
        names.append("technician_visit_policy")
    if not names:
        names.append("payment_failure_policy")
    return names


def _demo_response(context: dict[str, Any]) -> str:
    customer = context.get("customer") or {}
    name = str(customer.get("name") or "there").split()[0]
    tools = {tool["tool_name"]: tool for tool in context.get("tool_results", [])}
    duplicate = (tools.get("check_duplicate_charge") or {}).get("result") or {}
    outage = (tools.get("check_outage_status") or {}).get("result") or {}
    intents = set(context.get("intents") or [])
    classification = context.get("classification") or {}
    emotion = str(classification.get("emotion") or "neutral")
    relationship_start = _relationship_start(customer)

    parts = [_opening_for_emotion(name, emotion, relationship_start, intents)]
    if duplicate.get("duplicate_confirmed"):
        parts.append(
            f"I found duplicate payment evidence on invoice {duplicate.get('invoice_id')} for INR {float(duplicate.get('duplicate_amount') or 0):g}."
        )
    if outage.get("verified"):
        parts.append(
            f"I also found a verified outage in {outage.get('location')} lasting {float(outage.get('duration_hours') or 0):g} hours."
        )
    if "cancellation_intent" in intents:
        parts.append("I will keep the cancellation request queued while resolving the billing and service issues first.")
    if "router_issue" in intents and not outage.get("verified"):
        parts.append("I ran the router diagnostic and will guide the next customer-side action from that result.")
    replay = _replay_result_from_tools(list(tools.values()))
    if replay:
        parts.append(f"That action has already been taken: {replay}. I will not run it again.")
    else:
        if emotion in {"angry", "frustrated"}:
            parts.append("I will not ask you to repeat details I already verified. I will only take the next action after the tool evidence and policy gate both allow it.")
        else:
            parts.append("The policy path has been checked and the next action is safe for the demo flow.")
    return " ".join(parts)


def _opening_for_emotion(name: str, emotion: str, relationship_start: int, intents: set[str]) -> str:
    if emotion == "angry":
        return (
            f"{name}, I hear how frustrating this is, and I am taking ownership of the next step. "
            "I will use the account evidence already available instead of making you start over."
        )
    if emotion == "frustrated" or relationship_start < 40:
        issue_summary = _issue_summary(intents)
        try:
            sequence = casa_empathy_sequence(
                relationship_start,
                customer_name=name,
                issue_summary=issue_summary,
            )
            return " ".join(step.customer_message for step in sequence.steps[:2])
        except ValueError:
            return (
                f"{name}, I can see this has taken more effort than it should. "
                "I will keep this focused and verify the account before acting."
            )
    return f"{name}, I checked your account and I can handle this step by step."


def _issue_summary(intents: set[str]) -> str:
    labels = {
        "duplicate_charge": "the duplicate charge",
        "refund_request": "the refund request",
        "service_outage": "the service outage",
        "cancellation_intent": "the cancellation request",
        "router_issue": "the router problem",
    }
    matched = [label for intent, label in labels.items() if intent in intents]
    if not matched:
        return "the current support issue"
    if len(matched) == 1:
        return matched[0]
    return ", ".join(matched[:-1]) + f", and {matched[-1]}"


def _check_action_replay(candidate_message: str, candidate: ActionCandidate, *, db_path: Path):
    taken = load_taken_actions(
        candidate.customer_id,
        db_path=db_path,
        extra_actions=list(_DEMO_TAKEN_ACTIONS.get(candidate.customer_id, [])),
    )
    return confirm_action_replay(candidate_message, candidate, taken)


def _remember_demo_action(action: TakenAction) -> None:
    actions = _DEMO_TAKEN_ACTIONS.setdefault(action.customer_id, [])
    if not any(
        existing.action == action.action
        and existing.target_id == action.target_id
        and existing.amount == action.amount
        for existing in actions
    ):
        actions.insert(0, action)


def _already_taken_summary(replay) -> str:
    match = replay.matched_action
    if match and match.summary:
        return f"already taken - {match.summary}"
    return f"already taken - {replay.requested_action}"


def _replay_result_from_tools(tool_events: list[dict[str, Any]]) -> str | None:
    for tool in tool_events:
        result = tool.get("result") or {}
        if isinstance(result, dict) and result.get("mode") == "already_taken":
            matched = result.get("matched_action") or {}
            if isinstance(matched, dict) and matched.get("summary"):
                return str(matched["summary"])
            requested = result.get("requested_action")
            return str(requested or tool.get("tool_name") or "requested action")
    return None


def _customer_summary(customer: dict | None) -> str:
    if customer is None:
        return "customer not found"
    return f"{customer['name']} · {customer['plan_name']} · {customer['risk_level']} risk"


def _effective_emotion(message: str, classifier_emotion: str) -> str:
    text = message.lower()
    anger_terms = ("angry", "ridiculous", "unacceptable", "furious", "useless", "terrible", "hate")
    if any(term in text for term in anger_terms):
        return "angry"
    return classifier_emotion


def _health_score_for(classification: dict[str, Any], intents: list[str]) -> int:
    score = 72
    if classification.get("emotion") in {"frustrated", "angry"}:
        score -= 18
    if "cancellation_intent" in intents:
        score -= 8
    if len(intents) > 2:
        score -= 6
    return max(18, min(92, score))


def _relationship_start(customer: dict | None) -> int:
    if not customer:
        return 50
    churn = float(customer.get("churn_score") or 0)
    return max(20, min(84, round(84 - (churn * 65))))


def _relationship_end(customer: dict | None, intents: list[str], classification: dict[str, Any] | None = None) -> int:
    start = _relationship_start(customer)
    emotion = str((classification or {}).get("emotion") or "neutral")
    if emotion == "angry":
        return min(92, start + 5)
    if emotion == "frustrated":
        return min(92, start + 8)
    lift = 14 if len(intents) > 1 else 8
    return min(92, start + lift)


def _empathy_mode_for(classification: dict[str, Any], relationship_start: int) -> str:
    emotion = str(classification.get("emotion") or "neutral")
    if emotion == "angry":
        return "ANGER_REPAIR"
    if emotion == "frustrated" or relationship_start < 40:
        return "CASA_AT_RISK"
    return "STANDARD"


def _event(step: str, status: str, result: dict[str, Any] | None = None) -> str:
    payload = {"step": step, "status": status, "result": result or {}}
    return f"data: {json.dumps(payload, ensure_ascii=True)}\n\n"


def _json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []
