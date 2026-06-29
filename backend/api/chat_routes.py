import asyncio
import json
import sqlite3
from datetime import datetime, timezone
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
    generate_acknowledgment,
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
        chat_state = _load_session_state(customer_id, db_path)
        chat_state["turn_count"] = int(chat_state.get("turn_count", 0)) + 1
        backend_intent = _normalize_chat_intent(message, chat_state)

        queue = asyncio.Queue()

        async def _run_intent():
            await queue.put(_event("intent", "running"))
            classification = await asyncio.to_thread(_classify_message, message, llm)
            emotion = _effective_emotion(message, classification.emotion)
            issue_queue = build_issue_queue(classification)
            res_intents = [issue.intent for issue in issue_queue]
            if backend_intent in {"cancellation_request", "cancellation_confirmation"} and "cancellation_intent" not in res_intents:
                res_intents.append("cancellation_intent")

            payload = {
                "intents": res_intents,
                "latest_intent": backend_intent or classification.primary_intent,
                "emotion": emotion,
                "confidence": classification.intent_confidence,
                "queue": res_intents,
            }
            await queue.put(_event("intent", "done", payload))
            return res_intents, emotion, issue_queue

        async def _run_memory():
            await queue.put(_event("memory", "running"))
            res_customer = await asyncio.to_thread(lookup_customer, customer_id, db_path=db_path)
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
        if {"billing_dispute", "duplicate_charge", "refund_request"} & set(intents):
            invoices = await asyncio.to_thread(get_invoice_history, customer_id, months=3, db_path=db_path)
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
            _mark_cancellation_pending(chat_state, intents, pending_credits)

        yield _event("tools", "done", {"tools": tool_results})

        # 5. DAG (Policy Validation)
        yield _event("dag", "running")
        # In a fully dynamic system, this step would be driven by the ReAct loop determining the next action.
        # We will just pass a generic compliant status for now to let the LLM decide.
        dag = {"dag_name": "dynamic_agent_path", "policy_status": "compliant",
               "action": "none", "path": intents, "ujcs": 0.86 if tool_results else 0.0}
        yield _event("dag", "done", dag)

        # 6. Response
        yield _event("response", "running")
        if backend_intent == "greeting" and chat_state.get("active_flow") == "cancellation":
            final_text = _active_flow_greeting_response(customer, chat_state)
        elif backend_intent == "abort_cancellation":
            _abort_cancellation(chat_state)
            name = _first_name(customer)
            final_text = f"Okay {name}, I have stopped the cancellation process. Your service will remain active. Let me know if there's anything else I can help with!"
        elif backend_intent == "cancellation_confirmation":
            final_text = _cancellation_confirmation_response(
                customer, tool_results)
        elif "cancellation_intent" in intents:
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
                history_text = ""
                if chat_state.get("history"):
                    history_text = "Recent Conversation History:\n"
                    for turn in chat_state["history"][-6:]:
                        history_text += f"{turn['role'].capitalize()}: {turn['content']}\n"
                    history_text += "\n"

                prompt = (
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
                try:
                    if llm is None:
                        raise GeminiClientError("LLM unavailable")
                    final_text = await asyncio.to_thread(llm.generate, prompt, response_mime_type="text/plain", temperature=0.7)
                    final_text = final_text.strip()
                    if not final_text or _response_overclaims(final_text, tool_results) or _response_misses_evidence(final_text, tool_results):
                        final_text = _evidence_response(
                            customer, intents, emotion, tool_results)
                except Exception:
                    final_text = _evidence_response(
                        customer, intents, emotion, tool_results)

            _remember_findings(chat_state, current_findings)

        # Update chat history
        chat_history = chat_state.setdefault("history", [])
        chat_history.append({"role": "user", "content": message})
        chat_history.append({"role": "assistant", "content": final_text})
        chat_state["history"] = chat_history[-10:]

        # Persist the updated session so multi-turn context survives a restart.
        _save_session_state(customer_id, chat_state, db_path)

        yield _event("response", "done", {
            "text": final_text,
            "health_score": _health_score_for(emotion, intents),
            "relationship_start": _relationship_start(customer),
            "relationship_end": _relationship_end(customer, intents, emotion),
            "acknowledgment": generate_acknowledgment(issue_queue),
            "emotion": emotion,
            "empathy_mode": _empathy_mode_for(emotion, _relationship_start(customer)),
            "conversation_state": chat_state,
        })

    return StreamingResponse(generate(), media_type="text/event-stream")


def _safe_llm_client() -> LLMClient | None:
    try:
        return LLMClient()
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
    cancel_terms = ("cancel", "cancellation", "disconnect", "stop my subscription",
                    "stop service", "close my account", "cacel", "cancle")
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


def _abort_cancellation(state: dict[str, Any]) -> None:
    state.update({
        "active_flow": "none",
        "pending_customer_choice": None,
        "cancellation_request": None,
        "last_confirmed_intent": None,
        "last_bot_offer": [],
    })


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
        "subscription_status": row["account_status"],
        "contract_lockin": fee > 0,
        "cancellation_fee": fee,
        "billing_cycle_end": "2026-07-04",
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
                "service_active_until": "2026-07-04",
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
    lines = []
    if subscription.get("cancellation_allowed"):
        fee = float(subscription.get("cancellation_fee") or 0)
        if fee:
            lines.append(
                f"{name}, cancellation is allowed on your account. The current plan has an INR {fee:g} cancellation fee.")
        else:
            lines.append(
                f"{name}, cancellation is allowed on your account and there is no lock-in fee.")
    else:
        lines.append(
            f"{name}, I checked your subscription status and cancellation needs a specialist review before it can proceed.")

    pending_items = []
    amount = float(pending.get("pending_credit_amount") or 0)
    if amount:
        pending_items.append(f"INR {amount:g} outage credit is still eligible")
    if pending.get("duplicate_charge_refund_pending"):
        invoice = pending.get("duplicate_invoice_id")
        pending_items.append(
            f"a duplicate-charge review is pending{f' for invoice {invoice}' if invoice else ''}")
    if pending_items:
        lines.append("Before I proceed, I found pending account value: " +
                     "; ".join(pending_items) + ".")
        lines.append(
            "You can cancel now, or resolve the pending credit/refund first and then cancel. Which do you prefer?")
    else:
        lines.append(
            "I did not find pending credits blocking the request. Reply 'cancel' to create the cancellation request now.")
    return " ".join(lines)


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
        if not any(term in lowered for term in ("outage", "chennai", "7 hour", "7-hour", "service issue")):
            return True

    # Ensure the response is at least a minimum length to be considered valid
    if len(text.strip()) < 5:
        return True
    return False


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
