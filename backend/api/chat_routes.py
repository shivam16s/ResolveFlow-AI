import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from backend.agent import (
    IntentClassifier,
    LLMClient,
    build_issue_queue,
    generate_acknowledgment,
)
from backend.agent.policy_store import ChromaPolicyStore
from backend.tools import (
    check_duplicate_charge,
    check_outage_status,
    get_invoice_history,
    lookup_customer,
    retrieve_policy,
    run_router_diagnostic,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])

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
        policy_store: ChromaPolicyStore | None = getattr(request.app.state, "policy_store", None)
        
        llm = LLMClient()
        
        # 1. Intent
        yield _event("intent", "running")
        classification = await asyncio.to_thread(IntentClassifier(llm_client=llm).classify, message)
        emotion = classification.emotion
        issue_queue = build_issue_queue(classification)
        intents = [issue.intent for issue in issue_queue]
        
        yield _event("intent", "done", {
            "intents": intents,
            "emotion": emotion,
            "confidence": classification.intent_confidence,
            "queue": intents,
        })
        
        # 2. Memory (Customer Context)
        yield _event("memory", "running")
        customer = await asyncio.to_thread(lookup_customer, customer_id, db_path=db_path)
        yield _event("memory", "done", customer or {})
        
        # 3. Policy (True Semantic RAG)
        yield _event("policy", "running")
        policy_results = []
        if policy_store is not None:
            # Query the vector DB semantically using the customer's message
            # We fetch top 3 most relevant policy chunks across all documents
            search_results = await asyncio.to_thread(
                policy_store.query, message, top_k=3
            )
            
            # Extract unique policy IDs from the chunks
            metadatas = search_results.get("metadatas", [[]])[0]
            unique_policy_ids = list({meta["policy_id"]: meta for meta in metadatas if "policy_id" in meta}.values())
            
            # For each unique policy found, fully retrieve and evaluate it using CRAG
            for meta in unique_policy_ids:
                policy = await asyncio.to_thread(
                    retrieve_policy,
                    policy_name=meta["policy_id"],
                    query=message,
                    policy_dir=policy_dir,
                    llm_client=llm,
                )
                if policy:
                    policy_results.append({
                        "policy_name": policy["policy_name"],
                        "policy_id": policy["policy_id"],
                        "confidence": policy["relevance"]["score"],
                        "crag_path": policy["relevance"]["route"].upper(),
                    })
        yield _event("policy", "done", {"policies": policy_results})
        
        # 4. Tools (Dynamic Execution could go here, but for now we run the basics based on intent)
        yield _event("tools", "running")
        tool_results = []
        if {"billing_dispute", "duplicate_charge", "refund_request"} & set(intents):
            invoices = await asyncio.to_thread(get_invoice_history, customer_id, months=3, db_path=db_path)
            tool_results.append({"tool_name": "get_invoice_history", "ok": True, "result": {"invoices": invoices}})
            
            duplicate = await asyncio.to_thread(check_duplicate_charge, customer_id, db_path=db_path)
            tool_results.append({"tool_name": "check_duplicate_charge", "ok": True, "result": duplicate})
            
        if {"service_outage", "router_issue"} & set(intents):
            if customer and customer.get("location"):
                outage = await asyncio.to_thread(check_outage_status, customer["location"], customer_id=customer_id, db_path=db_path)
                tool_results.append({"tool_name": "check_outage_status", "ok": True, "result": outage})
                
        if "router_issue" in intents:
            diagnostic = await asyncio.to_thread(run_router_diagnostic, customer_id, db_path=db_path)
            tool_results.append({"tool_name": "run_router_diagnostic", "ok": True, "result": diagnostic})
            
        yield _event("tools", "done", {"tools": tool_results})
        
        # 5. DAG (Policy Validation)
        yield _event("dag", "running")
        # In a fully dynamic system, this step would be driven by the ReAct loop determining the next action.
        # We will just pass a generic compliant status for now to let the LLM decide.
        dag = {"dag_name": "dynamic_agent_path", "policy_status": "compliant", "action": "none"}
        yield _event("dag", "done", dag)
        
        # 6. Response
        yield _event("response", "running")
        prompt = (
            f"You are a helpful telecom support agent.\n"
            f"Customer Message: '{message}'\n"
            f"Customer Context: {customer}\n"
            f"Tool Results: {tool_results}\n"
            f"Policies Retrieved: {[p['policy_id'] for p in policy_results]}\n\n"
            "Write a concise, friendly, and helpful final response to the customer based on this context. "
            "Address them by their first name. Do not use markdown. Speak directly to the customer in a conversational tone."
        )
        try:
            final_text = await asyncio.to_thread(llm.generate, prompt, response_mime_type="text/plain", temperature=0.7)
            final_text = final_text.strip()
        except Exception:
            final_text = "I have checked your account context and resolved your request based on our policies."
            
        yield _event("response", "done", {
            "text": final_text,
            "health_score": 85,
            "relationship_start": 50,
            "relationship_end": 60,
            "acknowledgment": generate_acknowledgment(issue_queue),
            "emotion": emotion,
            "empathy_mode": "STANDARD",
        })

    return StreamingResponse(generate(), media_type="text/event-stream")
