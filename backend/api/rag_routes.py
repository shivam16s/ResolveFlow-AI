from __future__ import annotations

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
        manager = MemoryManager(db_path=request.app.state.db_path)
        results = manager.retrieve(
            query=payload.query,
            customer_id=payload.customer_id,
            top_k=payload.top_k,
            memory_type=payload.memory_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return JSONResponse({
        "results": [r.to_dict() for r in results],
        "query": payload.query,
        "customer_id": payload.customer_id
    })


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
