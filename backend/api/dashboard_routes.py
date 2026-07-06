from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

# NOTE: This module holds the dashboard controller *functions*. They are mounted
# as routes by backend/api/routes.py (the single routing surface) — there is no
# router here, so endpoints can only ever be registered in one place.
from fastapi import BackgroundTasks, HTTPException, Request

from backend.evaluation import (
    compute_business_adherence,
    evaluate_policy_retrievals_with_ragas,
    load_evaluation_scenarios,
    run_evaluation,
)
from backend.tools import generate_context_card
from backend.agent.llm_client import LLMClient, GeminiClientError

ISSUE_COLORS = ["#6366f1", "#10b981",
                "#f59e0b", "#ef4444", "#14b8a6", "#a855f7"]
HEALTH_BUCKETS = [
    ("0-29", 0, 29, "#ef4444"),
    ("30-49", 30, 49, "#f97316"),
    ("50-69", 50, 69, "#f59e0b"),
    ("70-89", 70, 89, "#10b981"),
    ("90-100", 90, 100, "#34d399"),
]
_POLICY_COMPLIANCE_PCT_CACHE: dict[tuple[str, int, int], float | None] = {}


def dashboard_overview(request: Request) -> dict[str, Any]:
    db_path = Path(request.app.state.db_path)
    with _connect(request.app.state.db_path) as connection:
        total_cases = _scalar(connection, "SELECT COUNT(*) FROM conversations")
        status_counts = _status_counts(connection)
        credit_row = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM credits").fetchone()
        ticket_count = _scalar(connection, "SELECT COUNT(*) FROM tickets")
        high_risk = _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM customers
            WHERE risk_level IN ('high', 'critical') OR churn_score >= 0.70
            """,
        )
        compliant = _scalar(
            connection, "SELECT COUNT(*) FROM audit_logs WHERE policy_status = 'compliant'")
        audit_total = _scalar(connection, "SELECT COUNT(*) FROM audit_logs")
        health_scores = _health_scores(connection)

    resolved = status_counts.get("resolved", 0)
    escalated = status_counts.get("escalated", 0)
    evaluation_policy_pct = _latest_policy_compliance_pct(db_path)
    return {
        "total_cases_today": total_cases,
        "resolved_by_ai_pct": _pct(resolved, total_cases),
        "escalated_pct": _pct(escalated, total_cases),
        "policy_compliant_pct": evaluation_policy_pct
        if evaluation_policy_pct is not None
        else _pct(compliant, audit_total),
        "credits_applied_count": int(credit_row[0] or 0),
        "credits_applied_total_inr": float(credit_row[1] or 0),
        "tickets_created": ticket_count,
        "high_risk_customers": high_risk,
        "avg_health_score": round(sum(health_scores) / len(health_scores), 1) if health_scores else 0,
    }


def dashboard_charts(request: Request) -> dict[str, Any]:
    with _connect(request.app.state.db_path) as connection:
        return {
            "resolution_trend": _resolution_trend(connection),
            "issue_distribution": _issue_distribution(connection),
            "tool_frequency": _tool_frequency(connection),
            "health_distribution": _health_distribution(connection),
        }


def list_cases(request: Request, page: int = 1, limit: int = 20) -> dict[str, Any]:
    page = max(1, page)
    limit = min(max(1, limit), 100)
    offset = (page - 1) * limit
    with _connect(request.app.state.db_path) as connection:
        total = _scalar(connection, "SELECT COUNT(*) FROM conversations")
        rows = connection.execute(
            """
            SELECT
                conv.session_id,
                conv.customer_id,
                conv.messages,
                conv.intents,
                conv.tools_called,
                conv.health_scores,
                conv.final_status,
                conv.relationship_score_start,
                conv.relationship_score_end,
                conv.created_at,
                cust.name AS customer_name,
                audit.case_id,
                audit.health_score AS audit_health_score,
                audit.handoff_required
            FROM conversations conv
            JOIN customers cust ON cust.customer_id = conv.customer_id
            LEFT JOIN audit_logs audit ON audit.session_id = conv.session_id
            ORDER BY datetime(conv.created_at) DESC, conv.session_id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    return {
        "cases": [_case_row(row) for row in rows],
        "total": total,
        "page": page,
        "limit": limit,
    }


def case_detail(case_id: str, request: Request) -> dict[str, Any]:
    with _connect(request.app.state.db_path) as connection:
        row = _case_detail_row(connection, case_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"case {case_id!r} not found")
        audit = _audit_row(connection, row["session_id"])
        memories = connection.execute(
            """
            SELECT memory_id, content, memory_type, created_at
            FROM memory_store
            WHERE customer_id = ?
            ORDER BY datetime(updated_at) DESC, memory_id DESC
            LIMIT 8
            """,
            (row["customer_id"],),
        ).fetchall()

    messages = _messages(row["messages"])
    health_timeline = _health_timeline(row["health_scores"], audit)
    tools_called = _tools_called(row["tools_called"], audit)
    policy_path = _policy_dag_path(audit)
    return {
        "case_id": audit["case_id"] if audit else row["session_id"],
        "customer_id": row["customer_id"],
        "customer_name": row["customer_name"],
        "status": _case_status(row["final_status"], audit),
        "messages": _message_payload(messages),
        "tools_called": tools_called,
        "policy_dag_path": policy_path,
        "health_score_timeline": health_timeline,
        "relationship_score_start": row["relationship_score_start"],
        "relationship_score_end": row["relationship_score_end"],
        "guided_action_events": _guided_action_events(tools_called, audit),
        "memory_citations": [_memory_citation(memory) for memory in memories],
        "policy_retrievals": _policy_retrievals(audit),
        "intents_detected": _json_list(row["intents"]),
        "final_health_score": _final_health_score(row, health_timeline, audit),
        "ujcs": audit["ujcs"] if audit else None,
        "created_at": row["created_at"],
    }


def case_context_card(case_id: str, request: Request) -> dict[str, Any]:
    with _connect(request.app.state.db_path) as connection:
        row = _case_detail_row(connection, case_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"case {case_id!r} not found")

    card = generate_context_card(
        row["session_id"], db_path=request.app.state.db_path)
    if card is not None:
        return card
    return {
        "case_id": case_id,
        "session_id": row["session_id"],
        "customer_id": row["customer_id"],
        "customer": {
            "customer_id": row["customer_id"],
            "name": row["customer_name"],
        },
        "issues_detected": _json_list(row["intents"]),
        "issues_resolved": [],
        "issues_remaining": _json_list(row["intents"]),
        "tools_called": _json_list(row["tools_called"]),
        "memory_context": [],
    }


def evaluation_results(request: Request) -> dict[str, Any]:
    latest = _latest_evaluation_file(_data_dir(request))
    if latest is None:
        evaluation = run_evaluation(k=1, db_path=request.app.state.db_path)
        run_at = datetime.now().isoformat()
        run_id = f"eval-live-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    else:
        evaluation = json.loads(latest.read_text(encoding="utf-8"))
        run_at = datetime.fromtimestamp(latest.stat().st_mtime).isoformat()
        run_id = latest.stem
    return _evaluation_report(evaluation, run_id=run_id, run_at=run_at, db_path=Path(request.app.state.db_path))


def evaluation_run(
    request: Request,
    background_tasks: BackgroundTasks | None = None,
    *,
    live_llm: bool = False,
) -> dict[str, Any]:
    run_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
    run_at = datetime.now().isoformat()
    output_path = _data_dir(request) / f"{run_id}.json"
    db_path = Path(request.app.state.db_path)
    if background_tasks is not None:
        background_tasks.add_task(
            _write_evaluation_run,
            db_path=db_path,
            output_path=output_path,
            live_llm=live_llm,
        )
        return {
            "job_id": run_id,
            "run_id": run_id,
            "status": "queued",
            "live_llm": live_llm,
            "result_path": str(output_path),
            "run_at": run_at,
        }

    result = _write_evaluation_run(db_path=db_path, output_path=output_path, live_llm=live_llm)
    return {
        "job_id": run_id,
        "run_id": run_id,
        "status": "completed",
        "live_llm": live_llm,
        "result_path": str(output_path),
        "summary": _evaluation_report(
            result,
            run_id=run_id,
            run_at=run_at,
            db_path=db_path,
        ),
    }


def _write_evaluation_run(*, db_path: Path, output_path: Path, live_llm: bool = False) -> dict[str, Any]:
    result = run_evaluation(
        k=5,
        db_path=db_path,
        use_live_llm=live_llm,
        temperature_schedule=[0.3, 0.45, 0.6, 0.75, 0.9] if live_llm else None,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(
        result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _scalar(connection: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = connection.execute(sql, params).fetchone()
    return int(row[0] or 0) if row is not None else 0


def _status_counts(connection: sqlite3.Connection) -> Counter[str]:
    rows = connection.execute(
        "SELECT final_status, COUNT(*) FROM conversations GROUP BY final_status").fetchall()
    return Counter({row[0]: int(row[1]) for row in rows})


def _resolution_trend(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    start = datetime.now() - timedelta(days=6)
    buckets = {
        (start + timedelta(days=offset)).strftime("%Y-%m-%d"): {"resolved": 0, "escalated": 0}
        for offset in range(7)
    }
    rows = connection.execute(
        """
        SELECT date(created_at) AS day, final_status, COUNT(*) AS count
        FROM conversations
        WHERE date(created_at) >= date('now', '-6 day')
        GROUP BY day, final_status
        """
    ).fetchall()
    for row in rows:
        if row["day"] in buckets and row["final_status"] in {"resolved", "escalated"}:
            buckets[row["day"]][row["final_status"]] = int(row["count"])
    return [
        {
            "date": datetime.fromisoformat(day).strftime("%d %b"),
            "resolved": values["resolved"],
            "escalated": values["escalated"],
        }
        for day, values in sorted(buckets.items())
    ]


def _issue_distribution(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for row in connection.execute("SELECT intents FROM conversations").fetchall():
        counter.update(_json_list(row["intents"]))
    for row in connection.execute("SELECT issue_type FROM tickets").fetchall():
        counter.update([row["issue_type"]])
    total = sum(counter.values())
    if total == 0:
        return []
    return [
        {
            "name": _label(name),
            "value": round((count / total) * 100, 1),
            "color": ISSUE_COLORS[index % len(ISSUE_COLORS)],
        }
        for index, (name, count) in enumerate(counter.most_common())
    ]


def _tool_frequency(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for row in connection.execute("SELECT tools_called FROM conversations").fetchall():
        counter.update(_tool_names(_json_list(row["tools_called"])))
    for row in connection.execute("SELECT tools_called FROM audit_logs").fetchall():
        counter.update(_tool_names(_json_list(row["tools_called"])))
    return [{"tool": name, "calls": count} for name, count in counter.most_common(10)]


def _health_distribution(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    scores = _health_scores(connection)
    rows = []
    for label, low, high, color in HEALTH_BUCKETS:
        rows.append(
            {
                "range": label,
                "count": sum(1 for score in scores if low <= score <= high),
                "color": color,
            }
        )
    return rows


def _health_scores(connection: sqlite3.Connection) -> list[float]:
    scores = []
    for row in connection.execute("SELECT health_scores, relationship_score_end FROM conversations").fetchall():
        for item in _json_list(row["health_scores"]):
            if isinstance(item, dict):
                value = item.get("score")
            else:
                value = item
            if isinstance(value, (int, float)):
                scores.append(float(value))
        if isinstance(row["relationship_score_end"], (int, float)):
            scores.append(float(row["relationship_score_end"]))
    for row in connection.execute("SELECT health_score FROM audit_logs WHERE health_score IS NOT NULL").fetchall():
        scores.append(float(row["health_score"]))
    return scores


def _case_row(row: sqlite3.Row) -> dict[str, Any]:
    messages = _messages(row["messages"])
    health_timeline = _health_timeline(row["health_scores"], None)
    return {
        "case_id": row["case_id"] or row["session_id"],
        "route_id": row["session_id"],
        "customer_name": row["customer_name"],
        "customer_id": row["customer_id"],
        "issues": [_label(item) for item in _json_list(row["intents"])],
        "status": _case_status(row["final_status"], row),
        "health_score": _final_health_score(row, health_timeline, row),
        "relationship_score_start": row["relationship_score_start"],
        "relationship_score_end": row["relationship_score_end"],
        "created_at": row["created_at"],
        "turns": len(messages),
    }


def _case_detail_row(connection: sqlite3.Connection, case_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT
            conv.*,
            cust.name AS customer_name,
            cust.location,
            cust.risk_level,
            cust.churn_score
        FROM conversations conv
        JOIN customers cust ON cust.customer_id = conv.customer_id
        LEFT JOIN audit_logs audit ON audit.session_id = conv.session_id
        WHERE conv.session_id = ? OR audit.case_id = ?
        ORDER BY datetime(conv.created_at) DESC
        LIMIT 1
        """,
        (case_id, case_id),
    ).fetchone()


def _audit_row(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM audit_logs WHERE session_id = ? ORDER BY datetime(created_at) DESC LIMIT 1",
        (session_id,),
    ).fetchone()


def _messages(raw: str | None) -> list[dict[str, Any]]:
    messages = []
    for index, item in enumerate(_json_list(raw), start=1):
        if not isinstance(item, dict):
            continue
        messages.append(
            {
                "role": str(item.get("role") or "user"),
                "content": str(item.get("content") or ""),
                "timestamp": str(item.get("timestamp") or item.get("created_at") or datetime.now().isoformat()),
                "turn": int(item.get("turn") or index),
            }
        )
    return messages


def _message_payload(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return messages


def _tools_called(raw_tools: str | None, audit: sqlite3.Row | None) -> list[dict[str, Any]]:
    items = _json_list(raw_tools)
    if audit is not None:
        items.extend(_json_list(audit["tools_called"]))
    tools = []
    for item in items:
        if isinstance(item, str):
            tools.append(
                {
                    "tool_name": item,
                    "args": {},
                    "result": {},
                    "timestamp": datetime.now().isoformat(),
                    "success": True,
                }
            )
        elif isinstance(item, dict):
            tools.append(
                {
                    "tool_name": str(item.get("tool_name") or item.get("name") or "tool_call"),
                    "args": _dict_or_empty(item.get("args")),
                    "result": _dict_or_empty(item.get("result")),
                    "timestamp": str(item.get("timestamp") or datetime.now().isoformat()),
                    "success": item.get("status") != "failed" and item.get("success") is not False,
                }
            )
    deduped = []
    seen = set()
    for tool in tools:
        key = (tool["tool_name"], json.dumps(
            tool["args"], sort_keys=True), tool["timestamp"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(tool)
    return deduped


def _policy_dag_path(audit: sqlite3.Row | None) -> dict[str, Any] | None:
    if audit is None:
        return None
    path = _json_list(audit["policy_dag_path"])
    if not path:
        return None
    nodes = []
    edges = []
    for index, item in enumerate(path):
        node_id = str(item.get("node_id") or item.get("id") or item.get(
            "node") or item) if isinstance(item, dict) else str(item)
        nodes.append(
            {
                "node_id": node_id,
                "description": _label(node_id),
                "visited": True,
                "result": item.get("result") if isinstance(item, dict) else None,
            }
        )
        if index > 0:
            edges.append(
                {
                    "from": nodes[index - 1]["node_id"],
                    "to": node_id,
                    "label": "next",
                    "traversed": True,
                }
            )
    return {
        "dag_name": "policy_validation",
        "nodes": nodes,
        "edges": edges,
        "ujcs": audit["ujcs"] or 0,
        "action_taken": _action_summary(audit),
        "policy_status": audit["policy_status"],
    }


def _health_timeline(raw_scores: str | None, audit: sqlite3.Row | None) -> list[dict[str, Any]]:
    points = []
    for index, item in enumerate(_json_list(raw_scores), start=1):
        if isinstance(item, dict):
            score = item.get("score")
            label = str(item.get("label") or item.get(
                "reason") or "Conversation health")
            turn = int(item.get("turn") or index)
            sentiment_score = _timeline_sentiment_score(
                item.get("sentiment_score", item.get("sentiment")), label, score)
        else:
            score = item
            label = "Conversation health"
            turn = index
            sentiment_score = _timeline_sentiment_score(None, label, score)
        if isinstance(score, (int, float)):
            points.append(
                {
                    "turn": turn,
                    "score": float(score),
                    "label": label,
                    "sentiment_score": sentiment_score,
                    "sentiment_label": _timeline_sentiment_label(sentiment_score),
                }
            )
    if not points and audit is not None and isinstance(audit["health_score"], (int, float)):
        score = float(audit["health_score"])
        sentiment_score = _timeline_sentiment_score(None, "Audit health score", score)
        points.append({
            "turn": 1,
            "score": score,
            "label": "Audit health score",
            "sentiment_score": sentiment_score,
            "sentiment_label": _timeline_sentiment_label(sentiment_score),
        })
    return points


def _timeline_sentiment_score(raw_value: Any, label: str, health_score: Any) -> float:
    if isinstance(raw_value, (int, float)):
        score = float(raw_value)
        return round(max(0.0, min(1.0, score / 100 if score > 1 else score)), 2)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        label_scores = {
            "angry": 0.1,
            "hostile": 0.1,
            "frustrated": 0.25,
            "negative": 0.3,
            "neutral": 0.65,
            "calm": 0.78,
            "positive": 0.9,
        }
        if normalized in label_scores:
            return label_scores[normalized]
    text = label.lower()
    if any(term in text for term in ("angry", "furious", "hostile", "cancellation")):
        return 0.12
    if any(term in text for term in ("loop", "waiting", "risk", "frustrated")):
        return 0.35
    if any(term in text for term in ("acknowledged", "corrected", "verified", "resolved", "recovered")):
        return 0.78
    if isinstance(health_score, (int, float)):
        return round(max(0.0, min(1.0, float(health_score) / 100)), 2)
    return 0.65


def _timeline_sentiment_label(score: float) -> str:
    if score < 0.25:
        return "angry"
    if score < 0.5:
        return "frustrated"
    if score < 0.75:
        return "neutral"
    return "positive"


def _guided_action_events(tools: list[dict[str, Any]], audit: sqlite3.Row | None) -> list[dict[str, Any]]:
    events = []
    for index, tool in enumerate(tools, start=1):
        state = "RESOLVED" if tool["success"] else "FAILED"
        events.append(
            {
                "state": state,
                "reason": tool["tool_name"],
                "attempt": 1,
                "timestamp": tool["timestamp"],
            }
        )
    if audit is not None and audit["handoff_required"]:
        events.append(
            {
                "state": "ESCALATED",
                "reason": "Human handoff required",
                "attempt": 1,
                "timestamp": audit["created_at"],
            }
        )
    return events


def _memory_citation(memory: sqlite3.Row) -> dict[str, Any]:
    return {
        "citation_id": memory["memory_id"],
        "content": memory["content"],
        "type": memory["memory_type"],
        "timestamp": memory["created_at"],
        "confidence": 1.0,
    }


def _policy_retrievals(audit: sqlite3.Row | None) -> list[dict[str, Any]]:
    if audit is None:
        return []
    retrievals = []
    for evidence in _json_list(audit["evidence_used"]):
        text = json.dumps(evidence, sort_keys=True) if isinstance(
            evidence, dict) else str(evidence)
        retrievals.append(
            {
                "policy_name": "Audit evidence",
                "chunk": text,
                "confidence": audit["ujcs"] if audit["ujcs"] is not None else 0.5,
                "crag_path": "CORRECT" if audit["policy_status"] == "compliant" else "AMBIGUOUS",
            }
        )
    return retrievals[:8]


def _final_health_score(row: sqlite3.Row, timeline: list[dict[str, Any]], audit: sqlite3.Row | None) -> float:
    if audit is not None and "audit_health_score" in row.keys() and isinstance(row["audit_health_score"], (int, float)):
        return float(row["audit_health_score"])
    if audit is not None and "health_score" in audit.keys() and isinstance(audit["health_score"], (int, float)):
        return float(audit["health_score"])
    if timeline:
        return float(timeline[-1]["score"])
    if "relationship_score_end" in row.keys() and isinstance(row["relationship_score_end"], (int, float)):
        return float(row["relationship_score_end"])
    if "churn_score" in row.keys() and isinstance(row["churn_score"], (int, float)):
        return round((1.0 - float(row["churn_score"])) * 100, 1)
    return 0


def _case_status(final_status: str, audit: sqlite3.Row | None) -> str:
    if audit is not None and "handoff_required" in audit.keys() and audit["handoff_required"]:
        return "escalated"
    if final_status == "resolved":
        return "resolved"
    if final_status == "escalated":
        return "escalated"
    if final_status == "abandoned":
        return "open"
    return "in_progress"


def _evaluation_report(
    evaluation: dict[str, Any],
    *,
    run_id: str,
    run_at: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    results = [item for item in evaluation.get(
        "results", []) if isinstance(item, dict)]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[str(result.get("scenario_id") or "unknown")].append(result)

    scenario_policy_scores = _scenario_policy_scores(results)
    # Compute the RAGAS report once and derive real per-scenario context_recall and
    # context-precision means from it. Scenarios that never retrieve a policy have
    # no RAGAS score and are reported as null (n/a) rather than an invented value.
    ragas_report = _ragas_report(evaluation)
    ragas_context_scores = _ragas_scenario_means(ragas_report, "context_precision")
    ragas_context_recall_scores = _ragas_scenario_means(ragas_report, "context_recall")
    scenarios = []
    for scenario_id, items in sorted(grouped.items()):
        passed = sum(1 for item in items if item.get("passed") is True)
        policy_compliance = scenario_policy_scores.get(scenario_id, 1.0)
        context_precision = ragas_context_scores.get(scenario_id)
        context_recall = ragas_context_recall_scores.get(scenario_id)
        scenarios.append(
            {
                "case_id": scenario_id,
                "scenario_name": _label(scenario_id),
                "pass_k": round(passed / len(items), 4),
                "avg_turns": _average_customer_turns(items),
                "policy_compliance": round(policy_compliance, 4),
                "ragas_context_recall": round(context_recall, 4) if context_recall is not None else None,
                "ragas_context_precision": round(context_precision, 4) if context_precision is not None else None,
                "status": "pass" if passed == len(items) else "partial" if passed > 0 else "fail",
            }
        )

    pass_rate = float(evaluation.get("success_rate") or 0)
    policy_compliance = (
        round(sum(item["policy_compliance"]
              for item in scenarios) / len(scenarios), 4)
        if scenarios
        else None
    )
    if policy_compliance is None and db_path is not None:
        policy_compliance = _policy_compliance_from_audit_logs(db_path)
    if policy_compliance is None:
        policy_compliance = 0
    ragas_context_recall = float(ragas_report.get("average_context_recall") or 0)
    ragas_context_precision = float(ragas_report.get("average_context_precision") or 0)
    return {
        "run_id": run_id,
        "run_at": run_at,
        "total_scenarios": int(evaluation.get("scenario_count") or len(scenarios)),
        "pass_rate": pass_rate,
        "avg_pass_k": round(sum(item["pass_k"] for item in scenarios) / len(scenarios), 4) if scenarios else 0,
        "avg_policy_compliance": round(policy_compliance, 4),
        "avg_ragas_context_recall": round(ragas_context_recall, 4),
        "avg_ragas_context_precision": round(ragas_context_precision, 4),
        "business_adherence": _business_adherence_report(evaluation),
        "temperature_results": _temperature_results(results),
        "scenarios": scenarios,
    }


def _temperature_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        temperature = result.get("temperature")
        label = "deterministic" if temperature is None else f"{float(temperature):.2f}"
        grouped[label].append(result)
    rows = []
    for label, items in sorted(grouped.items()):
        if not items:
            continue
        passed = sum(1 for item in items if item.get("passed") is True)
        temperatures = [
            item.get("temperature")
            for item in items
            if isinstance(item.get("temperature"), (int, float))
        ]
        rows.append({
            "temperature": None if label == "deterministic" else round(float(label), 2),
            "label": label,
            "runs": len(items),
            "pass_rate": round(passed / len(items), 4),
            "avg_score": round(sum(float(item.get("score") or 0) for item in items) / len(items), 4),
            "pass_indices": sorted({int(item.get("pass_index") or 0) for item in items}),
            "source": "live_llm" if temperatures else "deterministic",
        })
    return rows


def _business_adherence_report(evaluation: dict[str, Any]) -> dict[str, Any] | None:
    """Beyond-IVR business-adherence score derived from the run (best-effort)."""
    try:
        return compute_business_adherence(evaluation)
    except Exception:
        return None


def _scenario_policy_scores(results: list[dict[str, Any]]) -> dict[str, float]:
    try:
        scenario_by_id = {
            scenario.scenario_id: scenario for scenario in load_evaluation_scenarios()}
    except (OSError, ValueError):
        return {}

    grouped: dict[str, list[float]] = defaultdict(list)
    for result in results:
        scenario_id = str(result.get("scenario_id") or "")
        scenario = scenario_by_id.get(scenario_id)
        if scenario is None:
            continue
        required = list(scenario.goal_state.get("required_policies", []))
        if not required:
            grouped[scenario_id].append(1.0)
            continue
        retrieved = set(result.get("policies_retrieved") or [])
        grouped[scenario_id].append(
            sum(1 for policy in required if policy in retrieved) / len(required))
    return {
        scenario_id: round(sum(scores) / len(scores), 4)
        for scenario_id, scores in grouped.items()
        if scores
    }


def _ragas_report(evaluation: dict[str, Any]) -> dict[str, Any]:
    try:
        return evaluate_policy_retrievals_with_ragas(evaluation)
    except (OSError, ValueError):
        return {"average_context_recall": 0.0, "average_context_precision": 0.0, "scores": []}


def _ragas_scenario_means(ragas_report: dict[str, Any], key: str) -> dict[str, float]:
    """Per-scenario mean of a RAGAS metric (context_recall or context_precision).

    Scenarios with no policy retrieval simply do not appear, so the caller can
    report them as n/a instead of substituting a placeholder value.
    """
    grouped: dict[str, list[float]] = defaultdict(list)
    for score in ragas_report.get("scores", []):
        if not isinstance(score, dict):
            continue
        scenario_id = str(score.get("scenario_id") or "")
        if not scenario_id:
            continue
        value = score.get(key)
        if value is not None:
            grouped[scenario_id].append(float(value))
    return {
        scenario_id: round(sum(scores) / len(scores), 4)
        for scenario_id, scores in grouped.items()
        if scores
    }


def _policy_compliance_from_audit_logs(db_path: Path) -> float | None:
    try:
        with _connect(db_path) as connection:
            latest = connection.execute(
                "SELECT MAX(datetime(created_at)) FROM audit_logs").fetchone()[0]
            if latest is None:
                return None
            rows = connection.execute(
                """
                SELECT policy_status, ujcs
                FROM audit_logs
                WHERE datetime(created_at) >= datetime(?, '-30 days')
                """,
                (latest,),
            ).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    compliant = sum(
        1
        for row in rows
        if row["policy_status"] == "compliant" and float(row["ujcs"] or 0) >= 0.8
    )
    return round(compliant / len(rows), 4)


def _average_customer_turns(items: list[dict[str, Any]]) -> float:
    counts = []
    for item in items:
        artifacts = item.get("artifacts") if isinstance(
            item.get("artifacts"), dict) else {}
        messages = artifacts.get("messages") if isinstance(
            artifacts.get("messages"), list) else None
        if messages is not None:
            counts.append(len(messages))
    return round(sum(counts) / len(counts), 2) if counts else 0


def _latest_evaluation_file(data_dir: Path) -> Path | None:
    files = list(data_dir.glob("eval_*.json"))
    return max(files, key=lambda item: item.stat().st_mtime) if files else None


def _latest_policy_compliance_pct(db_path: Path) -> float | None:
    latest = _latest_evaluation_file(db_path.resolve().parent)
    if latest is None:
        return None
    try:
        stat = latest.stat()
    except OSError:
        return None
    cache_key = (str(db_path.resolve()), stat.st_mtime_ns, stat.st_size)
    if cache_key in _POLICY_COMPLIANCE_PCT_CACHE:
        return _POLICY_COMPLIANCE_PCT_CACHE[cache_key]

    audit_score = _policy_compliance_from_audit_logs(db_path)
    if audit_score is not None:
        value = round(audit_score * 100, 1)
        _POLICY_COMPLIANCE_PCT_CACHE.clear()
        _POLICY_COMPLIANCE_PCT_CACHE[cache_key] = value
        return value

    try:
        evaluation = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = _policy_compliance_pct_from_evaluation(evaluation)
    _POLICY_COMPLIANCE_PCT_CACHE.clear()
    _POLICY_COMPLIANCE_PCT_CACHE[cache_key] = value
    return value


def _policy_compliance_pct_from_evaluation(evaluation: dict[str, Any]) -> float | None:
    results = [item for item in evaluation.get(
        "results", []) if isinstance(item, dict)]
    if not results:
        return None
    scores = _scenario_policy_scores(results)
    if not scores:
        return None
    return round((sum(scores.values()) / len(scores)) * 100, 1)


def _data_dir(request: Request) -> Path:
    return Path(request.app.state.db_path).resolve().parents[0]


def _json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, tuple):
        return list(raw)
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _tool_names(items: list[Any]) -> list[str]:
    names = []
    for item in items:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            names.append(str(item.get("tool_name")
                         or item.get("name") or "tool_call"))
    return names


def dashboard_insights(request: Request) -> dict[str, Any]:
    with _connect(request.app.state.db_path) as connection:
        rows = connection.execute("SELECT session_id, intents FROM conversations ORDER BY created_at DESC LIMIT 20").fetchall()
        intents_list = []
        for row in rows:
            if row[1]:
                try:
                    intents_list.append(json.loads(row[1]))
                except Exception:
                    pass

    prompt = "Analyze these recent customer intents and generate a 3-sentence Root Cause Analysis for administrators. Do not use markdown:\n" + json.dumps(intents_list)
    try:
        client = LLMClient()
        analysis = client.generate(prompt, temperature=0.7)
        return {"insights": analysis, "source": "gemini", "fallback": False}
    except GeminiClientError as exc:
        analysis = (
            "Fallback insight: Gemini synthesis is unavailable, so this is a deterministic summary. "
            "Review the recent intent mix manually before taking action."
        )
        return {
            "insights": analysis,
            "source": "deterministic_fallback",
            "fallback": True,
            "error": exc.__class__.__name__,
        }


def _pct(value: int, total: int) -> float:
    return round((value / total) * 100, 1) if total else 0


def _label(value: Any) -> str:
    return str(value).replace("_", " ").replace("-", " ").title()


def _action_summary(audit: sqlite3.Row) -> str:
    actions = _json_list(audit["action_taken"])
    if not actions:
        return "No action recorded"
    labels = []
    for action in actions:
        if isinstance(action, dict):
            labels.append(_label(action.get("action")
                          or action.get("type") or "action"))
        else:
            labels.append(_label(action))
    return ", ".join(labels)
