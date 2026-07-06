from __future__ import annotations

import re
import sqlite3
from typing import Any

import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.agent.memory_manager import MemoryManager
from backend.tools import retrieve_policy


rag_router = APIRouter(prefix="/api/rag", tags=["rag"])


class MemorySearchRequest(BaseModel):
    customer_id: str
    query: str
    top_k: int = 5
    memory_type: str | None = None


class PolicyRetrieveRequest(BaseModel):
    query: str
    policy_name: str
    top_k: int = 3


@rag_router.post("/memory/search")
def memory_search(payload: MemorySearchRequest, request: Request) -> JSONResponse:
    try:
        manager = _memory_manager_for_request(request)
        results = manager.retrieve(
            query=payload.query,
            customer_id=payload.customer_id,
            top_k=payload.top_k,
            memory_type=payload.memory_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload_results = [r.to_dict() for r in results]
    if not payload_results:
        # The vector/graph stores are only populated after the (optional) demo
        # indexing step. So the knowledge-base search is never empty out of the
        # box, fall back to a keyword search over the seeded memory_store table.
        payload_results = _memory_store_fallback(
            db_path=request.app.state.db_path,
            customer_id=payload.customer_id,
            query=payload.query,
            top_k=payload.top_k,
            memory_type=payload.memory_type,
        )

    return JSONResponse({
        "results": payload_results,
        "query": payload.query,
        "customer_id": payload.customer_id
    })


def _memory_manager_for_request(request: Request) -> MemoryManager:
    db_path = request.app.state.db_path
    manager = getattr(request.app.state, "memory_manager", None)
    manager_db_path = getattr(request.app.state, "memory_manager_db_path", None)
    if isinstance(manager, MemoryManager) and manager_db_path == str(db_path):
        return manager
    if manager is not None and hasattr(manager, "close"):
        manager.close()

    manager = MemoryManager(db_path=db_path)
    request.app.state.memory_manager = manager
    request.app.state.memory_manager_db_path = str(db_path)
    return manager


def _memory_store_fallback(
    *, db_path: Any, customer_id: str, query: str, top_k: int, memory_type: str | None
) -> list[dict[str, Any]]:
    terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) >= 3]
    sql = (
        "SELECT memory_id, memory_type, content, entity_tags, updated_at "
        "FROM memory_store WHERE customer_id = ?"
    )
    params: list[Any] = [customer_id]
    if memory_type:
        sql += " AND memory_type = ?"
        params.append(memory_type)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                sql + " ORDER BY datetime(updated_at) DESC", params).fetchall()
    except sqlite3.Error:
        return []

    scored = []
    for row in rows:
        content = (row["content"] or "").lower()
        overlap = sum(1 for term in terms if term in content) if terms else 0
        scored.append((overlap, row))
    # Keyword matches first; fall back to most-recent memories when nothing matches
    # so the panel still shows the customer's known history.
    # Two stable sorts: recency (newest first), then overlap — ISO timestamps
    # sort lexicographically, and unary minus doesn't apply to strings.
    scored.sort(key=lambda item: item[1]["updated_at"] or "", reverse=True)
    scored.sort(key=lambda item: item[0], reverse=True)

    results = []
    for rank, (overlap, row) in enumerate(scored[:top_k], start=1):
        relevance = round(overlap / len(terms), 4) if terms else 0.0
        results.append({
            "memory_id": row["memory_id"],
            "document": row["content"],
            "metadata": {
                "memory_type": row["memory_type"],
                "entity_tags": _loads_json(row["entity_tags"]),
                "updated_at": row["updated_at"],
            },
            "fused_score": round(relevance * 0.5 + 0.1, 6),
            "sources": ["memory_store"],
            "vector_rank": rank,
            "graph_rank": None,
            "vector_score": relevance,
            "graph_score": None,
            "supporting_nodes": [],
            "query_nodes": terms[:5],
        })
    return results


@rag_router.post("/policy/retrieve")
def policy_retrieve(payload: PolicyRetrieveRequest, request: Request) -> JSONResponse:
    try:
        result = retrieve_policy(
            policy_name=payload.policy_name,
            query=payload.query,
            top_k=payload.top_k,
            policy_dir=request.app.state.policy_dir,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not result:
        raise HTTPException(
            status_code=404, detail=f"policy {payload.policy_name!r} not found or no results")

    return JSONResponse({
        "results": [result],
        "query": payload.query,
        "policy_name": payload.policy_name
    })


@rag_router.get("/memory/graph/{customer_id}")
def memory_graph(customer_id: str, request: Request) -> JSONResponse:
    try:
        from backend.agent.memory_graph import list_memory_graph_nodes
        db_path = request.app.state.db_path
        with sqlite3.connect(db_path) as conn:
            graph_nodes = list_memory_graph_nodes(conn, customer_id)

            nodes = []
            edges = []
            for node in graph_nodes:
                nodes.append({
                    "node_id": node["node_id"],
                    "label": node["label"],
                    "node_type": node["node_type"],
                    "supporting_passages": node["passages"]
                })

                for edge in node["edges"]:
                    edges.append({
                        "source": node["node_id"],
                        "target": edge.get("target_node"),
                        "relation": edge.get("relation"),
                        "weight": edge.get("weight")
                    })

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse({
        "customer_id": customer_id,
        "nodes": nodes,
        "edges": edges
    })


@rag_router.get("/customers")
def list_customers(request: Request) -> JSONResponse:
    try:
        db_path = request.app.state.db_path
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT customer_id, name, risk_level FROM customers ORDER BY name").fetchall()
            customers = [dict(row) for row in rows]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse({"customers": customers})


def _loads_json(value: Any) -> Any:
    if not value:
        return []
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []
    return value
