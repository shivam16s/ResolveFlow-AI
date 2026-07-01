import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
    load_taken_actions,
)
from backend.agent.policy_store import ChromaPolicyStore
from backend.agent.llm_client import GeminiClientError
from backend.tools import (
    check_duplicate_charge,
    check_outage_status,
    create_ticket,
    get_invoice_history,
    lookup_customer,
    retrieve_policy,
    run_router_diagnostic,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])
_CHAT_STATES: dict[str, dict[str, Any]] = {}
_MEMORY_CANCELLATION_REQUESTS: dict[str, dict[str, Any]] = {}


def _event(step: str, status: str, result: dict[str, Any] | None = None) -> str:
    payload = {"step": step, "status": status, "result": result or {}}
    return f"data: {json.dumps(payload, ensure_ascii=True)}\n\n"


@router.get("/message/stream")
def chat_message_stream(
    request: Request,
    customer_id: str = Query(..., min_length=1),
    message: str = Query(..., min_length=1),
) -> StreamingResponse:
    async def generate():
        db_path = Path(request.app.state.db_path)
        policy_dir = Path(request.app.state.policy_dir)
        policy_store: ChromaPolicyStore | None = getattr(
            request.app.state, "policy_store", None)

        llm = _safe_llm_client()
        classifier_llm = _safe_classifier_client()
        chat_state = _load_session_state(customer_id, db_path)
        chat_state["turn_count"] = int(chat_state.get("turn_count", 0)) + 1
        backend_intent = _normalize_chat_intent(message, chat_state)

        queue = asyncio.Queue()

        # Each pipeline task MUST emit exactly one "running" and one "done" event,
        # even on failure. The drain loop below reads a fixed number of events;
        # if a task raised before its "done" the loop would block forever, hanging
        # the SSE connection. The try/except guarantees the "done" is always sent
        # so a tool/DB error degrades gracefully instead of deadlocking the stream.
        async def _run_intent():
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
                payload = {
                    "intents": res_intents,
                    "latest_intent": backend_intent or classification.primary_intent,
                    "emotion": emotion,
                    "confidence": classification.intent_confidence,
                    "queue": res_intents,
                }
                await queue.put(_event("intent", "done", payload))
                return res_intents, emotion, issue_queue
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
                    "queue": fallback_intents,
                }))
                return fallback_intents, emotion, issue_queue

        async def _run_memory():
            await queue.put(_event("memory", "running"))
            try:
                res_customer = await asyncio.to_thread(lookup_customer, customer_id, db_path=db_path)
            except Exception:
                res_customer = None
            await queue.put(_event("memory", "done", res_customer or {}))
            return res_customer

        async def _run_policy():
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
            return res_policy_results

        # Start background tasks
        intent_task = asyncio.create_task(_run_intent())
        memory_task = asyncio.create_task(_run_memory())
        policy_task = asyncio.create_task(_run_policy())

        # Yield events as they come in until all 3 "done" events have been yielded (i.e. 6 events total)
        for _ in range(6):
            yield await queue.get()

        # Await the actual results to use them downstream
        (intents, emotion, issue_queue), customer, policy_results = await asyncio.gather(
            intent_task, memory_task, policy_task
        )

        # 4. Tools (Dynamic Execution could go here, but for now we run the basics based on intent)
        yield _event("tools", "running")
        tool_results = []
        # A backend/DB fault while calling a tool must not kill the SSE stream:
        # keep whatever evidence was gathered and let the response stage degrade
        # gracefully rather than leaving the connection without a terminal event.
        try:
            if {"billing_dispute", "duplicate_charge", "refund_request"} & set(intents):
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

            if {"service_outage", "router_issue"} & set(intents):
                if customer and customer.get("location"):
                    outage = await asyncio.to_thread(check_outage_status, customer["location"], customer_id=customer_id, db_path=db_path)
                    tool_results.append({"tool_name": "check_outage_status", "ok": True,
                                        "summary": _outage_summary(outage), "result": outage})

            if "router_issue" in intents:
                diagnostic = await asyncio.to_thread(run_router_diagnostic, customer_id, db_path=db_path)
                tool_results.append({"tool_name": "run_router_diagnostic", "ok": True, "summary": str(
                    diagnostic.get("recommendation") or "diagnostic complete"), "result": diagnostic})

            if backend_intent == "cancellation_confirmation":
                cancellation_request = await asyncio.to_thread(_create_cancellation_request, customer_id, message, db_path)
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

        # 5. DAG (Policy Validation)
        yield _event("dag", "running")
        # In a fully dynamic system, this step would be driven by the ReAct loop determining the next action.
        # We will just pass a generic compliant status for now to let the LLM decide.
        dag = {"dag_name": "dynamic_agent_path", "policy_status": "compliant",
               "action": "none", "path": intents, "ujcs": 0.86 if tool_results else 0.0}
        yield _event("dag", "done", dag)

        # 6. Response
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
                draft = (await asyncio.to_thread(
                    llm.generate, base_prompt, response_mime_type="text/plain", temperature=0.7)).strip()
            except Exception:
                return _evidence_response(customer, intents, emotion, tool_results), 0.9, "fallback", []
            score, issues = await asyncio.to_thread(_action_trust_score, draft, tool_results, classifier_llm)
            if score >= _TRUST_THRESHOLD:
                return draft, score, "proceed", issues
            try:
                revised = (await asyncio.to_thread(
                    llm.generate, _revision_prompt(base_prompt, draft, issues),
                    response_mime_type="text/plain", temperature=0.4)).strip()
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
            _abort_cancellation(chat_state, customer_id, db_path)
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
        health_score = _health_score_for(emotion, intents)
        handoff = _maybe_build_handoff(
            customer, message, emotion, intents, tool_results, health_score, chat_state,
            force=force_handoff,
            force_reason="low model confidence after self-revision" if force_handoff else None)

        # Feature: multi-language. Localize the final reply into the customer's
        # preferred language (English replies are left untouched). Runs AFTER the
        # overclaim/evidence guards so safety checks operate on the English text.
        final_text = await asyncio.to_thread(_localize_response, final_text, customer, llm)

        # Update chat history
        chat_history = chat_state.setdefault("history", [])
        chat_history.append({"role": "user", "content": message})
        chat_history.append({"role": "assistant", "content": final_text})
        chat_state["history"] = chat_history[-10:]

        # Persist the updated session so multi-turn context survives a restart.
        _save_session_state(customer_id, chat_state, db_path)

        yield _event("response", "done", {
            "text": final_text,
            "health_score": health_score,
            "relationship_start": _relationship_start(customer),
            "relationship_end": _relationship_end(customer, intents, emotion),
            "acknowledgment": generate_acknowledgment(issue_queue),
            "emotion": emotion,
            "empathy_mode": _empathy_mode_for(emotion, _relationship_start(customer)),
            "language": _language_name(_preferred_language(customer)),
            "handoff": handoff,
            "trust": {
                "score": trust_score,
                "action": trust_action,
                "issues": trust_issues,
                "threshold": _TRUST_THRESHOLD,
            },
            "verified_claims": _verified_claims(tool_results),
            "conversation_state": chat_state,
        })

    return StreamingResponse(generate(), media_type="text/event-stream")


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
    if any(term in text for term in anger_terms):
        return "angry"
    return classifier_emotion


def _duplicate_summary(duplicate: dict[str, Any]) -> str:
    amount = duplicate.get("duplicate_amount")
    if duplicate.get("duplicate_confirmed") and amount:
        return f"duplicate found INR {float(amount):g}"
    return "no confirmed duplicate"


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


def _state_for(customer_id: str) -> dict[str, Any]:
    return _CHAT_STATES.setdefault(
        customer_id,
        {
            "customer_id": customer_id,
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
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_CHAT_STATE_TABLE} (
            customer_id TEXT PRIMARY KEY,
            state_json  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
        """
    )


def _read_state_row(customer_id: str, db_path: Path | None) -> dict[str, Any] | None:
    if not db_path:
        return None
    try:
        with sqlite3.connect(db_path) as connection:
            _ensure_chat_state_table(connection)
            row = connection.execute(
                f"SELECT state_json FROM {_CHAT_STATE_TABLE} WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()
        if not row:
            return None
        value = json.loads(row[0])
        return value if isinstance(value, dict) else None
    except (sqlite3.Error, json.JSONDecodeError, OSError, TypeError):
        return None


def _load_session_state(customer_id: str, db_path: Path | None) -> dict[str, Any]:
    """Return the live chat state, hydrating from the database on a cache miss.

    Within a process the in-memory cache is authoritative (we write through after
    every turn). After a restart the cache is empty, so the stored row is loaded
    back so the agent still knows what was already handled.
    """
    if customer_id in _CHAT_STATES:
        return _CHAT_STATES[customer_id]
    state = _state_for(customer_id)  # creates and caches the default
    stored = _read_state_row(customer_id, db_path)
    if stored:
        state.update(stored)
    return state


def _save_session_state(customer_id: str, state: dict[str, Any], db_path: Path | None) -> None:
    if not db_path:
        return
    try:
        payload = json.dumps(state, ensure_ascii=True, default=str)
    except (TypeError, ValueError):
        return
    try:
        with sqlite3.connect(db_path) as connection:
            _ensure_chat_state_table(connection)
            connection.execute(
                f"""
                INSERT INTO {_CHAT_STATE_TABLE} (customer_id, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(customer_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (customer_id, payload, _now_iso()),
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


def _abort_cancellation(state: dict[str, Any], customer_id: str | None = None, db_path: Path | None = None) -> None:
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
        _MEMORY_CANCELLATION_REQUESTS.pop(customer_id, None)


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


def _create_cancellation_request(customer_id: str, reason: str, db_path: Path) -> dict[str, Any]:
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
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

        ticket = create_ticket(
            customer_id,
            "cancellation_request",
            priority="high",
            status="open",
            db_path=db_path,
        )
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE customers SET account_status = 'pending_cancellation' WHERE customer_id = ?",
                (customer_id,),
            )
        return {
            "mode": "created",
            "customer_id": customer_id,
            "ticket_id": ticket["ticket_id"],
            "cancellation_request_id": _cancellation_id(ticket["ticket_id"]),
            "status": "created",
            "reason": reason,
            "service_active_until": "2026-07-04",
            "pending_credit_preserved": True,
        }
    except sqlite3.Error:
        return _create_memory_cancellation_request(customer_id, reason)


def _create_memory_cancellation_request(customer_id: str, reason: str) -> dict[str, Any]:
    existing = _MEMORY_CANCELLATION_REQUESTS.get(customer_id)
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
    _MEMORY_CANCELLATION_REQUESTS[customer_id] = request
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
    by_name = {str(tool.get("tool_name")): tool for tool in tools}
    duplicate = (by_name.get("check_duplicate_charge")
                 or {}).get("result") or {}
    outage = (by_name.get("check_outage_status") or {}).get("result") or {}
    replay = (by_name.get("apply_credit_guard") or {}).get("result") or {}

    parts = []
    if emotion == "angry":
        parts.append(
            f"{name}, I hear how frustrating this is. I will use the evidence already on the account instead of making you repeat everything.")
    elif emotion == "frustrated" or _relationship_start(customer) < 40:
        parts.append(
            f"{name}, I can see this has taken more effort than it should. I will keep this focused and verify each issue before actioning it.")
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
    else:
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
    return (
        f"You are a helpful telecom support agent.\n"
        f"{history_text}"
        f"Customer Message: '{message}'\n"
        f"Customer Context: {customer}\n"
        f"Tool Results: {tool_results}\n"
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
        "A reply that promises a refund/credit was applied, or invents amounts/IDs/"
        "facts not in the evidence, is NOT supported.\n"
        f"TOOL EVIDENCE: {json.dumps(evidence, ensure_ascii=True, default=str)}\n"
        f"DRAFT REPLY: {text}\n"
        'Return ONLY JSON: {"supported": true/false, "unsupported_claims": ["..."]}'
    )
    try:
        raw = verifier.generate(
            prompt, response_mime_type="application/json",
            temperature=0.0, max_output_tokens=512)
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


def _localize_response(text: str, customer: dict[str, Any] | None, llm: LLMClient | None) -> str:
    """Translate the final reply into the customer's preferred language.

    English customers (and any case where the LLM is unavailable or fails) keep
    the original English text, so the deterministic safety guards that ran on the
    English version are never bypassed. Names, IDs and amounts are preserved.
    """
    code = _preferred_language(customer)
    if code == "en" or not text.strip() or llm is None:
        return text
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
        translated = llm.generate(
            prompt, response_mime_type="text/plain", temperature=0.2)
        translated = translated.strip()
        return translated or text
    except Exception:
        return text


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


def _health_score_for(emotion: str, intents: list[str]) -> int:
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
