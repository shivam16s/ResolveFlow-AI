from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
from typing import Any

from .db.init_db import DEFAULT_DB_PATH
from .tools import generate_context_card


@dataclass(frozen=True)
class RenderedHandoffCard:
    case_id: str
    session_id: str
    customer_id: str
    html: str
    context_card: dict[str, Any]
    source: str = "dashboard_case_detail_handoff_tab"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RenderedAuditLogTabs:
    case_id: str
    session_id: str
    customer_id: str
    html: str
    raw_json: dict[str, Any]
    source: str = "dashboard_case_detail_audit_tabs"

    def to_dict(self) -> dict:
        return asdict(self)


def render_case_handoff_tab(
    case_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> RenderedHandoffCard | None:
    normalized_case_id = _require_text(case_id, "case_id")
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        audit_row = connection.execute(
            """
            SELECT case_id, session_id, customer_id
            FROM audit_logs
            WHERE case_id = ?
            """,
            (normalized_case_id,),
        ).fetchone()
    if audit_row is None:
        return None

    context_card = generate_context_card(audit_row["session_id"], db_path=db_path)
    if context_card is None:
        return None

    html = render_handoff_context_card_html(context_card)
    return RenderedHandoffCard(
        case_id=normalized_case_id,
        session_id=audit_row["session_id"],
        customer_id=audit_row["customer_id"],
        html=html,
        context_card=context_card,
    )


def render_case_audit_log_tabs(
    case_id: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> RenderedAuditLogTabs | None:
    normalized_case_id = _require_text(case_id, "case_id")
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
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
    if row is None:
        return None

    raw_json = _audit_row_payload(row)
    html = render_audit_log_tabs_html(raw_json)
    return RenderedAuditLogTabs(
        case_id=raw_json["case_id"],
        session_id=raw_json["session_id"],
        customer_id=raw_json["customer_id"],
        html=html,
        raw_json=raw_json,
    )


def render_audit_log_tabs_html(raw_json: dict[str, Any]) -> str:
    if not isinstance(raw_json, dict):
        raise ValueError("raw_json must be a dict")

    tools = raw_json.get("tools_called")
    evidence = raw_json.get("evidence_used")
    actions = raw_json.get("action_taken")
    path = raw_json.get("policy_dag_path")
    raw_json_text = json.dumps(raw_json, indent=2, sort_keys=True, ensure_ascii=True)
    summary = _audit_summary(raw_json)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Case Detail - Proof Trail</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #1d2433;
      --muted: #5f6b7a;
      --line: #d8dee8;
      --accent: #2457c5;
      --code: #101828;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }}
    .case-detail {{
      max-width: 1160px;
      margin: 0 auto;
      padding: 24px;
    }}
    .case-detail-tabs {{
      display: flex;
      gap: 4px;
      border-bottom: 1px solid var(--line);
      margin-bottom: 18px;
    }}
    .case-detail-tabs a {{
      color: var(--muted);
      text-decoration: none;
      padding: 10px 12px;
      border: 1px solid transparent;
      border-bottom: 0;
      border-radius: 6px 6px 0 0;
      font-weight: 600;
    }}
    .case-detail-tabs a[aria-selected="true"] {{
      background: var(--panel);
      color: var(--accent);
      border-color: var(--line);
      margin-bottom: -1px;
    }}
    .proof-trail {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    .proof-header {{
      padding: 18px 20px;
      border-bottom: 1px solid var(--line);
    }}
    h1, h2 {{
      margin: 0;
      letter-spacing: 0;
    }}
    h1 {{
      font-size: 22px;
    }}
    h2 {{
      font-size: 15px;
      margin-bottom: 10px;
    }}
    .subtle {{
      color: var(--muted);
    }}
    .tab-controls {{
      display: flex;
      gap: 6px;
      padding: 12px 20px 0;
      border-bottom: 1px solid var(--line);
    }}
    .tab-controls label {{
      padding: 9px 12px;
      border: 1px solid var(--line);
      border-bottom: 0;
      border-radius: 6px 6px 0 0;
      color: var(--muted);
      font-weight: 700;
      cursor: pointer;
    }}
    input[name="proof-tab"] {{
      position: absolute;
      opacity: 0;
      pointer-events: none;
    }}
    #tab-human:checked ~ .tab-controls label[for="tab-human"],
    #tab-json:checked ~ .tab-controls label[for="tab-json"] {{
      color: var(--accent);
      background: var(--panel);
    }}
    .tab-panel {{
      display: none;
      padding: 18px 20px 20px;
    }}
    #tab-human:checked ~ .tab-panels [data-panel="human"],
    #tab-json:checked ~ .tab-panels [data-panel="json"] {{
      display: block;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px 24px;
    }}
    .metric-row {{
      display: grid;
      grid-template-columns: 150px minmax(0, 1fr);
      gap: 12px;
      margin: 7px 0;
    }}
    .label {{
      color: var(--muted);
      font-weight: 600;
    }}
    .list {{
      margin: 0;
      padding-left: 18px;
    }}
    .list li {{
      margin: 5px 0;
    }}
    pre {{
      margin: 0;
      padding: 16px;
      overflow: auto;
      background: #f0f3f8;
      color: var(--code);
      border: 1px solid var(--line);
      border-radius: 8px;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 13px;
    }}
    @media (max-width: 760px) {{
      .case-detail {{
        padding: 12px;
      }}
      .case-detail-tabs, .tab-controls {{
        overflow-x: auto;
      }}
      .grid, .metric-row {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="case-detail">
    <nav class="case-detail-tabs" aria-label="Case detail sections">
      <a href="#" aria-selected="false">Overview</a>
      <a href="#" aria-selected="false">Transcript</a>
      <a href="#" aria-selected="true">Proof Trail</a>
      <a href="#" aria-selected="false">Handoff</a>
    </nav>
    <section class="proof-trail" data-testid="proof-trail-tabs">
      <header class="proof-header">
        <h1>Resolution Proof Trail</h1>
        <div class="subtle">Case {escape(_text(raw_json.get("case_id")))} | Customer {escape(_text(raw_json.get("customer_id")))} | Session {escape(_text(raw_json.get("session_id")))}</div>
      </header>
      <input id="tab-human" name="proof-tab" type="radio" checked>
      <input id="tab-json" name="proof-tab" type="radio">
      <div class="tab-controls" role="tablist" aria-label="Proof trail views">
        <label for="tab-human" role="tab" data-testid="human-readable-tab">Human-readable</label>
        <label for="tab-json" role="tab" data-testid="raw-json-tab-label">Raw JSON</label>
      </div>
      <div class="tab-panels">
        <section class="tab-panel" data-panel="human" data-testid="human-readable-panel">
          <div class="grid">
            <div>
              <h2>Summary</h2>
              {_metric("Status", raw_json.get("policy_status"))}
              {_metric("UJCS", raw_json.get("ujcs"))}
              {_metric("Health score", raw_json.get("health_score"))}
              {_metric("Handoff", "Required" if raw_json.get("handoff_required") else "Not required")}
              {_metric("Created", raw_json.get("created_at"))}
            </div>
            <div>
              <h2>Human Summary</h2>
              {_metric("Proof", summary)}
            </div>
            <div>
              <h2>Tools</h2>
              {_list_block("Called", _tool_labels(tools))}
            </div>
            <div>
              <h2>Evidence</h2>
              {_list_block("Used", evidence)}
            </div>
            <div>
              <h2>Actions</h2>
              {_list_block("Taken", _action_labels(actions))}
            </div>
            <div>
              <h2>Policy DAG</h2>
              {_list_block("Path", path)}
            </div>
          </div>
        </section>
        <section class="tab-panel" data-panel="json" data-testid="raw-json-panel">
          <pre>{escape(raw_json_text)}</pre>
        </section>
      </div>
    </section>
  </main>
</body>
</html>"""


def render_handoff_context_card_html(context_card: dict[str, Any]) -> str:
    if not isinstance(context_card, dict):
        raise ValueError("context_card must be a dict")

    customer = _dict_value(context_card.get("customer"))
    issues_summary = _dict_value(context_card.get("issues_summary"))
    policy_path = _dict_value(context_card.get("policy_dag_path_so_far"))
    audit = _dict_value(context_card.get("audit"))
    handoff_queue = _dict_value(context_card.get("handoff_queue"))
    relationship = _dict_value(context_card.get("relationship"))

    customer_name = _text(customer.get("name"), "Unknown customer")
    plan_name = _text(customer.get("plan_name"), "Plan unavailable")
    account_status = _text(customer.get("account_status"), "unknown")
    location = _text(customer.get("location"), "Location unavailable")
    opening = _text(context_card.get("recommended_opening"), "I have the case context and can continue from here.")
    reason = _text(context_card.get("reason_for_escalation"), "No escalation reason recorded.")
    last_message = _text(context_card.get("last_customer_message"), "No customer message recorded.")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Case Detail - Handoff</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #1d2433;
      --muted: #5f6b7a;
      --line: #d8dee8;
      --accent: #2457c5;
      --ok: #0b7a53;
      --warn: #a15c00;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }}
    .case-detail {{
      max-width: 1160px;
      margin: 0 auto;
      padding: 24px;
    }}
    .case-detail-tabs {{
      display: flex;
      gap: 4px;
      border-bottom: 1px solid var(--line);
      margin-bottom: 18px;
    }}
    .case-detail-tabs a {{
      color: var(--muted);
      text-decoration: none;
      padding: 10px 12px;
      border: 1px solid transparent;
      border-bottom: 0;
      border-radius: 6px 6px 0 0;
      font-weight: 600;
    }}
    .case-detail-tabs a[aria-selected="true"] {{
      background: var(--panel);
      color: var(--accent);
      border-color: var(--line);
      margin-bottom: -1px;
    }}
    .handoff-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    .handoff-header {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      padding: 18px 20px;
      border-bottom: 1px solid var(--line);
    }}
    h1, h2 {{
      margin: 0;
      letter-spacing: 0;
    }}
    h1 {{
      font-size: 22px;
    }}
    h2 {{
      font-size: 15px;
      margin-bottom: 10px;
    }}
    .subtle {{
      color: var(--muted);
    }}
    .status-pill {{
      align-self: start;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 10px;
      font-weight: 700;
      color: var(--accent);
      background: #eef3ff;
      white-space: nowrap;
    }}
    .opening {{
      padding: 16px 20px;
      border-bottom: 1px solid var(--line);
      font-size: 16px;
      font-weight: 650;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 1px;
      background: var(--line);
    }}
    .panel {{
      background: var(--panel);
      padding: 16px 20px;
      min-width: 0;
    }}
    .metric-row {{
      display: grid;
      grid-template-columns: 160px minmax(0, 1fr);
      gap: 12px;
      margin: 7px 0;
    }}
    .label {{
      color: var(--muted);
      font-weight: 600;
    }}
    .list {{
      margin: 0;
      padding-left: 18px;
    }}
    .list li {{
      margin: 5px 0;
    }}
    .path {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
    }}
    .node {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      background: #fbfcfe;
      font-weight: 600;
    }}
    .arrow {{
      color: var(--muted);
    }}
    .ok {{
      color: var(--ok);
      font-weight: 700;
    }}
    .warn {{
      color: var(--warn);
      font-weight: 700;
    }}
    @media (max-width: 760px) {{
      .case-detail {{
        padding: 12px;
      }}
      .handoff-header, .grid {{
        grid-template-columns: 1fr;
      }}
      .case-detail-tabs {{
        overflow-x: auto;
      }}
      .metric-row {{
        grid-template-columns: 1fr;
        gap: 2px;
      }}
    }}
  </style>
</head>
<body>
  <main class="case-detail">
    <nav class="case-detail-tabs" aria-label="Case detail sections">
      <a href="#" aria-selected="false">Overview</a>
      <a href="#" aria-selected="false">Transcript</a>
      <a href="#" aria-selected="false">Proof Trail</a>
      <a href="#" aria-selected="true">Handoff</a>
    </nav>
    <section class="handoff-card" data-testid="handoff-context-card">
      <header class="handoff-header">
        <div>
          <h1>{escape(customer_name)}</h1>
          <div class="subtle">{escape(plan_name)} | {escape(location)} | {escape(account_status)}</div>
        </div>
        <div class="status-pill">{escape(_handoff_status(handoff_queue))}</div>
      </header>
      <div class="opening" data-testid="opening-line">{escape(opening)}</div>
      <div class="grid">
        <section class="panel" data-testid="issue-summary">
          <h2>Issues</h2>
          {_metric("Summary", issues_summary.get("summary_text"))}
          {_metric("Resolved", _count_label(issues_summary.get("resolved_count"), "issue"))}
          {_metric("Remaining", _count_label(issues_summary.get("remaining_count"), "issue"))}
          {_list_block("Remaining labels", issues_summary.get("remaining_labels"))}
        </section>
        <section class="panel" data-testid="handoff-queue">
          <h2>Handoff</h2>
          {_metric("Reason", reason)}
          {_metric("Queue status", handoff_queue.get("status"))}
          {_metric("Handoff ID", handoff_queue.get("handoff_id"))}
          {_metric("Assigned to", handoff_queue.get("assigned_to") or "Unassigned")}
        </section>
        <section class="panel" data-testid="health-relationship">
          <h2>Health</h2>
          {_metric("Current score", context_card.get("current_health_score"))}
          {_metric("Emotion", context_card.get("emotion"))}
          {_metric("Urgency", context_card.get("urgency"))}
          {_metric("Relationship", _relationship_text(relationship))}
        </section>
        <section class="panel" data-testid="policy-path">
          <h2>Policy DAG Path</h2>
          {_path_block(policy_path.get("nodes"))}
          {_metric("Current node", policy_path.get("current_node"))}
          {_metric("Policy status", policy_path.get("policy_status"))}
          {_metric("UJCS", policy_path.get("ujcs"))}
        </section>
        <section class="panel" data-testid="evidence-actions">
          <h2>Evidence and Actions</h2>
          {_list_block("Evidence", context_card.get("evidence_used"))}
          {_list_block("Actions", _action_labels(context_card.get("actions_taken")))}
        </section>
        <section class="panel" data-testid="memory-message">
          <h2>Memory and Last Message</h2>
          {_list_block("Memory", _memory_labels(context_card.get("memory_context")))}
          {_metric("Last message", last_message)}
        </section>
      </div>
    </section>
  </main>
</body>
</html>"""


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _audit_row_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
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


def _json_value(raw_value: str | None, default):
    if raw_value is None:
        return default
    try:
        value = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return default
    return value


def _audit_summary(raw_json: dict[str, Any]) -> str:
    tools = ", ".join(_tool_labels(raw_json.get("tools_called"))) or "no tools recorded"
    evidence_count = len(raw_json.get("evidence_used") or [])
    action_count = len(raw_json.get("action_taken") or [])
    ujcs = raw_json.get("ujcs")
    ujcs_text = "not computed" if ujcs is None else f"{float(ujcs):.4f}"
    handoff_text = "handoff required" if raw_json.get("handoff_required") else "no handoff required"
    return (
        f"Case {raw_json.get('case_id')} for customer {raw_json.get('customer_id')} used {tools}; "
        f"{evidence_count} evidence item(s), {action_count} action(s), UJCS {ujcs_text}, "
        f"policy status {raw_json.get('policy_status')}, {handoff_text}."
    )


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    normalized = " ".join(str(value).split())
    return normalized or default


def _metric(label: str, value: Any) -> str:
    return (
        '<div class="metric-row">'
        f'<div class="label">{escape(label)}</div>'
        f"<div>{escape(_text(value, 'Not recorded'))}</div>"
        "</div>"
    )


def _list_block(label: str, values: Any) -> str:
    if not isinstance(values, list) or not values:
        return _metric(label, "None")
    items = "".join(f"<li>{escape(_text(item))}</li>" for item in values)
    return (
        '<div class="metric-row">'
        f'<div class="label">{escape(label)}</div>'
        f'<ul class="list">{items}</ul>'
        "</div>"
    )


def _path_block(nodes: Any) -> str:
    if not isinstance(nodes, list) or not nodes:
        return _metric("Path", "Not started")
    parts = []
    for index, node in enumerate(nodes):
        if index:
            parts.append('<span class="arrow">to</span>')
        parts.append(f'<span class="node">{escape(_text(node))}</span>')
    return (
        '<div class="metric-row">'
        '<div class="label">Path</div>'
        f'<div class="path">{"".join(parts)}</div>'
        "</div>"
    )


def _count_label(value: Any, noun: str) -> str:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return "Not recorded"
    suffix = noun if count == 1 else f"{noun}s"
    return f"{count} {suffix}"


def _relationship_text(relationship: dict[str, Any]) -> str:
    start = relationship.get("start")
    end = relationship.get("end")
    delta = relationship.get("delta")
    if start is None and end is None:
        return "Not recorded"
    return f"{_text(start)} to {_text(end)} ({_signed_number(delta)})"


def _signed_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "delta unavailable"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:g}"


def _handoff_status(handoff_queue: dict[str, Any]) -> str:
    status = _text(handoff_queue.get("status"), "No queue record")
    return f"Handoff: {status}"


def _action_labels(actions: Any) -> list[str]:
    if not isinstance(actions, list):
        return []
    labels = []
    for action in actions:
        if isinstance(action, dict):
            labels.append(_text(action.get("action") or action.get("status") or action))
        else:
            labels.append(_text(action))
    return labels


def _tool_labels(tools: Any) -> list[str]:
    if not isinstance(tools, list):
        return []
    labels = []
    for tool in tools:
        if isinstance(tool, dict):
            labels.append(_text(tool.get("tool_name") or tool.get("name") or tool.get("tool") or tool))
        else:
            labels.append(_text(tool))
    return labels


def _memory_labels(memories: Any) -> list[str]:
    if not isinstance(memories, list):
        return []
    labels = []
    for memory in memories:
        if isinstance(memory, dict):
            labels.append(_text(memory.get("content") or memory.get("memory_id")))
        else:
            labels.append(_text(memory))
    return labels
