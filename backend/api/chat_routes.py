import asyncio
import copy
import hashlib
import hmac
import json
import os
import re
import sqlite3
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from backend.agent import (
    ActionCandidate,
    IntentClassifier,
    LLMClient,
    build_issue_queue,
    confirm_action_replay,
    detect_handoff_triggers,
    generate_acknowledgment,
    generate_handoff_customer_message,
    insert_human_handoff_queue,
    load_taken_actions,
    log_handoff_event_to_audit,
)
from backend.agent.policy_graph import PolicyGraphValidator
from backend.agent.policy_store import ChromaPolicyStore
from backend.agent.health import (
    compute_health_score,
    knowledge_coverage_component,
    loop_penalty_component,
    missing_info_risk_component,
    sentiment_score_component,
)
from backend.tools import (
    apply_credit,
    check_duplicate_charge,
    check_outage_status,
    create_ticket,
    generate_handoff_summary,
    get_invoice_history,
    lookup_customer,
    retrieve_policy,
    run_router_diagnostic,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])
_ChatStateKey = tuple[str, str]
_CHAT_STATES: dict[_ChatStateKey, dict[str, Any]] = {}
_CHAT_STATE_LOCKS: dict[_ChatStateKey, tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}
_CHAT_STATE_LOCKS_GUARD = Lock()
_MEMORY_CANCELLATION_REQUESTS: dict[_ChatStateKey, dict[str, Any]] = {}


def _event(step: str, status: str, result: dict[str, Any] | None = None) -> str:
    payload = {"step": step, "status": status, "result": result or {}}
    return f"data: {json.dumps(payload, ensure_ascii=True)}\n\n"


@router.get("/message/stream")
def chat_message_stream(
    request: Request,
    customer_id: str = Query(..., min_length=1),
    session_id: str = Query("default", min_length=1),
    message: str = Query(..., min_length=1),
    temperature: float | None = Query(None, ge=0.0, le=2.0),
) -> StreamingResponse:
    normalized_session_id = _normalize_session_id(session_id)
    llm_temperature = (
        float(temperature)
        if isinstance(temperature, (int, float)) and 0.0 <= float(temperature) <= 2.0
        else None
    )

    async def generate_unlocked():
        turn_started = time.perf_counter()
        stage_timings: dict[str, float] = {}
        db_path = Path(request.app.state.db_path)
        policy_dir = Path(request.app.state.policy_dir)
        policy_store: ChromaPolicyStore | None = getattr(
            request.app.state, "policy_store", None)

        llm = _safe_llm_client()
        classifier_llm = _safe_classifier_client()
        state_lock = _chat_state_lock(customer_id, normalized_session_id)
        async with state_lock:
            chat_state = await asyncio.to_thread(
                _load_session_state, customer_id, normalized_session_id, db_path
            )
            chat_state["turn_count"] = int(chat_state.get("turn_count", 0)) + 1
            backend_intent = _normalize_chat_intent(message, chat_state)

        queue = asyncio.Queue()

        # Each pipeline task MUST emit exactly one "running" and one "done" event,
        # even on failure. The drain loop below reads a fixed number of events;
        # if a task raised before its "done" the loop would block forever, hanging
        # the SSE connection. The try/except guarantees the "done" is always sent
        # so a tool/DB error degrades gracefully instead of deadlocking the stream.
        async def _run_intent():
            stage_started = time.perf_counter()
            await queue.put(_event("intent", "running"))
            try:
                classification = await asyncio.to_thread(_classify_message, message, classifier_llm)
                emotion = _effective_emotion(message, classification.emotion)
                issue_queue = build_issue_queue(classification)
                res_intents = [issue.intent for issue in issue_queue]
                cancellation_signalled = (
                    backend_intent in {"cancellation_request", "cancellation_confirmation"}
                    or _has_cancellation_signal(message)
                    or chat_state.get("active_flow") == "cancellation"
                )
                if "cancellation_intent" in res_intents and not cancellation_signalled:
                    # The classifier over-detected cancellation on a message that
                    # never mentions it; don't enter the destructive flow.
                    res_intents = [i for i in res_intents if i != "cancellation_intent"]
                    issue_queue = build_issue_queue(res_intents)
                if backend_intent in {"cancellation_request", "cancellation_confirmation"} and "cancellation_intent" not in res_intents:
                    res_intents.append("cancellation_intent")
                    issue_queue = build_issue_queue(res_intents)
                if _has_outage_signal(message) and "service_outage" not in res_intents:
                    res_intents.append("service_outage")
                    issue_queue = build_issue_queue(res_intents)
                if _has_payment_problem_signal(message) and "billing_dispute" not in res_intents:
                    res_intents.append("billing_dispute")
                    issue_queue = build_issue_queue(res_intents)
                if _has_refund_signal(message) and "refund_request" not in res_intents:
                    res_intents.append("refund_request")
                    issue_queue = build_issue_queue(res_intents)
                if (
                    "duplicate_charge" in res_intents
                    and _has_human_or_fix_signal(message)
                    and "refund_request" not in res_intents
                ):
                    res_intents.append("refund_request")
                    issue_queue = build_issue_queue(res_intents)
                payload = {
                    "intents": res_intents,
                    "latest_intent": backend_intent or classification.primary_intent,
                    "emotion": emotion,
                    "confidence": classification.intent_confidence,
                    "queue": [issue.intent for issue in issue_queue],
                }
                await queue.put(_event("intent", "done", payload))
                stage_timings["intent"] = (time.perf_counter() - stage_started) * 1000
                return res_intents, emotion, issue_queue, classification.intent_confidence
            except Exception:
                emotion = _effective_emotion(message, "neutral")
                fallback_intents = ["general_query"]
                if backend_intent in {"cancellation_request", "cancellation_confirmation"}:
                    fallback_intents = ["cancellation_intent"]
                issue_queue = build_issue_queue(fallback_intents)
                await queue.put(_event("intent", "done", {
                    "intents": fallback_intents,
                    "latest_intent": backend_intent or "general_query",
                    "emotion": emotion,
                    "confidence": 0.0,
                    "queue": [issue.intent for issue in issue_queue],
                }))
                stage_timings["intent"] = (time.perf_counter() - stage_started) * 1000
                return fallback_intents, emotion, issue_queue, 0.0

        async def _run_memory():
            stage_started = time.perf_counter()
            await queue.put(_event("memory", "running"))
            try:
                res_customer = await asyncio.to_thread(lookup_customer, customer_id, db_path=db_path)
            except Exception:
                res_customer = None
            await queue.put(_event("memory", "done", res_customer or {}))
            stage_timings["memory"] = (time.perf_counter() - stage_started) * 1000
            return res_customer

        async def _run_policy():
            stage_started = time.perf_counter()
            await queue.put(_event("policy", "running"))
            res_policy_results = []
            if policy_store is not None:
                try:
                    search_results = await asyncio.to_thread(
                        policy_store.query, message, top_k=3
                    )
                    metadatas = search_results.get("metadatas", [[]])[0]
                    unique_policy_ids = list(
                        {meta["policy_id"]: meta for meta in metadatas if "policy_id" in meta}.values())

                    async def _fetch_policy(meta):
                        return await asyncio.to_thread(
                            retrieve_policy,
                            policy_name=meta["policy_id"],
                            query=message,
                            policy_dir=policy_dir,
                            llm_client=None,
                        )

                    policies = await asyncio.gather(*[_fetch_policy(meta) for meta in unique_policy_ids])
                    for policy in policies:
                        if policy:
                            res_policy_results.append({
                                "policy_name": policy["policy_name"],
                                "policy_id": policy["policy_id"],
                                "confidence": policy["relevance"]["score"],
                                "crag_path": policy["relevance"]["route"].upper(),
                            })
                except Exception:
                    res_policy_results = []
            await queue.put(_event("policy", "done", {"policies": res_policy_results}))
            stage_timings["policy"] = (time.perf_counter() - stage_started) * 1000
            return res_policy_results

        # Start background tasks
        intent_task = asyncio.create_task(_run_intent())
        memory_task = asyncio.create_task(_run_memory())
        policy_task = asyncio.create_task(_run_policy())

        # Yield events as they come in until all 3 "done" events have been yielded (i.e. 6 events total)
        for _ in range(6):
            yield await queue.get()

        # Await the actual results to use them downstream
        (intents, emotion, issue_queue, intent_confidence), customer, policy_results = await asyncio.gather(
            intent_task, memory_task, policy_task
        )

        # 4. Tools (Dynamic Execution could go here, but for now we run the basics based on intent)
        tools_started = time.perf_counter()
        yield _event("tools", "running")
        tool_results = []
        dag_result: dict[str, Any] | None = None
        dag_forced_handoff_reason: str | None = None
        # A backend/DB fault while calling a tool must not kill the SSE stream:
        # keep whatever evidence was gathered and let the response stage degrade
        # gracefully rather than leaving the connection without a terminal event.
        try:
            if {"billing_dispute", "duplicate_charge"} & set(intents):
                invoices = await asyncio.to_thread(get_invoice_history, customer_id, months=12, db_path=db_path)
                tool_results.append({"tool_name": "get_invoice_history", "ok": True,
                                    "summary": f"{len(invoices)} invoices loaded", "result": {"invoices": invoices}})

                duplicate = await asyncio.to_thread(check_duplicate_charge, customer_id, db_path=db_path)
                duplicate_summary = _duplicate_summary(duplicate)
                tool_results.append({"tool_name": "check_duplicate_charge",
                                    "ok": True, "summary": duplicate_summary, "result": duplicate})
                if duplicate.get("duplicate_confirmed"):
                    replay = await asyncio.to_thread(_check_credit_replay, message, customer_id, duplicate, db_path)
                    tool_results.append({
                        "tool_name": "apply_credit_guard",
                        "ok": True,
                        "summary": _replay_summary(replay),
                        "result": replay.to_dict(),
                    })
                    # duplicate_charge_refund_dag is a pure tool-derived decision tree
                    # (no LLM judgment needed), so it is safe to traverse and act on
                    # synchronously within this turn -- unlike free-form replies, which
                    # go through the trust-score/CoVe gate below. Gated on an explicit
                    # billing/duplicate-charge intent (not bare refund_request): the demo
                    # customer's seed data always has a duplicate charge on file, so a
                    # refund_request about something unrelated (e.g. an outage credit)
                    # would otherwise spuriously open a duplicate-charge ticket/handoff.
                    if {"billing_dispute", "duplicate_charge"} & set(intents):
                        try:
                            dag_context = _duplicate_charge_dag_context(duplicate)
                            validation = PolicyGraphValidator().run("duplicate_charge_refund_dag", dag_context)
                            dag_result = {
                                "dag_name": validation.policy_name,
                                "policy_status": "compliant",
                                "action": validation.action or "none",
                                "path": validation.path,
                                "ujcs": validation.ujcs,
                            }
                            if validation.action in {"create_ticket", "handoff_human"} and not replay.already_taken:
                                # A human reviewer needs a ticket to work from whether the
                                # DAG auto-clears it or routes to manual review -- only the
                                # priority/reason differs. create_ticket() re-validates that
                                # policy_context resolves to exactly "create_ticket", so the
                                # auto-clear path can pass it through for that authorization
                                # check; the manual-review ticket is a bookkeeping side effect
                                # of the handoff, not itself a DAG-authorized action, so it is
                                # created without a policy_name/policy_context pair.
                                escalated = validation.action == "handoff_human"
                                ticket = await asyncio.to_thread(
                                    create_ticket,
                                    customer_id,
                                    "duplicate_charge_refund_review",
                                    priority="high" if escalated else "medium",
                                    status="escalated" if escalated else "open",
                                    policy_name=None if escalated else "duplicate_charge_refund_dag",
                                    policy_context=None if escalated else dag_context,
                                    db_path=db_path,
                                )
                                tool_results.append({
                                    "tool_name": "create_ticket",
                                    "ok": True,
                                    "summary": f"ticket {ticket.get('ticket_id')} opened for duplicate-charge review",
                                    "result": ticket,
                                })
                                if escalated:
                                    dag_forced_handoff_reason = str(
                                        (validation.action_args or {}).get("reason")
                                        or "duplicate_charge_refund_manual_review"
                                    )
                        except Exception:
                            dag_result = None

            if {"service_outage", "router_issue"} & set(intents):
                if customer and customer.get("location"):
                    try:
                        outage = await asyncio.to_thread(
                            check_outage_status,
                            customer["location"],
                            customer_id=customer_id,
                            db_path=db_path,
                        )
                        tool_results.append({
                            "tool_name": "check_outage_status",
                            "ok": True,
                            "summary": _outage_summary(outage),
                            "result": outage,
                        })
                    except Exception as exc:  # noqa: BLE001 - live agent must degrade to handoff.
                        outage = {
                            "verified": False,
                            "error": str(exc),
                            "location": customer.get("location"),
                        }
                        tool_results.append({
                            "tool_name": "check_outage_status",
                            "ok": False,
                            "summary": "outage lookup failed; escalating with no outage claim",
                            "result": outage,
                        })
                        dag_forced_handoff_reason = "outage_tool_failure"
                    if _wants_service_credit(message, intents):
                        service_invoices = _tool_result(tool_results, "get_invoice_history")
                        if service_invoices is None:
                            invoices = await asyncio.to_thread(get_invoice_history, customer_id, months=12, db_path=db_path)
                            service_invoices = {"invoices": invoices}
                            tool_results.append({
                                "tool_name": "get_invoice_history",
                                "ok": True,
                                "summary": f"{len(invoices)} invoices loaded",
                                "result": service_invoices,
                            })
                        service_context = await asyncio.to_thread(
                            _service_credit_dag_context,
                            outage,
                            customer_id,
                            db_path,
                        )
                        try:
                            validation = PolicyGraphValidator().run("service_credit_dag", service_context)
                            dag_result = {
                                "dag_name": validation.policy_name,
                                "policy_status": "compliant",
                                "action": validation.action or "none",
                                "path": validation.path,
                                "ujcs": validation.ujcs,
                            }
                            if (
                                validation.action == "apply_credit"
                                and (validation.action_args or {}).get("credit_type") == "service_outage"
                            ):
                                credit = await asyncio.to_thread(
                                    apply_credit,
                                    customer_id,
                                    _service_credit_amount(service_context),
                                    _service_credit_reason(outage),
                                    policy_context=service_context,
                                    applied_to_invoice=_latest_invoice_id(service_invoices),
                                    db_path=db_path,
                                )
                                tool_results.append({
                                    "tool_name": "apply_credit",
                                    "ok": True,
                                    "summary": f"credit {credit.get('credit_id')} applied for verified outage",
                                    "result": credit,
                                })
                            elif validation.action == "handoff_human":
                                dag_forced_handoff_reason = str(
                                    (validation.action_args or {}).get("reason")
                                    or "service_credit_manual_review"
                                )
                        except Exception:
                            dag_result = None

            if (
                ("refund_request" in intents or "plan_change" in intents)
                and _tool_result(tool_results, "get_invoice_history") is None
            ):
                invoices = await asyncio.to_thread(get_invoice_history, customer_id, months=12, db_path=db_path)
                tool_results.append({
                    "tool_name": "get_invoice_history",
                    "ok": True,
                    "summary": f"{len(invoices)} invoices loaded",
                    "result": {"invoices": invoices},
                })

            if _requires_refund_exception_review(message, intents):
                refund_invoices = _tool_result(tool_results, "get_invoice_history")
                if refund_invoices is None:
                    invoices = await asyncio.to_thread(get_invoice_history, customer_id, months=12, db_path=db_path)
                    refund_invoices = {"invoices": invoices}
                    tool_results.append({
                        "tool_name": "get_invoice_history",
                        "ok": True,
                        "summary": f"{len(invoices)} invoices loaded",
                        "result": refund_invoices,
                    })
                try:
                    refund_context = _refund_exception_dag_context(
                        message,
                        customer,
                        refund_invoices,
                    )
                    validation = PolicyGraphValidator().run("refund_exception_dag", refund_context)
                    dag_result = {
                        "dag_name": validation.policy_name,
                        "policy_status": "compliant",
                        "action": validation.action or "none",
                        "path": validation.path,
                        "ujcs": validation.ujcs,
                    }
                    if validation.action == "create_ticket":
                        ticket = await asyncio.to_thread(
                            create_ticket,
                            customer_id,
                            "refund_review",
                            priority="high",
                            status="open",
                            policy_name="refund_exception_dag",
                            policy_context=refund_context,
                            db_path=db_path,
                        )
                        tool_results.append({
                            "tool_name": "create_ticket",
                            "ok": True,
                            "summary": f"refund review ticket {ticket.get('ticket_id')} opened",
                            "result": ticket,
                        })
                    elif validation.action == "handoff_human":
                        dag_forced_handoff_reason = str(
                            (validation.action_args or {}).get("reason")
                            or "refund_exception_manual_review"
                        )
                except Exception:
                    pass

            if "router_issue" in intents or _needs_router_diagnostic(message, chat_state, intents):
                diagnostic = await asyncio.to_thread(run_router_diagnostic, customer_id, db_path=db_path)
                tool_results.append({"tool_name": "run_router_diagnostic", "ok": True, "summary": str(
                    diagnostic.get("recommendation") or "diagnostic complete"), "result": diagnostic})
                if _needs_router_diagnostic(message, chat_state, intents):
                    dag_forced_handoff_reason = dag_forced_handoff_reason or "repeated_outage_loop"

            if backend_intent == "cancellation_confirmation":
                cancellation_request = await asyncio.to_thread(
                    _create_cancellation_request,
                    customer_id,
                    message,
                    db_path,
                    normalized_session_id,
                )
                tool_results.append({
                    "tool_name": "create_cancellation_request",
                    "ok": True,
                    "summary": _cancellation_request_summary(cancellation_request),
                    "result": cancellation_request,
                })
                _mark_cancellation_completed(chat_state, cancellation_request)
            elif "cancellation_intent" in intents:
                subscription = await asyncio.to_thread(_get_subscription_status, customer_id, db_path)
                cancellation_policy = _get_cancellation_policy(
                    customer, subscription)
                pending_credits = await asyncio.to_thread(_check_pending_credits, customer_id, customer, db_path)
                retention_offer = _build_retention_offer(
                    customer, subscription, cancellation_policy)
                tool_results.extend([
                    {
                        "tool_name": "get_subscription_status",
                        "ok": True,
                        "summary": _subscription_summary(subscription),
                        "result": subscription,
                    },
                    {
                        "tool_name": "get_cancellation_policy",
                        "ok": True,
                        "summary": _cancellation_policy_summary(cancellation_policy),
                        "result": cancellation_policy,
                    },
                    {
                        "tool_name": "check_pending_credits",
                        "ok": True,
                        "summary": _pending_credit_summary(pending_credits),
                        "result": pending_credits,
                    },
                ])
                if retention_offer.get("offer_available"):
                    tool_results.append({
                        "tool_name": "build_retention_offer",
                        "ok": True,
                        "summary": _retention_offer_summary(retention_offer),
                        "result": retention_offer,
                    })
                try:
                    retention_context = _cancellation_retention_dag_context(
                        customer,
                        subscription,
                        pending_credits,
                        intents,
                    )
                    validation = PolicyGraphValidator().run(
                        "cancellation_retention_dag", retention_context)
                    if dag_result is None or dag_result.get("dag_name") == "dynamic_agent_path":
                        dag_result = {
                            "dag_name": validation.policy_name,
                            "policy_status": "compliant",
                            "action": validation.action or "none",
                            "path": validation.path,
                            "ujcs": validation.ujcs,
                        }
                    if validation.action == "create_ticket":
                        ticket = await asyncio.to_thread(
                            create_ticket,
                            customer_id,
                            "retention_unresolved_issue",
                            priority="high",
                            status="escalated",
                            policy_name="cancellation_retention_dag",
                            policy_context=retention_context,
                            db_path=db_path,
                        )
                        tool_results.append({
                            "tool_name": "create_ticket",
                            "ok": True,
                            "summary": f"retention ticket {ticket.get('ticket_id')} opened",
                            "result": ticket,
                        })
                        dag_forced_handoff_reason = "cancellation_retention_required"
                    elif validation.action == "handoff_human":
                        dag_forced_handoff_reason = str(
                            (validation.action_args or {}).get("reason")
                            or "cancellation_retention_required"
                        )
                except Exception:
                    pass
                _mark_cancellation_pending(chat_state, intents, pending_credits)
        except Exception:
            tool_results.append({
                "tool_name": "tool_execution",
                "ok": False,
                "summary": "a backend tool could not complete; continuing with available evidence",
                "result": {},
            })

        # Bind every successful tool result to a tamper-evident evidence receipt.
        receipts = _attach_receipts(message, tool_results)
        yield _event("tools", "done", {"tools": tool_results, "receipts": receipts})
        stage_timings["tools"] = (time.perf_counter() - tools_started) * 1000

        # 5. DAG (Policy Validation)
        dag_started = time.perf_counter()
        yield _event("dag", "running")
        # Intents with a pure tool-derived decision tree (currently: duplicate-charge
        # refund review) run the real PolicyGraphValidator above and land in dag_result.
        # Everything else still falls back to this generic status until the ReAct loop
        # drives DAG selection for every intent.
        dag = dag_result or {
            "dag_name": "dynamic_agent_path",
            "policy_status": "compliant",
            "action": "none",
            "path": intents,
            "ujcs": 0.86 if tool_results else 0.0,
        }
        yield _event("dag", "done", dag)
        stage_timings["dag"] = (time.perf_counter() - dag_started) * 1000

        # 6. Response
        response_started = time.perf_counter()
        yield _event("response", "running")

        # Trust metadata (Cleanlab/tau2-bench). Template-driven branches are
        # inherently grounded, so they carry high deterministic trust; only the
        # free-form LLM path is scored and (if needed) revised or escalated.
        trust_score = 0.95
        trust_action = "deterministic"
        trust_issues: list[str] = []
        force_handoff = False

        async def _grounded_reply(base_prompt: str):
            """Generate -> trust-score -> revise once -> escalate to a safe floor."""
            if llm is None:
                return _evidence_response(customer, intents, emotion, tool_results), 0.9, "deterministic", []
            try:
                # gemini-2.5-flash spends an unpredictable share of
                # max_output_tokens on internal reasoning before emitting
                # visible text -- the default 1024 budget can get starved
                # mid-sentence, producing a draft that LOOKS complete enough to
                # pass the trust check but is actually truncated.
                draft = (await asyncio.to_thread(
                    llm.generate, base_prompt, response_mime_type="text/plain",
                    temperature=0.7 if llm_temperature is None else llm_temperature,
                    max_output_tokens=2048)).strip()
            except Exception:
                return _evidence_response(customer, intents, emotion, tool_results), 0.9, "fallback", []
            score, issues = await asyncio.to_thread(_action_trust_score, draft, tool_results, classifier_llm)
            if score >= _TRUST_THRESHOLD:
                return draft, score, "proceed", issues
            try:
                revised = (await asyncio.to_thread(
                    llm.generate, _revision_prompt(base_prompt, draft, issues),
                    response_mime_type="text/plain", temperature=0.4,
                    max_output_tokens=2048)).strip()
                rscore, rissues = await asyncio.to_thread(_action_trust_score, revised, tool_results, classifier_llm)
            except Exception:
                revised, rscore, rissues = "", 0.0, issues
            if revised and rscore >= _TRUST_THRESHOLD:
                return revised, rscore, "revised", rissues
            # Still untrustworthy: serve the safe deterministic answer AND loop in a human.
            safe = _evidence_response(customer, intents, emotion, tool_results)
            return safe, max(score, rscore), "escalated", (rissues or issues)

        if backend_intent == "greeting" and chat_state.get("active_flow") == "cancellation":
            final_text = _active_flow_greeting_response(customer, chat_state)
        elif backend_intent == "abort_cancellation":
            await asyncio.to_thread(
                _abort_cancellation, chat_state, customer_id, normalized_session_id, db_path
            )
            name = _first_name(customer)
            final_text = f"Okay {name}, I have stopped the cancellation process. Your service will remain active. Let me know if there's anything else I can help with!"
        elif backend_intent == "cancellation_confirmation":
            final_text = _cancellation_confirmation_response(
                customer, tool_results)
        elif "cancellation_intent" in intents:
            # A cancellation can arrive alongside billing/outage issues in one
            # message (the flagship multi-issue case). The options response now
            # folds in any confirmed findings so they are not silently dropped.
            final_text = _cancellation_options_response(customer, tool_results)
        else:
            current_findings = _finding_keys(tool_results)
            new_findings = [key for key in current_findings if key not in chat_state.get(
                "presented_findings", [])]
            is_repeat = bool(current_findings) and not new_findings

            if is_repeat:
                # The customer is re-raising something we already covered this
                # session. Say it has already been handled and move forward
                # instead of re-deriving the same evidence.
                final_text = _already_addressed_response(
                    customer, intents, emotion, tool_results)
            else:
                base_prompt = _build_response_prompt(
                    message, customer, tool_results, policy_results, chat_state)
                final_text, trust_score, trust_action, trust_issues = await _grounded_reply(base_prompt)
                if trust_action == "escalated":
                    force_handoff = True

            _remember_findings(chat_state, current_findings)

        # Feature: warm human handoff. When the conversation is going badly
        # (anger persists, health collapses, low model confidence after revision,
        # or an explicit ask), surface an escalation with a ready-to-read context
        # summary for the human agent instead of letting the bot grind on.
        #
        # Everything from here through the telemetry write is wrapped: an
        # exception anywhere in this tail (handoff building, localization,
        # session-state save) must not skip the telemetry row or leave the
        # stream without a terminal event -- it would otherwise silently
        # undercount exactly the turns most worth measuring (the failing ones)
        # and leave the frontend stuck waiting past its watchdog.
        health_score = _health_score_for(
            emotion,
            intents,
            customer=customer,
            message=message,
            chat_state=chat_state,
            tool_results=tool_results,
            policy_results=policy_results,
            intent_confidence=intent_confidence,
        )
        handoff = None
        handoff_summary = None
        records_synced = False
        try:
            handoff = _maybe_build_handoff(
                customer, message, emotion, intents, tool_results, health_score, chat_state,
                force=force_handoff or bool(dag_forced_handoff_reason),
                force_reason=dag_forced_handoff_reason
                or ("low model confidence after self-revision" if force_handoff else None))
            if handoff and handoff.get("should_handoff"):
                try:
                    await asyncio.to_thread(
                        _sync_live_conversation_records,
                        customer_id=customer_id,
                        session_id=normalized_session_id,
                        message=message,
                        final_text=final_text,
                        intents=intents,
                        tool_results=tool_results,
                        policy_results=policy_results,
                        dag=dag,
                        health_score=health_score,
                        relationship_start=_relationship_start(customer),
                        relationship_end=_relationship_end(customer, intents, emotion),
                        handoff_required=True,
                        db_path=db_path,
                    )
                    records_synced = True
                    handoff_summary = await asyncio.to_thread(
                        generate_handoff_summary,
                        normalized_session_id,
                        handoff_reason=handoff.get("reason") or "conversation health at risk",
                        db_path=db_path,
                    )
                    if handoff_summary:
                        if isinstance(handoff_summary.get("context_card"), dict):
                            handoff["context_card"] = handoff_summary["context_card"]
                        tool_results.append({
                            "tool_name": "generate_handoff_summary",
                            "ok": True,
                            "summary": f"handoff summary {handoff_summary.get('handoff_summary_id')} generated",
                            "result": handoff_summary,
                        })
                except Exception:
                    handoff_summary = None
                # Without this, the customer sees a "connecting you to a
                # specialist" message but no row is ever written to
                # human_handoff_queue -- the agent-desk console has nothing to
                # show for a real live escalation. Never let this break the
                # customer-facing reply if the audit-side write fails.
                try:
                    await asyncio.to_thread(
                        _record_handoff_to_queue,
                        customer_id=customer_id,
                        session_id=normalized_session_id,
                        handoff=handoff,
                        db_path=db_path,
                    )
                except Exception:
                    pass
            if (handoff and handoff.get("should_handoff")
                    and "explicit_request" in handoff.get("trigger_codes", [])
                    and not tool_results):
                # An explicit "get me a human" with no other findings to report:
                # the generic clarifying filler would contradict the handoff banner
                # (asking "how can I help" while also announcing an escalation), so
                # lead with the handoff acknowledgment instead.
                final_text = handoff["customer_message"]

            # Feature: multi-language. Localize the final reply into the customer's
            # preferred language (English replies are left untouched). Runs AFTER the
            # overclaim/evidence guards so safety checks operate on the English text.
            final_text, response_language_code = await asyncio.to_thread(
                _localize_response, final_text, customer, llm)

            # Update chat history
            chat_history = chat_state.setdefault("history", [])
            chat_history.append({"role": "user", "content": message})
            chat_history.append({"role": "assistant", "content": final_text})
            chat_state["history"] = chat_history[-10:]
            if not records_synced:
                await asyncio.to_thread(
                    _sync_live_conversation_records,
                    customer_id=customer_id,
                    session_id=normalized_session_id,
                    message=message,
                    final_text=final_text,
                    intents=intents,
                    tool_results=tool_results,
                    policy_results=policy_results,
                    dag=dag,
                    health_score=health_score,
                    relationship_start=_relationship_start(customer),
                    relationship_end=_relationship_end(customer, intents, emotion),
                    handoff_required=bool(handoff and handoff.get("should_handoff")),
                    db_path=db_path,
                )

            # Persist the updated session so multi-turn context survives a restart.
            async with state_lock:
                state_snapshot = _snapshot_chat_state(chat_state)
                await asyncio.to_thread(
                    _save_session_state, customer_id, normalized_session_id, state_snapshot, db_path
                )
        except Exception:
            response_language_code = "en"
            async with state_lock:
                state_snapshot = _snapshot_chat_state(chat_state)

        stage_timings["response"] = (time.perf_counter() - response_started) * 1000
        try:
            await asyncio.to_thread(
                _record_turn_telemetry,
                db_path=db_path,
                customer_id=customer_id,
                session_id=normalized_session_id,
                turn_count=int(chat_state.get("turn_count", 0)),
                latency_ms=(time.perf_counter() - turn_started) * 1000,
                input_tokens=_rough_token_count(message),
                output_tokens=_rough_token_count(final_text),
                stage_breakdown=stage_timings,
            )
        except Exception:
            pass

        yield _event("response", "done", {
            "text": final_text,
            "session_id": normalized_session_id,
            "health_score": health_score,
            "relationship_start": _relationship_start(customer),
            "relationship_end": _relationship_end(customer, intents, emotion),
            "acknowledgment": generate_acknowledgment(issue_queue),
            "emotion": emotion,
            "empathy_mode": _empathy_mode_for(emotion, _relationship_start(customer)),
            "language": _language_name(response_language_code),
            "handoff": handoff,
            "trust": {
                "score": trust_score,
                "action": trust_action,
                "issues": trust_issues,
                "threshold": _TRUST_THRESHOLD,
            },
            "verified_claims": _verified_claims(tool_results),
            "conversation_state": state_snapshot,
            "llm_temperature": llm_temperature,
            "handoff_summary": handoff_summary,
        })

    return StreamingResponse(generate_unlocked(), media_type="text/event-stream")


@router.get("/session/messages")
def chat_session_messages(
    request: Request,
    customer_id: str = Query(..., min_length=1),
    session_id: str = Query("default", min_length=1),
) -> dict[str, Any]:
    """Return persisted chat history for a customer/session pair."""
    db_path = Path(request.app.state.db_path)
    state = _load_session_state(customer_id, session_id, db_path)
    history = state.get("history") if isinstance(state, dict) else []
    messages = history if isinstance(history, list) else []
    messages = [*messages, *_load_proactive_customer_messages(customer_id, db_path)]
    return {
        "customer_id": customer_id,
        "session_id": _normalize_session_id(session_id),
        "messages": messages,
    }


def append_human_reply_to_session(
    *,
    customer_id: str,
    session_id: str,
    message: str,
    agent_name: str,
    db_path: Path,
) -> dict[str, Any]:
    normalized_customer_id = " ".join(customer_id.strip().split())
    normalized_session_id = _normalize_session_id(session_id)
    normalized_message = " ".join(message.strip().split())
    normalized_agent = " ".join(agent_name.strip().split()) or "Human specialist"
    if not normalized_customer_id:
        raise ValueError("customer_id must not be empty")
    if not normalized_message:
        raise ValueError("message must not be empty")

    state = _load_session_state(normalized_customer_id, normalized_session_id, db_path)
    history = state.setdefault("history", [])
    if not isinstance(history, list):
        history = []
    reply = {
        "role": "human_agent",
        "agent_name": normalized_agent,
        "content": normalized_message,
        "timestamp": _now_iso(),
    }
    for existing in history:
        if (
            isinstance(existing, dict)
            and existing.get("role") == "human_agent"
            and existing.get("agent_name") == normalized_agent
            and " ".join(str(existing.get("content", "")).strip().split()) == normalized_message
        ):
            return existing
    history.append(reply)
    state["history"] = history[-20:]
    _save_session_state(normalized_customer_id, normalized_session_id, state, db_path)
    return reply


def _load_proactive_customer_messages(customer_id: str, db_path: Path) -> list[dict[str, Any]]:
    normalized_customer_id = " ".join(customer_id.strip().split())
    if not normalized_customer_id:
        return []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT session_id, messages
            FROM conversations
            WHERE customer_id = ?
              AND session_id LIKE 'proactive-%'
            ORDER BY datetime(created_at) ASC, session_id ASC
            """,
            (normalized_customer_id,),
        ).fetchall()
    proactive_messages: list[dict[str, Any]] = []
    for row in rows:
        try:
            messages = json.loads(row["messages"])
        except json.JSONDecodeError:
            continue
        if not isinstance(messages, list):
            continue
        for message in messages:
            if not isinstance(message, dict) or not message.get("proactive"):
                continue
            proactive_messages.append({
                **message,
                "role": "agent",
                "source_session_id": row["session_id"],
            })
    return proactive_messages


def _record_turn_telemetry(
    *,
    db_path: Path,
    customer_id: str,
    session_id: str,
    turn_count: int,
    latency_ms: float,
    input_tokens: int,
    output_tokens: int,
    stage_breakdown: dict[str, float],
) -> None:
    telemetry_id = hashlib.sha256(
        f"{customer_id}|{session_id}|{turn_count}|{time.time_ns()}".encode("utf-8")
    ).hexdigest()[:16]
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO telemetry (
                telemetry_id,
                session_id,
                customer_id,
                turn_count,
                latency_ms,
                input_tokens,
                output_tokens,
                total_tokens,
                stage_breakdown,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telemetry_id,
                session_id,
                customer_id,
                max(0, turn_count),
                max(0.0, latency_ms),
                input_tokens,
                output_tokens,
                input_tokens + output_tokens,
                json.dumps({
                    stage: round(max(0.0, duration), 1)
                    for stage, duration in stage_breakdown.items()
                }, ensure_ascii=True),
                _now_iso(),
            ),
        )


def _rough_token_count(text: str) -> int:
    normalized = " ".join(str(text).split())
    if not normalized:
        return 0
    return max(1, round(len(normalized) / 4))


def _safe_llm_client() -> LLMClient | None:
    try:
        return LLMClient()
    except Exception:
        return None


def _safe_classifier_client() -> LLMClient | None:
    """Lightweight secondary model for structured classification.

    The README's two-model split puts classification on the lite model; it is
    cheaper and, with JSON mime forced in IntentClassifier, more reliable for
    structured extraction than the heavy thinking model.
    """
    try:
        return LLMClient("secondary")
    except Exception:
        return None


def _classify_message(message: str, llm: LLMClient | None):
    if llm is not None:
        try:
            return IntentClassifier(llm_client=llm).classify(message)
        except Exception:
            pass
    return IntentClassifier().classify(message)


def _effective_emotion(message: str, classifier_emotion: str) -> str:
    text = message.lower()
    anger_terms = ("angry", "ridiculous", "unacceptable",
                   "furious", "useless", "terrible", "hate")
    repeat_complaint_terms = ("third time", "again and again", "keep calling",
                              "every time i call", "still waiting", "how many times")
    if any(term in text for term in anger_terms):
        return "angry"
    if any(term in text for term in repeat_complaint_terms):
        return "angry"
    # Shouting (long runs of caps) and stacked exclamation marks are strong
    # anger signals that literal keyword matching on lowercased text misses.
    letters = [c for c in message if c.isalpha()]
    if len(letters) >= 8 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7:
        return "angry"
    if message.count("!") >= 2:
        return "angry"
    return classifier_emotion


def _duplicate_summary(duplicate: dict[str, Any]) -> str:
    amount = duplicate.get("duplicate_amount")
    if duplicate.get("duplicate_confirmed") and amount:
        return f"duplicate found INR {float(amount):g}"
    return "no confirmed duplicate"


def _duplicate_charge_dag_context(duplicate: dict[str, Any]) -> dict[str, Any]:
    """Map check_duplicate_charge's result onto duplicate_charge_refund_dag's node fields."""
    timestamps = duplicate.get("payment_timestamps") or []
    if timestamps:
        reference_date = date.fromisoformat(os.environ.get("RESOLVEFLOW_NOW", "2026-06-01")[:10])
        earliest_date = min(date.fromisoformat(str(ts)[:10]) for ts in timestamps)
        payment_age_days = max(0, (reference_date - earliest_date).days)
    else:
        payment_age_days = 9999
    return {
        "check_duplicate_charge": {"duplicate_confirmed": bool(duplicate.get("duplicate_confirmed"))},
        "get_invoice_history": {"single_matching_invoice": bool(duplicate.get("single_matching_invoice"))},
        "payment_age_days": payment_age_days,
        "duplicate_amount": float(duplicate.get("duplicate_amount") or 0),
    }


def _wants_service_credit(message: str, intents: list[str]) -> bool:
    text = message.lower()
    return (
        "service_outage" in intents
        and (
            "refund_request" in intents
            or "credit" in text
            or "eligible" in text
            or "refund" in text
        )
    )


def _service_credit_dag_context(
    outage: dict[str, Any],
    customer_id: str,
    db_path: Path,
) -> dict[str, Any]:
    """Map outage evidence and prior credits onto service_credit_dag fields."""
    return {
        "check_outage_status": {
            "verified": bool(outage.get("verified")),
            "duration_hours": float(outage.get("duration_hours") or 0),
        },
        "get_invoice_history": {
            "credit_this_cycle": _has_service_credit_this_cycle(customer_id, db_path),
        },
    }


def _has_service_credit_this_cycle(customer_id: str, db_path: Path) -> bool:
    reference_date = os.environ.get("RESOLVEFLOW_NOW", "2026-06-01")[:10]
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM credits
            WHERE customer_id = ?
              AND date(applied_at) >= date(?, 'start of month')
              AND (
                lower(reason) LIKE '%outage%'
                OR lower(reason) LIKE '%service credit%'
                OR policy_id = 'service_credit_dag'
              )
            LIMIT 1
            """,
            (customer_id, reference_date),
        ).fetchone()
    return row is not None


def _service_credit_amount(policy_context: dict[str, Any]) -> float:
    outage = policy_context.get("check_outage_status", {})
    duration_hours = float(outage.get("duration_hours") or 0)
    if bool(outage.get("verified")) and duration_hours >= 6:
        return 500.0
    return 100.0


def _service_credit_reason(outage: dict[str, Any]) -> str:
    outage_id = outage.get("outage_id") or "verified outage"
    duration = float(outage.get("duration_hours") or 0)
    if duration:
        return f"Verified outage {outage_id} lasted {duration:g} hours."
    return f"Service outage credit for {outage_id}."


def _latest_invoice_id(invoice_payload: dict[str, Any] | None) -> str | None:
    invoices = (invoice_payload or {}).get("invoices") or []
    if not invoices:
        return None
    invoice_id = invoices[0].get("invoice_id") if isinstance(invoices[0], dict) else None
    return str(invoice_id) if invoice_id else None


def _tool_result(tool_results: list[dict[str, Any]], tool_name: str) -> dict[str, Any] | None:
    for result in reversed(tool_results):
        if result.get("tool_name") == tool_name and isinstance(result.get("result"), dict):
            return result["result"]
    return None


def _requires_refund_exception_review(message: str, intents: list[str]) -> bool:
    text = " ".join(message.lower().split())
    if "refund_request" not in intents and "refund" not in text:
        return False
    if "duplicate_charge" in intents:
        return False
    amount = _refund_amount_from_text(text)
    stale_terms = ("more than a month", "older than 30", "early may", "april", "last month")
    return (amount is not None and amount > 500) or any(term in text for term in stale_terms)


def _refund_exception_dag_context(
    message: str,
    customer: dict[str, Any] | None,
    invoice_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    text = " ".join(message.lower().split())
    amount = _refund_amount_from_text(text)
    invoices = (invoice_payload or {}).get("invoices") or []
    if amount is None and invoices and isinstance(invoices[0], dict):
        amount = float(invoices[0].get("amount") or 0)
    stale_terms = ("more than a month", "older than 30", "early may", "april", "last month")
    payment_age_days = 999 if any(term in text for term in stale_terms) else 0
    return {
        "refund_reason_eligible": "refund" in text,
        "payment_ownership_verified": bool((customer or {}).get("customer_id")),
        "payment_age_days": payment_age_days,
        "refund_amount": float(amount or 0),
    }


def _refund_amount_from_text(text: str) -> float | None:
    match = re.search(r"(?:inr|rs\.?|₹)\s*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
    if match:
        return float(match.group(1))
    match = re.search(r"\b([0-9]{3,6})(?:\s*rupees)?\b", text, flags=re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _outage_summary(outage: dict[str, Any]) -> str:
    duration = outage.get("duration_hours")
    if outage.get("verified") and duration:
        return f"verified outage {float(duration):g} hrs"
    return "no verified active outage"


def _check_credit_replay(message: str, customer_id: str, duplicate: dict[str, Any], db_path: Path):
    candidate = ActionCandidate(
        action="apply_credit",
        customer_id=customer_id,
        target_id=str(duplicate.get("invoice_id") or ""),
        amount=float(duplicate.get("duplicate_amount") or 0),
        reason="duplicate_charge_credit",
    )
    taken = load_taken_actions(customer_id, db_path=db_path)
    return confirm_action_replay(message, candidate, taken)


def _replay_summary(replay) -> str:
    if replay.already_taken:
        match = replay.matched_action
        if match and match.summary:
            return f"already taken - {match.summary}"
        return "requested action already taken"
    return "eligible; no prior matching action found"


def _normalize_session_id(session_id: str | None) -> str:
    normalized = " ".join(str(session_id or "default").strip().split())
    return normalized or "default"


def _state_key(customer_id: str, session_id: str | None) -> _ChatStateKey:
    return customer_id, _normalize_session_id(session_id)


def _state_for(customer_id: str, session_id: str = "default") -> dict[str, Any]:
    key = _state_key(customer_id, session_id)
    return _CHAT_STATES.setdefault(
        key,
        {
            "customer_id": customer_id,
            "session_id": key[1],
            "active_flow": "none",
            "last_confirmed_intent": None,
            "pending_customer_choice": None,
            "open_issues": [],
            "completed_tools": [],
            "last_bot_offer": [],
            "cancellation_notice_shown": False,
            "cancellation_request": None,
            # Cross-turn awareness so the agent does not repeat itself and can
            # state when something has already been handled.
            "turn_count": 0,
            "presented_findings": [],
            "history": [],
        },
    )


def _chat_state_lock(customer_id: str, session_id: str = "default") -> asyncio.Lock:
    key = _state_key(customer_id, session_id)
    loop = asyncio.get_running_loop()
    with _CHAT_STATE_LOCKS_GUARD:
        entry = _CHAT_STATE_LOCKS.get(key)
        if entry is None or entry[0] is not loop:
            lock = asyncio.Lock()
            _CHAT_STATE_LOCKS[key] = (loop, lock)
            return lock
        return entry[1]


def _snapshot_chat_state(state: dict[str, Any]) -> dict[str, Any]:
    try:
        snapshot = copy.deepcopy(state)
    except Exception:  # noqa: BLE001 - never expose the shared live state.
        snapshot = json.loads(json.dumps(state, ensure_ascii=True, default=str))
    return snapshot if isinstance(snapshot, dict) else {}


# Live chat state is persisted to a dedicated table (created lazily so the core
# 13-table schema, seeders, and reset stay untouched). The in-memory dict above
# is a write-through cache; on a cache miss (e.g. after a server restart) the
# state is hydrated from the database so multi-turn context survives restarts.
_CHAT_STATE_TABLE = "chat_session_state"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future_date(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


def _ensure_chat_state_table(connection: sqlite3.Connection) -> None:
    existing_columns = connection.execute(
        f"PRAGMA table_info({_CHAT_STATE_TABLE})"
    ).fetchall()
    if existing_columns and not any(row[1] == "session_id" for row in existing_columns):
        old_rows = connection.execute(
            f"SELECT customer_id, state_json, updated_at FROM {_CHAT_STATE_TABLE}"
        ).fetchall()
        connection.execute(f"DROP TABLE {_CHAT_STATE_TABLE}")
        _create_chat_state_table(connection)
        connection.executemany(
            f"""
            INSERT OR REPLACE INTO {_CHAT_STATE_TABLE}
                (customer_id, session_id, state_json, updated_at)
            VALUES (?, 'default', ?, ?)
            """,
            old_rows,
        )
        return
    if existing_columns:
        connection.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{_CHAT_STATE_TABLE}_customer_session
            ON {_CHAT_STATE_TABLE}(customer_id, session_id)
            """
        )
        return
    _create_chat_state_table(connection)


def _create_chat_state_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_CHAT_STATE_TABLE} (
            customer_id TEXT NOT NULL,
            session_id  TEXT NOT NULL,
            state_json  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (customer_id, session_id)
        )
        """
    )


def _read_state_row(customer_id: str, session_id: str, db_path: Path | None) -> dict[str, Any] | None:
    if not db_path:
        return None
    normalized_session_id = _normalize_session_id(session_id)
    try:
        with sqlite3.connect(db_path) as connection:
            _ensure_chat_state_table(connection)
            row = connection.execute(
                f"""
                SELECT state_json
                FROM {_CHAT_STATE_TABLE}
                WHERE customer_id = ? AND session_id = ?
                """,
                (customer_id, normalized_session_id),
            ).fetchone()
        if not row:
            return None
        value = json.loads(row[0])
        return value if isinstance(value, dict) else None
    except (sqlite3.Error, json.JSONDecodeError, OSError, TypeError):
        return None


def _load_session_state(customer_id: str, session_id: str, db_path: Path | None) -> dict[str, Any]:
    """Return the live chat state, hydrating from the database on a cache miss.

    Within a process the in-memory cache is authoritative (we write through after
    every turn). After a restart the cache is empty, so the stored row is loaded
    back so the agent still knows what was already handled.
    """
    key = _state_key(customer_id, session_id)
    if key in _CHAT_STATES:
        return _CHAT_STATES[key]
    state = _state_for(customer_id, key[1])  # creates and caches the default
    stored = _read_state_row(customer_id, key[1], db_path)
    if stored:
        state.update(stored)
        state["customer_id"] = customer_id
        state["session_id"] = key[1]
    return state


def _save_session_state(
    customer_id: str,
    session_id: str,
    state: dict[str, Any],
    db_path: Path | None,
) -> None:
    if not db_path:
        return
    normalized_session_id = _normalize_session_id(session_id)
    state = {**state, "customer_id": customer_id, "session_id": normalized_session_id}
    try:
        payload = json.dumps(state, ensure_ascii=True, default=str)
    except (TypeError, ValueError):
        return
    try:
        with sqlite3.connect(db_path) as connection:
            _ensure_chat_state_table(connection)
            connection.execute(
                f"""
                INSERT INTO {_CHAT_STATE_TABLE}
                    (customer_id, session_id, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(customer_id, session_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (customer_id, normalized_session_id, payload, _now_iso()),
            )
    except (sqlite3.Error, OSError):
        return


# Words/phrases that signal a genuine cancellation request. Used both to detect
# the deterministic cancellation intent and to gate the LLM classifier: the
# cancellation flow creates requests and flips account status, so we never enter
# it on a message that carries no cancellation signal (the LLM occasionally
# over-detects cancellation_intent on unrelated messages like a router fault).
_CANCEL_TERMS = (
    "cancel", "cancellation", "cacel", "cancle",
    "disconnect my", "stop my subscription", "stop my service",
    "stop service", "close my account", "terminate my service",
    "terminate my account", "terminate my plan", "end my plan",
    "end my subscription", "leave connectcare", "switch provider",
)


def _has_cancellation_signal(message: str) -> bool:
    text = " ".join(message.strip().lower().split())
    return any(term in text for term in _CANCEL_TERMS)


def _has_outage_signal(message: str) -> bool:
    text = " ".join(message.strip().lower().split())
    if "downgrade" in text:
        return False
    phrases = (
        "offline",
        "internet down",
        "internet is down",
        "net down",
        "service down",
        "outage",
        "downtime",
        "not working",
        "no internet",
    )
    return any(phrase in text for phrase in phrases)


def _has_payment_problem_signal(message: str) -> bool:
    text = " ".join(message.strip().lower().split())
    phrases = (
        "charged",
        "billing",
        "bill",
        "invoice",
        "pay later",
        "payment",
        "keep charging",
        "charges",
    )
    return any(phrase in text for phrase in phrases)


def _has_refund_signal(message: str) -> bool:
    text = " ".join(message.strip().lower().split())
    phrases = (
        "refund",
        "credit my bill",
        "credit",
        "charged twice",
        "duplicate charge",
        "extra charges",
    )
    return any(phrase in text for phrase in phrases)


def _has_human_or_fix_signal(message: str) -> bool:
    text = " ".join(message.strip().lower().split())
    phrases = ("specialist", "human", "agent", "fix", "resolve", "review")
    return any(phrase in text for phrase in phrases)


def _needs_router_diagnostic(
    message: str,
    chat_state: dict[str, Any],
    intents: list[str],
) -> bool:
    text = " ".join(message.strip().lower().split())
    if "router_issue" in intents:
        return True
    if "service_outage" not in intents:
        return False
    history = chat_state.get("history")
    prior_customer_turns = [
        str(item.get("content", "")).lower()
        for item in history
        if isinstance(item, dict) and item.get("role") == "user"
    ] if isinstance(history, list) else []
    repeated_not_working = text.count("not working") >= 2
    prior_outage_mentions = sum(
        1
        for turn in prior_customer_turns
        if "not working" in turn or "internet" in turn or "outage" in turn
    )
    return repeated_not_working or prior_outage_mentions >= 1


def _normalize_chat_intent(message: str, state: dict[str, Any]) -> str | None:
    text = " ".join(message.strip().lower().split())
    if text in {"hi", "hii", "hello", "hey"}:
        return "greeting"

    abort_terms = {
        "stop cancel", "stop cacel", "stop cancle", "stop cancellation",
        "abort cancel", "abort cacel", "abort cancle", "abort cancellation",
        "don't cancel", "don't cacel", "don't cancle", "do not cancel",
        "nevermind", "keep my subscription", "keep my service", "no cancel"
    }
    if any(term in text for term in abort_terms):
        return "abort_cancellation"

    cancel_confirmations = {"cancel", "cancel now", "proceed",
                            "yes cancel", "continue cancellation", "cacel", "cancle"}
    cancel_terms = _CANCEL_TERMS
    is_cancel_message = any(
        term in text for term in cancel_terms) or text in cancel_confirmations
    # A cancellation request was already created this session: re-raising it
    # should confirm it is already done, not restart the options flow.
    if state.get("cancellation_request") and is_cancel_message:
        return "cancellation_confirmation"
    if (
        state.get("active_flow") == "cancellation"
        and state.get("pending_customer_choice") == "cancel_now_or_resolve_credit_first"
        and text in cancel_confirmations
    ):
        return "cancellation_confirmation"
    if any(term in text for term in cancel_terms):
        return "cancellation_request"
    return None


def _mark_cancellation_pending(state: dict[str, Any], intents: list[str], pending_credits: dict[str, Any]) -> None:
    open_issues = []
    if "duplicate_charge" in intents or pending_credits.get("duplicate_charge_refund_pending"):
        open_issues.append("duplicate_charge")
    if "service_outage" in intents or float(pending_credits.get("pending_credit_amount") or 0) > 0:
        open_issues.append("service_outage")
    state.update(
        {
            "active_flow": "cancellation",
            "last_confirmed_intent": "cancellation_request",
            "pending_customer_choice": "cancel_now_or_resolve_credit_first",
            "open_issues": open_issues,
            "completed_tools": [
                "get_subscription_status",
                "get_cancellation_policy",
                "check_pending_credits",
            ],
            "last_bot_offer": ["cancel_now", "resolve_credit_first"],
            "cancellation_notice_shown": True,
        }
    )


def _abort_cancellation(
    state: dict[str, Any],
    customer_id: str | None = None,
    session_id: str = "default",
    db_path: Path | None = None,
) -> None:
    state.update({
        "active_flow": "none",
        "pending_customer_choice": None,
        "cancellation_request": None,
        "last_confirmed_intent": None,
        "last_bot_offer": [],
    })
    # Reverse any account-level changes a prior cancellation made so the demo
    # (and the customer's real status) self-heals when they decide to stay.
    _restore_active_account(customer_id, db_path)
    if customer_id:
        _MEMORY_CANCELLATION_REQUESTS.pop(_state_key(customer_id, session_id), None)


def _restore_active_account(customer_id: str | None, db_path: Path | None) -> None:
    """Undo a pending cancellation: re-activate the account and close its ticket.

    ``_create_cancellation_request`` flips ``customers.account_status`` to
    ``pending_cancellation`` and opens a ticket. Aborting must put both back so
    repeated demos do not permanently degrade the seeded customer state.
    """
    if not customer_id or not db_path:
        return
    try:
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                UPDATE customers SET account_status = 'active'
                WHERE customer_id = ? AND account_status = 'pending_cancellation'
                """,
                (customer_id,),
            )
            connection.execute(
                """
                UPDATE tickets SET status = 'resolved'
                WHERE customer_id = ?
                  AND issue_type = 'cancellation_request'
                  AND status IN ('open', 'in_progress')
                """,
                (customer_id,),
            )
    except sqlite3.Error:
        return


def _mark_cancellation_completed(state: dict[str, Any], cancellation_request: dict[str, Any]) -> None:
    state.update(
        {
            "active_flow": "cancellation",
            "last_confirmed_intent": "cancellation_confirmation",
            "pending_customer_choice": None,
            "completed_tools": [*state.get("completed_tools", []), "create_cancellation_request"],
            "last_bot_offer": [],
            "cancellation_request": cancellation_request,
        }
    )


def _get_subscription_status(customer_id: str, db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT c.customer_id, c.account_status, c.plan_id, c.risk_level,
                   p.plan_name, p.monthly_price, p.speed_mbps, p.cancellation_fee
            FROM customers c
            JOIN plans p ON p.plan_id = c.plan_id
            WHERE c.customer_id = ?
            """,
            (customer_id,),
        ).fetchone()
    if row is None:
        return {"customer_id": customer_id, "found": False, "cancellation_allowed": False}
    fee = float(row["cancellation_fee"] or 0)
    return {
        "customer_id": row["customer_id"],
        "found": True,
        "plan_id": row["plan_id"],
        "plan_name": row["plan_name"],
        "monthly_price": float(row["monthly_price"] or 0),
        "subscription_status": row["account_status"],
        "contract_lockin": fee > 0,
        "cancellation_fee": fee,
        "billing_cycle_end": _future_date(4),
        "cancellation_allowed": row["account_status"] in {"active", "pending_cancellation"},
        "risk_level": row["risk_level"],
    }


def _get_cancellation_policy(customer: dict[str, Any] | None, subscription: dict[str, Any]) -> dict[str, Any]:
    risk_level = str((customer or {}).get("risk_level")
                     or subscription.get("risk_level") or "low")
    return {
        "policy_id": "cancellation_policy",
        "policy_name": "cancellation_retention_dag",
        "cancellation_allowed": bool(subscription.get("cancellation_allowed")),
        "notice_required": True,
        "pending_credit_notice_required": True,
        "human_approval_required": risk_level == "critical",
        "retention_offer_required": risk_level in {"high", "critical"},
        "rule": "Mention pending credits once, then proceed if the customer confirms cancellation.",
    }


def _cancellation_retention_dag_context(
    customer: dict[str, Any] | None,
    subscription: dict[str, Any],
    pending_credits: dict[str, Any],
    intents: list[str],
) -> dict[str, Any]:
    account_status = str(subscription.get("subscription_status") or (customer or {}).get("account_status") or "")
    issue_intents = {
        "service_outage",
        "router_issue",
        "billing_dispute",
        "duplicate_charge",
        "refund_request",
        "technician_request",
    }
    has_open_issue = (
        account_status == "pending_cancellation"
        or bool(issue_intents & set(intents))
        or float(pending_credits.get("pending_credit_amount") or 0) > 0
        or bool(pending_credits.get("duplicate_charge_refund_pending"))
    )
    return {
        "lookup_customer": {
            "identity_verified": bool((customer or {}).get("customer_id") or subscription.get("found")),
        },
        "has_open_issue": has_open_issue,
        "churn_score": float((customer or {}).get("churn_score") or 0),
    }


def _check_pending_credits(customer_id: str, customer: dict[str, Any] | None, db_path: Path) -> dict[str, Any]:
    duplicate = check_duplicate_charge(customer_id, db_path=db_path)
    outage = None
    if customer and customer.get("location"):
        outage = check_outage_status(
            str(customer["location"]), customer_id=customer_id, db_path=db_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        credit_rows = connection.execute(
            """
            SELECT credit_id, amount, reason, applied_to_invoice
            FROM credits
            WHERE customer_id = ?
            ORDER BY datetime(applied_at) DESC
            """,
            (customer_id,),
        ).fetchall()
    existing_credits = [dict(row) for row in credit_rows]
    existing_reasons = " ".join(
        str(row.get("reason") or "").lower() for row in existing_credits)

    outage_credit = 0.0
    if outage and outage.get("verified") and "outage" not in existing_reasons:
        outage_credit = 500.0 if float(outage.get(
            "duration_hours") or 0) >= 6 else 100.0

    duplicate_pending = bool(duplicate.get(
        "duplicate_confirmed")) and "duplicate" not in existing_reasons
    return {
        "customer_id": customer_id,
        "pending_credit_amount": outage_credit,
        "credit_reason": "Verified outage longer than 6 hours" if outage_credit >= 500 else ("Verified short outage" if outage_credit else None),
        "duplicate_charge_refund_pending": duplicate_pending,
        "duplicate_invoice_id": duplicate.get("invoice_id"),
        "duplicate_amount": duplicate.get("duplicate_amount"),
        "existing_credits": existing_credits,
        "warning": "Customer should be informed once before cancellation is completed.",
    }


def _create_cancellation_request(
    customer_id: str,
    reason: str,
    db_path: Path,
    session_id: str = "default",
) -> dict[str, Any]:
    try:
        with sqlite3.connect(db_path, timeout=30.0) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            customer = connection.execute(
                "SELECT customer_id FROM customers WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()
            if customer is None:
                raise ValueError(f"customer {customer_id!r} not found")

            existing = connection.execute(
                """
                SELECT ticket_id, status, created_at
                FROM tickets
                WHERE customer_id = ?
                  AND issue_type = 'cancellation_request'
                  AND status IN ('open', 'in_progress')
                ORDER BY datetime(created_at) DESC
                LIMIT 1
                """,
                (customer_id,),
            ).fetchone()
            if existing is not None:
                ticket_id = str(existing["ticket_id"])
                return {
                    "mode": "already_taken",
                    "customer_id": customer_id,
                    "ticket_id": ticket_id,
                    "cancellation_request_id": _cancellation_id(ticket_id),
                    "status": existing["status"],
                    "service_active_until": _future_date(4),
                    "pending_credit_preserved": True,
                }

            ticket_id = f"TKT-{uuid4().hex[:12].upper()}"
            created_at = _now_iso()
            connection.execute(
                """
                INSERT INTO tickets(ticket_id, customer_id, issue_type, status, priority, created_at)
                VALUES (?, ?, 'cancellation_request', 'open', 'high', ?)
                """,
                (ticket_id, customer_id, created_at),
            )
            connection.execute(
                "UPDATE customers SET account_status = 'pending_cancellation' WHERE customer_id = ?",
                (customer_id,),
            )
            return {
                "mode": "created",
                "customer_id": customer_id,
                "ticket_id": ticket_id,
                "cancellation_request_id": _cancellation_id(ticket_id),
                "status": "created",
                "reason": reason,
                "service_active_until": _future_date(4),
                "pending_credit_preserved": True,
            }
    except sqlite3.Error:
        return _create_memory_cancellation_request(customer_id, reason, session_id)


def _create_memory_cancellation_request(
    customer_id: str,
    reason: str,
    session_id: str = "default",
) -> dict[str, Any]:
    key = _state_key(customer_id, session_id)
    existing = _MEMORY_CANCELLATION_REQUESTS.get(key)
    if existing is not None:
        return {"mode": "already_taken", **existing}
    ticket_id = f"TKT-MEM-{len(_MEMORY_CANCELLATION_REQUESTS) + 1:04d}"
    request = {
        "customer_id": customer_id,
        "ticket_id": ticket_id,
        "cancellation_request_id": _cancellation_id(ticket_id),
        "status": "created",
        "reason": reason,
        "service_active_until": "2026-07-04",
        "pending_credit_preserved": True,
        "persistence": "memory_fallback",
    }
    _MEMORY_CANCELLATION_REQUESTS[key] = request
    return {"mode": "created", **request}


def _cancellation_id(ticket_id: str) -> str:
    return f"CAN-{ticket_id.replace('TKT-', '')[-6:]}"


def _subscription_summary(subscription: dict[str, Any]) -> str:
    if not subscription.get("found"):
        return "subscription not found"
    fee = float(subscription.get("cancellation_fee") or 0)
    lock = "lock-in fee applies" if fee else "no lock-in"
    return f"{subscription.get('subscription_status')} subscription, {lock}"


def _cancellation_policy_summary(policy: dict[str, Any]) -> str:
    if policy.get("human_approval_required"):
        return "human approval required"
    return "cancellation allowed after pending-credit notice"


def _pending_credit_summary(pending: dict[str, Any]) -> str:
    parts = []
    amount = float(pending.get("pending_credit_amount") or 0)
    if amount:
        parts.append(f"INR {amount:g} outage credit eligible")
    if pending.get("duplicate_charge_refund_pending"):
        parts.append("duplicate-charge review pending")
    return ", ".join(parts) if parts else "no pending credits found"


def _cancellation_request_summary(request: dict[str, Any]) -> str:
    if request.get("mode") == "already_taken":
        return f"already created {request.get('cancellation_request_id')}"
    return f"created {request.get('cancellation_request_id')}"


def _active_flow_greeting_response(customer: dict[str, Any] | None, state: dict[str, Any]) -> str:
    name = _first_name(customer)
    if state.get("pending_customer_choice") == "cancel_now_or_resolve_credit_first":
        return (
            f"Hi {name}. Your cancellation flow is in progress. "
            "Do you want me to cancel now, resolve the pending billing/service credit first, or help with something else?"
        )
    return f"Hi {name}. Your cancellation request is in progress. I can continue that or help with another account issue."


def _cancellation_options_response(customer: dict[str, Any] | None, tools: list[dict[str, Any]]) -> str:
    name = _first_name(customer)
    by_name = {str(tool.get("tool_name")): tool.get("result") or {}
               for tool in tools}
    subscription = by_name.get("get_subscription_status", {})
    pending = by_name.get("check_pending_credits", {})
    duplicate = by_name.get("check_duplicate_charge", {})
    outage = by_name.get("check_outage_status", {})
    retention = by_name.get("build_retention_offer", {})
    lines = []

    # Multi-issue: if the same message also raised a billing/outage problem,
    # acknowledge those verified findings first so the cancellation request does
    # not bury them. This is the flagship "several issues in one message" case.
    findings = []
    if duplicate.get("duplicate_confirmed"):
        amount = float(duplicate.get("duplicate_amount") or 0)
        findings.append(
            f"the duplicate charge on invoice {duplicate.get('invoice_id')} for INR {amount:g}")
    if outage.get("verified"):
        duration = float(outage.get("duration_hours") or 0)
        findings.append(
            f"the verified {duration:g}-hour outage in {outage.get('location')}")
    if findings:
        lines.append(
            f"{name}, before we talk about cancelling I've confirmed {_join_clause(findings)} - I'll keep those queued for the refund/credit and won't lose them.")

    if subscription.get("cancellation_allowed"):
        fee = float(subscription.get("cancellation_fee") or 0)
        lead = name if not findings else "On the cancellation itself"
        if fee:
            lines.append(
                f"{lead}, cancellation is allowed on your account. The current plan has an INR {fee:g} cancellation fee.")
        else:
            lines.append(
                f"{lead}, cancellation is allowed on your account and there is no lock-in fee.")
    else:
        lines.append(
            f"{name}, I checked your subscription status and cancellation needs a specialist review before it can proceed.")

    # Retention / churn-save offer for at-risk customers.
    if retention.get("offer_available") and retention.get("headline"):
        lines.append(
            f"Before you go, I can offer {retention['headline']} to keep you with us.")

    pending_items = []
    amount = float(pending.get("pending_credit_amount") or 0)
    if amount:
        pending_items.append(f"INR {amount:g} outage credit is still eligible")
    if pending.get("duplicate_charge_refund_pending"):
        invoice = pending.get("duplicate_invoice_id")
        pending_items.append(
            f"a duplicate-charge review is pending{f' for invoice {invoice}' if invoice else ''}")
    if pending_items:
        lines.append("There's also pending account value: " +
                     "; ".join(pending_items) + ".")
        lines.append(
            "You can cancel now, take the retention offer, or resolve the pending credit/refund first. Which do you prefer?"
            if retention.get("offer_available")
            else "You can cancel now, or resolve the pending credit/refund first and then cancel. Which do you prefer?")
    else:
        if retention.get("offer_available"):
            lines.append(
                "Reply 'cancel' to create the cancellation request now, or let me know if the offer above changes your mind.")
        else:
            lines.append("Reply 'cancel' to create the cancellation request now.")
    return " ".join(lines)


def _build_retention_offer(
    customer: dict[str, Any] | None,
    subscription: dict[str, Any],
    cancellation_policy: dict[str, Any],
) -> dict[str, Any]:
    """Compute a churn-save offer for at-risk customers about to cancel.

    The cancellation DAG already flags ``retention_offer_required`` for high and
    critical risk; this turns that flag into a concrete, policy-bounded offer the
    agent can present before creating the cancellation request.
    """
    if not cancellation_policy.get("retention_offer_required"):
        return {"offer_available": False}
    risk = str((customer or {}).get("risk_level")
               or subscription.get("risk_level") or "low").lower()
    monthly = float(subscription.get("monthly_price") or 0)
    fee = float(subscription.get("cancellation_fee") or 0)
    if risk == "critical":
        discount_pct = 30
        months = 6
        waive_fee = True
    else:  # high
        discount_pct = 20
        months = 3
        waive_fee = fee > 0
    monthly_savings = round(monthly * discount_pct / 100, 2)
    total_savings = round(monthly_savings * months + (fee if waive_fee else 0), 2)
    parts = [f"{discount_pct}% off for {months} months"]
    if waive_fee and fee:
        parts.append(f"a waived INR {fee:g} cancellation fee")
    headline = " and ".join(parts)
    return {
        "offer_available": True,
        "offer_id": f"RET-{discount_pct}-{months}M",
        "risk_level": risk,
        "discount_pct": discount_pct,
        "discount_months": months,
        "monthly_savings": monthly_savings,
        "waive_cancellation_fee": waive_fee,
        "cancellation_fee": fee,
        "estimated_total_savings": total_savings,
        "headline": headline,
        "expires": _future_date(3),
    }


def _retention_offer_summary(offer: dict[str, Any]) -> str:
    if not offer.get("offer_available"):
        return "no retention offer"
    savings = float(offer.get("estimated_total_savings") or 0)
    return f"{offer.get('discount_pct')}% x {offer.get('discount_months')}mo (~INR {savings:g} saved)"


def _cancellation_confirmation_response(customer: dict[str, Any] | None, tools: list[dict[str, Any]]) -> str:
    name = _first_name(customer)
    request = {}
    for tool in tools:
        if tool.get("tool_name") == "create_cancellation_request":
            request = tool.get("result") or {}
            break
    request_id = request.get("cancellation_request_id") or request.get(
        "ticket_id") or "created"
    if request.get("mode") == "already_taken":
        return (
            f"{name}, your cancellation request is already open as {request_id}. "
            f"Your service remains active until {request.get('service_active_until')}, and your pending credit/refund notes are preserved."
        )
    return (
        f"{name}, your cancellation request has been created successfully. "
        f"Request ID: {request_id}. Your service remains active until {request.get('service_active_until')}. "
        "I also preserved your pending duplicate-charge review and eligible outage credit note so support can process those separately."
    )


def _finding_keys(tools: list[dict[str, Any]]) -> list[str]:
    """Stable keys for the concrete findings a turn surfaced to the customer.

    Used to detect when a later turn is re-raising something already covered.
    """
    by_name = {str(tool.get("tool_name")): (tool.get("result") or {})
               for tool in tools}
    keys: list[str] = []
    duplicate = by_name.get("check_duplicate_charge") or {}
    if duplicate.get("duplicate_confirmed"):
        keys.append(f"duplicate:{duplicate.get('invoice_id')}")
    outage = by_name.get("check_outage_status") or {}
    if outage.get("verified"):
        keys.append(f"outage:{outage.get('location')}")
    if by_name.get("run_router_diagnostic"):
        keys.append("router_diagnostic")
    return keys


def _finding_label(key: str) -> str:
    if key.startswith("duplicate:"):
        return f"the duplicate charge on invoice {key.split(':', 1)[1]}"
    if key.startswith("outage:"):
        return f"the verified outage in {key.split(':', 1)[1]}"
    if key == "router_diagnostic":
        return "your router diagnostic"
    return key


def _remember_findings(state: dict[str, Any], keys: list[str]) -> None:
    presented = state.setdefault("presented_findings", [])
    for key in keys:
        if key not in presented:
            presented.append(key)


def _session_context_note(state: dict[str, Any]) -> str:
    presented = state.get("presented_findings") or []
    if not presented:
        return ""
    labels = "; ".join(_finding_label(key) for key in presented)
    return (
        f"Earlier in THIS same conversation you already explained: {labels}. "
        "Do not re-explain those from scratch. Only add what is genuinely new, and if the "
        "customer is re-asking about something already covered, briefly acknowledge it was "
        "already handled and give the current status instead of repeating the full explanation. "
    )


def _join_clause(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _already_addressed_response(
    customer: dict[str, Any] | None,
    intents: list[str],
    emotion: str,
    tools: list[dict[str, Any]],
) -> str:
    name = _first_name(customer)
    by_name = {str(tool.get("tool_name")): (tool.get("result") or {})
               for tool in tools}
    duplicate = by_name.get("check_duplicate_charge") or {}
    outage = by_name.get("check_outage_status") or {}
    replay = by_name.get("apply_credit_guard") or {}

    covered: list[str] = []
    if duplicate.get("duplicate_confirmed"):
        amount = float(duplicate.get("duplicate_amount") or 0)
        covered.append(
            f"the duplicate charge on invoice {duplicate.get('invoice_id')} for INR {amount:g}")
    if outage.get("verified"):
        covered.append(f"the verified outage in {outage.get('location')}")

    parts: list[str] = []
    if covered:
        parts.append(
            f"{name}, we've already been over {_join_clause(covered)} - nothing new has changed on the account since we last checked.")
    else:
        parts.append(
            f"{name}, I've already looked into this and there's no new information on the account since we last checked.")

    if isinstance(replay, dict) and replay.get("already_taken"):
        matched = replay.get("matched_action") or {}
        summary = matched.get("summary") if isinstance(matched, dict) else None
        parts.append(
            f"The credit was already applied{f' ({summary})' if summary else ''}, so there's nothing more to run on that.")
    elif duplicate.get("duplicate_confirmed") or outage.get("verified"):
        parts.append(
            "It's still queued at the policy gate for the refund/credit to be actioned; I won't claim it's done until the tool confirms it.")

    if "cancellation_intent" in intents:
        parts.append("Your cancellation is still on hold behind these items. Do you want me to push the refund through with a specialist, or go ahead with the cancellation?")
    else:
        parts.append(
            "Would you like me to escalate it to a specialist to action it now, or is there anything else I can help with?")
    return " ".join(parts)


def _evidence_response(customer: dict[str, Any] | None, intents: list[str], emotion: str, tools: list[dict[str, Any]]) -> str:
    name = _first_name(customer)
    if not tools:
        # No tool evidence means no specific issue has been identified yet
        # (a greeting, a vague "can you help", or general chit-chat) -- talking
        # about "verified evidence" here would be a non-sequitur, so just ask
        # what the customer needs instead of narrating an empty evidence trail.
        # plan_change and technician_request have no tool wired up yet, so they
        # also land here -- acknowledge the specific ask instead of a blank
        # greeting that reads as if the message was never parsed.
        if "plan_change" in intents:
            return f"Got it, {name} - tell me which plan you're interested in (or your budget) and I'll check what's available."
        if "technician_request" in intents:
            return f"Got it, {name} - I can arrange a technician visit. What day and time works best for you?"
        if emotion == "angry":
            return f"{name}, I hear you're frustrated. Tell me what's going on and I'll get it sorted."
        return f"Hi {name}, how can I help you today?"
    by_name = {str(tool.get("tool_name")): tool for tool in tools}
    duplicate = (by_name.get("check_duplicate_charge")
                 or {}).get("result") or {}
    outage = (by_name.get("check_outage_status") or {}).get("result") or {}
    replay = (by_name.get("apply_credit_guard") or {}).get("result") or {}
    diagnostic = (by_name.get("run_router_diagnostic") or {}).get("result") or {}

    parts = []
    if emotion == "angry":
        parts.append(
            f"{name}, I hear how frustrating this is. I will use the evidence already on the account instead of making you repeat everything.")
    elif emotion == "frustrated":
        parts.append(
            f"{name}, I can see this has taken more effort than it should. I will keep this focused and verify each issue before actioning it.")
    elif _relationship_start(customer) < 40:
        parts.append(
            f"{name}, I know your experience with us hasn't always been smooth, so I'll keep this simple and verify each issue before actioning it.")
    else:
        parts.append(
            f"{name}, I checked your account and the verified support evidence.")

    if duplicate.get("duplicate_confirmed"):
        amount = float(duplicate.get("duplicate_amount") or 0)
        parts.append(
            f"There is duplicate payment evidence on invoice {duplicate.get('invoice_id')} for INR {amount:g}.")
    if outage.get("verified"):
        duration = float(outage.get("duration_hours") or 0)
        parts.append(
            f"There is also a verified outage in {outage.get('location')} lasting {duration:g} hours.")
    diagnostic_ran = "run_router_diagnostic" in by_name
    if diagnostic_ran and diagnostic.get("diagnostic_available"):
        if diagnostic.get("diagnostic_failure"):
            parts.append(
                f"The router diagnostic confirms a real fault: {diagnostic.get('router_status')} "
                f"status, signal strength {diagnostic.get('signal_strength')}. {diagnostic.get('recommendation') or ''}".strip())
        else:
            parts.append(
                f"The router diagnostic came back {diagnostic.get('router_status') or 'normal'} "
                f"with signal strength {diagnostic.get('signal_strength')}, so the fault is likely elsewhere. "
                f"{diagnostic.get('recommendation') or ''}".strip())
    elif diagnostic_ran and diagnostic.get("recommendation"):
        parts.append(diagnostic["recommendation"])
    if "cancellation_intent" in intents:
        parts.append(
            "I will keep the cancellation request queued while the billing and service issues are handled first.")
    if isinstance(replay, dict) and replay.get("already_taken"):
        matched = replay.get("matched_action") or {}
        summary = matched.get("summary") if isinstance(matched, dict) else None
        parts.append(
            f"That requested credit action has already been taken{f': {summary}' if summary else ''}, so I will not run it again.")
    elif duplicate.get("duplicate_confirmed") or outage.get("verified"):
        parts.append(
            "The next action is ready for the policy gate; I will not claim it is complete until the tool action confirms it.")
    elif not diagnostic_ran:
        parts.append(
            "I do not have enough verified evidence to take an account action yet, so the next step is clarification or a safe diagnostic.")
    return " ".join(parts)


def _response_overclaims(text: str, tools: list[dict[str, Any]]) -> bool:
    lowered = text.lower()
    by_name = {str(tool.get("tool_name")): tool for tool in tools}
    guard = (by_name.get("apply_credit_guard") or {}).get("result") or {}
    credit_already_taken = isinstance(
        guard, dict) and bool(guard.get("already_taken"))
    credit_tool_applied = any(
        str(tool.get("tool_name")) in {"apply_credit", "refund_payment"}
        and bool((tool.get("result") or {}).get("applied", False))
        for tool in tools
        if isinstance(tool.get("result"), dict)
    )
    action_confirmed = credit_already_taken or credit_tool_applied
    promised_action_terms = (
        "will process a full refund",
        "will process the refund",
        "will process a refund",
        "will now process",
        "processing a refund",
        "processing the refund",
        "processing a credit",
        "processing the credit",
        "we're processing",
        "we are processing",
        "refund immediately",
        "credit has been applied",
        "credit was applied",
        "credit of inr",
        "i have applied",
        "we've applied",
        "we have applied",
        "will be credited",
        "will be refunded",
        "appear on your next statement",
        "appear on your next bill",
        "back to your account",
    )
    if not action_confirmed and any(term in lowered for term in promised_action_terms):
        return True
    if "fully resolved" in lowered and not action_confirmed:
        return True
    return False


def _response_misses_evidence(text: str, tools: list[dict[str, Any]]) -> bool:
    lowered = text.lower()
    by_name = {str(tool.get("tool_name")): tool for tool in tools}
    duplicate = (by_name.get("check_duplicate_charge")
                 or {}).get("result") or {}
    outage = (by_name.get("check_outage_status") or {}).get("result") or {}
    if isinstance(duplicate, dict) and duplicate.get("duplicate_confirmed"):
        if not any(term in lowered for term in ("duplicate", "invoice", "inr", "double charge")):
            return True
    if isinstance(outage, dict) and outage.get("verified"):
        if not any(term in lowered for term in ("outage", "disruption", "service issue", "network issue", "connectivity", "affected")):
            return True

    # Ensure the response is at least a minimum length to be considered valid
    if len(text.strip()) < 5:
        return True
    return False


# ---------------------------------------------------------------------------
# Evidence receipts (Tool Receipts, arXiv 2603.10060): bind every customer-facing
# claim to the exact tool output that backs it via a tamper-evident HMAC so the
# audit trail can prove "this fact came from this verified tool call".
# ---------------------------------------------------------------------------
_RECEIPT_SECRET = os.environ.get("RESOLVEFLOW_RECEIPT_SECRET", "").encode("utf-8")
if not _RECEIPT_SECRET:
    logging.warning(
        "RESOLVEFLOW_RECEIPT_SECRET is not set — evidence receipts are using a "
        "fallback key and are NOT tamper-evident. Set the env var before deploying."
    )
    _RECEIPT_SECRET = b"resolveflow-evidence-key"


def _tool_receipt(tool_name: str, query: str, result: Any) -> dict[str, Any]:
    canonical = json.dumps(
        {"tool": tool_name, "query": query, "result": result},
        sort_keys=True, ensure_ascii=True, default=str,
    )
    digest = hmac.new(_RECEIPT_SECRET, canonical.encode("utf-8"),
                      hashlib.sha256).hexdigest()
    return {
        "tool": tool_name,
        "receipt_id": f"rcpt_{digest[:12]}",
        "hash": digest[:16],
        "algo": "HMAC-SHA256",
        "summary": None,
    }


def _attach_receipts(message: str, tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stamp each successful tool result with a receipt and return the receipt list."""
    receipts: list[dict[str, Any]] = []
    for tool in tool_results:
        if tool.get("ok") is False:
            continue
        receipt = _tool_receipt(
            str(tool.get("tool_name")), message, tool.get("result") or {})
        receipt["summary"] = tool.get("summary")
        tool["receipt_id"] = receipt["receipt_id"]
        receipts.append(receipt)
    return receipts


def _verified_claims(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The concrete, evidence-backed facts a turn surfaced, each bound to a receipt."""
    by_name = {str(t.get("tool_name")): t for t in tool_results}
    claims: list[dict[str, Any]] = []

    def _add(tool_name: str, claim: str) -> None:
        tool = by_name.get(tool_name)
        if tool is None:
            return
        claims.append({
            "claim": claim,
            "tool": tool_name,
            "receipt_id": tool.get("receipt_id"),
        })

    dup = (by_name.get("check_duplicate_charge") or {}).get("result") or {}
    if dup.get("duplicate_confirmed"):
        amount = float(dup.get("duplicate_amount") or 0)
        _add("check_duplicate_charge",
             f"Duplicate charge confirmed on invoice {dup.get('invoice_id')} for INR {amount:g}")
    outage = (by_name.get("check_outage_status") or {}).get("result") or {}
    if outage.get("verified"):
        duration = float(outage.get("duration_hours") or 0)
        _add("check_outage_status",
             f"Verified outage in {outage.get('location')} lasting {duration:g} hours")
    guard = (by_name.get("apply_credit_guard") or {}).get("result") or {}
    if isinstance(guard, dict) and guard.get("already_taken"):
        matched = guard.get("matched_action") or {}
        summary = matched.get("summary") if isinstance(matched, dict) else None
        _add("apply_credit_guard",
             f"Credit already applied{f' ({summary})' if summary else ''}; not re-run")
    diag = (by_name.get("run_router_diagnostic") or {}).get("result") or {}
    if diag:
        _add("run_router_diagnostic",
             f"Router diagnostic: {diag.get('recommendation') or 'completed'}")
    offer = (by_name.get("build_retention_offer") or {}).get("result") or {}
    if offer.get("offer_available"):
        _add("build_retention_offer", f"Retention offer: {offer.get('headline')}")
    request = (by_name.get("create_cancellation_request") or {}).get("result") or {}
    if request:
        _add("create_cancellation_request",
             f"Cancellation request {request.get('cancellation_request_id') or request.get('ticket_id')} on record")
    return claims


# ---------------------------------------------------------------------------
# Action trust scoring + self-revision (Cleanlab TLM on tau2-bench: trust scoring
# with a revise-or-escalate fallback cuts agent failures up to 50%). We score the
# drafted reply against the verified evidence (deterministic guards + an optional
# LLM chain-of-verification self-check); low trust triggers one self-revision and,
# if still untrustworthy, a safe grounded fallback plus a human escalation.
# ---------------------------------------------------------------------------
_TRUST_THRESHOLD = 0.6


def _clean_numbers_for_prompt(value: Any) -> Any:
    """Whole-number floats (1199.0) print with a trailing .0 in Python's repr,
    and the LLM copies that literal into its reply (e.g. "INR 1199.0"). Strip
    it before the evidence goes into the prompt so replies read naturally."""
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {key: _clean_numbers_for_prompt(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_numbers_for_prompt(item) for item in value]
    return value


def _build_response_prompt(
    message: str,
    customer: dict[str, Any] | None,
    tool_results: list[dict[str, Any]],
    policy_results: list[dict[str, Any]],
    chat_state: dict[str, Any],
) -> str:
    history_text = ""
    if chat_state.get("history"):
        history_text = "Recent Conversation History:\n"
        for turn in chat_state["history"][-6:]:
            history_text += f"{turn['role'].capitalize()}: {turn['content']}\n"
        history_text += "\n"
    clean_customer = _clean_numbers_for_prompt(customer)
    clean_tool_results = _clean_numbers_for_prompt(tool_results)
    return (
        f"You are a helpful telecom support agent.\n"
        f"{history_text}"
        f"Customer Message: '{message}'\n"
        f"Customer Context: {clean_customer}\n"
        f"Tool Results: {clean_tool_results}\n"
        f"Policies Retrieved: {[p['policy_id'] for p in policy_results]}\n\n"
        f"{_session_context_note(chat_state)}"
        "Write a very short, friendly, evidence-grounded final response to the customer (maximum 2 sentences). "
        "Use the verified tool results and policy retrievals. "
        "If an action guard says the requested action was already taken, say that clearly and do not pretend to run it again. "
        "If the customer is angry or frustrated, acknowledge it without asking them to repeat known details. "
        "Do not claim the issue is resolved unless the tool evidence proves the action was completed. "
        "Do not promise that a refund or credit will be processed, applied, or appear on a future bill unless an apply/refund tool result says it was applied. "
        "If the evidence only proves eligibility, say the next action is ready for policy/tool confirmation. "
        "Address them by their first name. Do not use markdown. Speak directly to the customer in a conversational tone."
    )


def _cove_verify(text: str, tool_results: list[dict[str, Any]], verifier: LLMClient | None) -> tuple[bool, list[str]]:
    """Chain-of-verification self-check: does the reply claim anything the tools don't support?"""
    if verifier is None or not text.strip():
        return True, []
    evidence = [
        {"tool": t.get("tool_name"), "summary": t.get(
            "summary"), "result": t.get("result")}
        for t in tool_results
    ]
    prompt = (
        "You are a verification checker for a telecom support agent.\n"
        "Given the TOOL EVIDENCE and the agent's DRAFT REPLY, decide whether every "
        "factual claim or commitment in the reply is supported by the evidence.\n"
        "A reply that only states verified facts, asks a question, or says an action "
        "is queued/eligible (without claiming it is done) is SUPPORTED.\n"
        "If the tool evidence shows an action (credit, refund, ticket) was ALREADY "
        "taken before this conversation (e.g. an 'already_taken'/'already applied' "
        "field is true), then the reply stating that fact is SUPPORTED -- this is "
        "reporting a pre-existing record, not promising a new action.\n"
        "A reply that promises a NEW refund/credit will be applied when the evidence "
        "does not show one was requested or authorized in this turn, or that invents "
        "amounts/IDs/facts not in the evidence, is NOT supported.\n"
        f"TOOL EVIDENCE: {json.dumps(evidence, ensure_ascii=True, default=str)}\n"
        f"DRAFT REPLY: {text}\n"
        'Return ONLY JSON: {"supported": true/false, "unsupported_claims": ["..."]}'
    )
    try:
        # Same thinking-token headroom issue as elsewhere in this file: a JSON
        # response truncated mid-object raises JSONDecodeError below, which is
        # swallowed by the except clause and silently disables the self-check
        # (always "supported"), so this needs the same generous budget already
        # applied to intent classification and translation.
        raw = verifier.generate(
            prompt, response_mime_type="application/json",
            temperature=0.0, max_output_tokens=2048)
        payload = json.loads(raw)
        supported = bool(payload.get("supported", True))
        claims = [str(c) for c in payload.get("unsupported_claims", []) if str(c).strip()]
        return supported, claims
    except Exception:
        # Never penalise on verifier failure; deterministic guards still apply.
        return True, []


def _action_trust_score(
    text: str,
    tool_results: list[dict[str, Any]],
    verifier: LLMClient | None,
) -> tuple[float, list[str]]:
    if not text or len(text.strip()) < 5:
        return 0.1, ["reply was empty or too short to act on"]
    score = 0.95
    issues: list[str] = []
    if _response_overclaims(text, tool_results):
        score -= 0.5
        issues.append("claims an action the tool evidence does not confirm")
    if _response_misses_evidence(text, tool_results):
        score -= 0.3
        issues.append("omits verified evidence the customer needs to see")
    supported, cove_issues = _cove_verify(text, tool_results, verifier)
    if not supported:
        score -= 0.4
        issues.extend(cove_issues or ["self-check found unsupported claims"])
    return max(0.0, min(1.0, round(score, 2))), issues


def _revision_prompt(base_prompt: str, prior_text: str, issues: list[str]) -> str:
    problem_list = "; ".join(issues) if issues else "unsupported or unclear claims"
    return (
        base_prompt
        + f"\n\nYour previous attempt was: '{prior_text}'\n"
        + f"It was flagged as untrustworthy because: {problem_list}.\n"
        "Rewrite the reply so every statement is grounded ONLY in the verified tool "
        "evidence. Do not claim any action is complete unless a tool result proves it. "
        "Keep it to at most 2 sentences and address the customer by first name."
    )


_LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "mr": "Marathi",
    "bn": "Bengali",
    "gu": "Gujarati",
    "pa": "Punjabi",
}


def _preferred_language(customer: dict[str, Any] | None) -> str:
    code = str((customer or {}).get("preferred_language") or "en").strip().lower()
    return code or "en"


def _language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(code, code.upper() or "English")


def _localize_response(
    text: str, customer: dict[str, Any] | None, llm: LLMClient | None
) -> tuple[str, str]:
    """Translate the final reply into the customer's preferred language.

    English customers (and any case where the LLM is unavailable or fails) keep
    the original English text, so the deterministic safety guards that ran on the
    English version are never bypassed. Names, IDs and amounts are preserved.

    Returns ``(text, language_code)`` where ``language_code`` reflects the
    language the returned text is actually in -- not just the customer's
    preference -- so the UI never labels an untranslated fallback as translated.
    """
    code = _preferred_language(customer)
    if code == "en" or not text.strip() or llm is None:
        return text, "en"
    language = _language_name(code)
    prompt = (
        f"Translate the following customer-support reply into {language}. "
        "Keep it natural and conversational for a telecom customer. "
        "Do NOT translate or alter proper names, account/invoice/request IDs, "
        "currency codes (INR), numbers, or dates. Return only the translated text "
        "with no preamble or quotes.\n\n"
        f"Reply: {text}"
    )
    try:
        # gemini-2.5-flash is a thinking model: it spends an unpredictable share
        # of max_output_tokens on internal reasoning before emitting visible
        # text (observed 1000-1500+ thinking tokens on a routine translation,
        # enough to blow past even a 4096 budget on some runs). Translation
        # gains nothing from chain-of-thought, so disable it outright rather
        # than trying to out-budget an unpredictable thinking phase.
        translated = llm.generate(
            prompt, response_mime_type="text/plain", temperature=0.2,
            max_output_tokens=2048, thinking_budget=0)
        translated = translated.strip()
    except Exception:
        return text, "en"
    return (translated, code) if translated else (text, "en")


def _maybe_build_handoff(
    customer: dict[str, Any] | None,
    message: str,
    emotion: str,
    intents: list[str],
    tools: list[dict[str, Any]],
    health_score: int,
    state: dict[str, Any],
    *,
    force: bool = False,
    force_reason: str | None = None,
) -> dict[str, Any] | None:
    """Decide whether to escalate to a human and, if so, package the context.

    Escalation must reflect how *this conversation* is going, not a static risk
    score: the detector treats high churn alone as a trigger, but every demo
    customer is high-churn, so churn-only escalation would fire on a plain
    "hi". We gate on real in-conversation distress (anger, collapsing health, or
    a frustrated customer juggling multiple unresolved issues) and only then use
    the backend detector to enrich the reason and customer-facing message.
    ``force`` lets the trust layer escalate when the model stays low-confidence.
    """
    churn = float((customer or {}).get("churn_score") or 0)
    # Health already folds in emotion + issue load (a frustrated customer with
    # several unresolved issues lands below 45), so it is the single severity
    # gate; anger and an explicit ask escalate regardless of score.
    distressed = emotion == "angry" or health_score < 45
    explicit_request = _explicit_handoff_request(message)
    if not distressed and not explicit_request and not force:
        return None
    try:
        detection = detect_handoff_triggers(
            health_score=health_score,
            sentiment=emotion,
            user_message=message,
            handoff_requested=explicit_request,
        )
    except Exception:
        detection = None

    triggers = detection.triggers if detection else []
    issue_labels = sorted({intent.replace("_", " ") for intent in intents
                           if intent != "general_query"})
    issue_summary = ", ".join(issue_labels) if issue_labels else "general support"
    try:
        customer_message = generate_handoff_customer_message(
            trigger_detection=detection,
            customer_name=_first_name(customer),
            issue_summary=issue_summary,
            estimated_wait="about 2 minutes",
        ).message
    except Exception:
        customer_message = (
            f"{_first_name(customer)}, I'm bringing in a human specialist who can "
            "take this further. They'll have the full context, so you won't need to repeat anything."
        )

    if triggers:
        reason = triggers[0].reason
        severity = detection.highest_severity
        trigger_codes = detection.trigger_codes
    elif emotion == "angry":
        reason = "customer is angry and needs a human touch"
        severity = "high"
        trigger_codes = ["anger"]
    elif force and not distressed:
        reason = force_reason or "low model confidence"
        severity = "medium"
        trigger_codes = ["low_trust"]
    else:
        reason = "conversation health at risk"
        severity = "medium"
        trigger_codes = ["health_at_risk"]

    context_card = {
        "customer_id": (customer or {}).get("customer_id"),
        "customer_name": (customer or {}).get("name"),
        "plan_name": (customer or {}).get("plan_name"),
        "risk_level": (customer or {}).get("risk_level"),
        "churn_score": churn,
        "emotion": emotion,
        "health_score": health_score,
        "issues": issue_labels,
        "last_message": message,
        "evidence": [t.get("summary") for t in tools if t.get("summary")],
        "turn_count": state.get("turn_count"),
    }
    return {
        "should_handoff": True,
        "reason": reason,
        "severity": severity,
        "trigger_codes": trigger_codes,
        "customer_message": customer_message,
        "context_card": context_card,
    }


def _sync_live_conversation_records(
    *,
    customer_id: str,
    session_id: str,
    message: str,
    final_text: str,
    intents: list[str],
    tool_results: list[dict[str, Any]],
    policy_results: list[dict[str, Any]],
    dag: dict[str, Any],
    health_score: float,
    relationship_start: int,
    relationship_end: int,
    handoff_required: bool,
    db_path: Path,
) -> str:
    case_id = f"case-{session_id}"
    now = _now_iso()
    relationship_delta = relationship_end - relationship_start
    messages = [
        {"role": "user", "content": message, "timestamp": now},
        {"role": "assistant", "content": final_text, "timestamp": now},
    ]
    tools_payload = _live_tool_payload(tool_results)
    evidence_payload = _live_evidence_payload(tool_results, policy_results)
    actions_payload = _live_action_payload(tool_results)
    policy_path = [str(node) for node in (dag.get("path") or [])]
    ujcs = dag.get("ujcs")
    policy_status = _live_policy_status(dag, handoff_required)
    final_status = "escalated" if handoff_required else "active"
    slots = {"customer_id": customer_id, "session_id": session_id}

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        existing = connection.execute(
            "SELECT messages, health_scores FROM conversations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        existing_messages = _json_loads(existing["messages"], []) if existing else []
        existing_health = _json_loads(existing["health_scores"], []) if existing else []
        merged_messages = [*existing_messages, *messages]
        merged_health = [*existing_health, {"score": health_score, "timestamp": now}]

        connection.execute(
            """
            INSERT INTO conversations(
                session_id,
                customer_id,
                messages,
                intents,
                slots,
                tools_called,
                health_scores,
                final_status,
                relationship_score_start,
                relationship_score_end,
                relationship_delta,
                completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                customer_id = excluded.customer_id,
                messages = excluded.messages,
                intents = excluded.intents,
                slots = excluded.slots,
                tools_called = excluded.tools_called,
                health_scores = excluded.health_scores,
                final_status = excluded.final_status,
                relationship_score_start = excluded.relationship_score_start,
                relationship_score_end = excluded.relationship_score_end,
                relationship_delta = excluded.relationship_delta,
                completed_at = excluded.completed_at
            """,
            (
                session_id,
                customer_id,
                json.dumps(merged_messages, ensure_ascii=True, default=str),
                json.dumps(list(dict.fromkeys(intents)), ensure_ascii=True),
                json.dumps(slots, ensure_ascii=True),
                json.dumps(tools_payload, ensure_ascii=True, default=str),
                json.dumps(merged_health, ensure_ascii=True, default=str),
                final_status,
                relationship_start,
                relationship_end,
                relationship_delta,
                now if final_status != "active" else None,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_logs(
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
                case_id,
                customer_id,
                session_id,
                json.dumps(tools_payload, ensure_ascii=True, default=str),
                json.dumps(evidence_payload, ensure_ascii=True, default=str),
                json.dumps(actions_payload, ensure_ascii=True, default=str),
                json.dumps(policy_path, ensure_ascii=True),
                float(ujcs) if isinstance(ujcs, (int, float)) else None,
                policy_status,
                health_score,
                int(handoff_required),
                now,
            ),
        )
    return case_id


def _json_loads(raw: Any, fallback: Any) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        return value if value is not None else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _live_tool_payload(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = []
    for tool in tool_results:
        name = str(tool.get("tool_name") or "").strip()
        if not name:
            continue
        payload.append({
            "tool_name": name,
            "name": name,
            "ok": tool.get("ok", True),
            "summary": tool.get("summary"),
            "result": tool.get("result") or {},
            "receipt_id": tool.get("receipt_id"),
        })
    return payload


def _live_evidence_payload(
    tool_results: list[dict[str, Any]],
    policy_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence = []
    for tool in tool_results:
        name = str(tool.get("tool_name") or "").strip()
        if not name:
            continue
        evidence.append({
            "source": name,
            "summary": tool.get("summary"),
            "result": tool.get("result") or {},
            "receipt_id": tool.get("receipt_id"),
        })
    for policy in policy_results:
        evidence.append({
            "source": "retrieve_policy",
            "policy_id": policy.get("policy_id"),
            "policy_name": policy.get("policy_name"),
            "confidence": policy.get("confidence"),
        })
    return evidence


def _live_action_payload(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions = []
    for tool in tool_results:
        name = str(tool.get("tool_name") or "")
        result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
        if name == "apply_credit" and result.get("credit_id"):
            actions.append({"action": "apply_credit", "credit_id": result["credit_id"]})
        elif name == "create_ticket" and result.get("ticket_id"):
            actions.append({"action": "create_ticket", "ticket_id": result["ticket_id"]})
        elif name == "create_cancellation_request" and result:
            actions.append({
                "action": "create_cancellation_request",
                "request_id": result.get("cancellation_request_id") or result.get("ticket_id"),
            })
        elif name == "generate_handoff_summary" and result.get("handoff_summary_id"):
            actions.append({
                "action": "generate_handoff_summary",
                "handoff_summary_id": result["handoff_summary_id"],
            })
    return actions


def _live_policy_status(dag: dict[str, Any], handoff_required: bool) -> str:
    if handoff_required:
        return "needs_review"
    if dag.get("policy_status") in {"pending", "compliant", "non_compliant", "needs_review"}:
        return str(dag["policy_status"])
    ujcs = dag.get("ujcs")
    if isinstance(ujcs, (int, float)) and ujcs >= 0.8:
        return "compliant"
    return "pending"


def _ensure_case_records(connection: sqlite3.Connection, *, customer_id: str, session_id: str) -> str:
    """Live chat has no case/audit trail by default; a handoff insert needs a
    conversations row and an audit_logs row to already exist (both are
    validated as foreign keys), so create them here if this is the session's
    first escalation."""
    case_id = f"case-{session_id}"
    connection.execute(
        "INSERT OR IGNORE INTO conversations(session_id, customer_id, messages) VALUES (?, ?, '[]')",
        (session_id, customer_id),
    )
    connection.execute(
        """
        INSERT INTO audit_logs (case_id, customer_id, session_id)
        VALUES (?, ?, ?)
        ON CONFLICT(case_id) DO NOTHING
        """,
        (case_id, customer_id, session_id),
    )
    return case_id


def _record_handoff_to_queue(
    *, customer_id: str, session_id: str, handoff: dict[str, Any], db_path: Path
) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        case_id = _ensure_case_records(
            connection, customer_id=customer_id, session_id=session_id)
    queue_entry = insert_human_handoff_queue(
        case_id=case_id,
        customer_id=customer_id,
        context_card=handoff.get("context_card") or {},
        handoff_reason=handoff.get("reason") or "conversation health at risk",
        db_path=db_path,
    )
    log_handoff_event_to_audit(
        case_id=case_id,
        customer_id=customer_id,
        session_id=session_id,
        queue_entry=queue_entry,
        customer_message=handoff.get("customer_message"),
        trigger_detection={"trigger_codes": handoff.get("trigger_codes") or []},
        db_path=db_path,
    )


def _explicit_handoff_request(message: str) -> bool:
    text = " ".join(message.strip().lower().split())
    phrases = (
        "human", "agent", "representative", "speak to someone", "talk to someone",
        "real person", "supervisor", "manager", "escalate",
    )
    return any(phrase in text for phrase in phrases)


def _first_name(customer: dict[str, Any] | None) -> str:
    if not customer:
        return "there"
    return str(customer.get("name") or customer.get("customer_name") or "there").split()[0]


def _health_score_for(
    emotion: str,
    intents: list[str],
    *,
    customer: dict[str, Any] | None = None,
    message: str = "",
    chat_state: dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    policy_results: list[dict[str, Any]] | None = None,
    intent_confidence: float | None = None,
) -> int:
    try:
        history = list((chat_state or {}).get("history") or [])
        health = compute_health_score(
            intent_confidence=max(0.0, min(1.0, float(intent_confidence or 0.0))),
            missing_info_risk=missing_info_risk_component(
                intents,
                _health_slots(customer=customer, message=message),
            ),
            sentiment_score=sentiment_score_component(
                [*history, {"role": "user", "content": message}]
            ),
            loop_penalty=loop_penalty_component(
                [*history, {"role": "user", "content": message}]
            ),
            knowledge_coverage=knowledge_coverage_component(
                tool_results or [],
                [{"score": result.get("confidence")} for result in (policy_results or [])],
            ),
        )
        return int(round(health.score))
    except Exception:
        return _fallback_health_score_for(emotion, intents)


def _health_slots(*, customer: dict[str, Any] | None, message: str) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    if customer:
        slots["customer_id"] = customer.get("customer_id")
        slots["location"] = customer.get("location")
        slots["plan_id"] = customer.get("plan_id")
    lowered = message.lower()
    if any(term in lowered for term in ("this month", "latest", "current bill")):
        slots["billing_period"] = "current"
    if any(term in lowered for term in ("refund", "credit")):
        slots["reason"] = message
    return {key: value for key, value in slots.items() if value}


def _fallback_health_score_for(emotion: str, intents: list[str]) -> int:
    score = 72
    if emotion in {"frustrated", "angry"}:
        score -= 18
    if "cancellation_intent" in intents:
        score -= 8
    if len(intents) > 2:
        score -= 6
    return max(18, min(92, score))


def _relationship_start(customer: dict[str, Any] | None) -> int:
    if not customer:
        return 50
    churn = float(customer.get("churn_score") or 0)
    return max(20, min(84, round(84 - (churn * 65))))


def _relationship_end(customer: dict[str, Any] | None, intents: list[str], emotion: str) -> int:
    start = _relationship_start(customer)
    if emotion == "angry":
        return min(92, start + 5)
    if emotion == "frustrated":
        return min(92, start + 8)
    return min(92, start + (14 if len(intents) > 1 else 8))


def _empathy_mode_for(emotion: str, relationship_start: int) -> str:
    if emotion == "angry":
        return "ANGER_REPAIR"
    if emotion == "frustrated" or relationship_start < 40:
        return "CASA_AT_RISK"
    return "STANDARD"
