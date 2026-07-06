# ResolveFlow AI — System Design

**Version:** 1.0
**Date:** 2026-07-02
**Status:** Production (Hackathon Build)

---

## 1. Problem Statement

Standard customer-support bots are optimized for *conversation*, not *resolution*. They cannot:
- Detect multiple concurrent issues in a single message
- Recall verified customer history across sessions
- Validate actions against enforceable business policy
- Prove that every response is grounded in real tool evidence
- Decide autonomously when to act, clarify, or escalate

ResolveFlow AI closes that gap for telecom support (fictional "ConnectCare Telecom").

---

## 2. Goals & Non-Goals

### Goals
- Resolve billing disputes, connectivity issues, and plan changes end-to-end without human intervention
- Enforce business policy at code level (not just via prompting)
- Produce a tamper-evident audit trail for every action taken
- Escalate to humans with full context before the conversation fails
- Run entirely without a live LLM key (deterministic fallback mode)

### Non-Goals
- Real payment processing or PII handling
- Multi-tenant session isolation (demo is single-process)
- Voice interface (text only)
- Production auth/TLS (demo scope)

---

## 3. Architecture Overview

```
                     ┌─────────────────────────────────────────┐
                     │           Next.js Frontend               │
                     │  (Chat UI · Dashboard · Eval · RAG page) │
                     └────────────────┬────────────────────────┘
                                      │ SSE + REST  (proxied /api/*)
                     ┌────────────────▼────────────────────────┐
                     │         FastAPI Application              │
                     │                                          │
                     │  ┌──────────┐  ┌──────────┐  ┌───────┐ │
                     │  │ Chat     │  │Dashboard │  │  RAG  │ │
                     │  │ Routes   │  │ Routes   │  │Routes │ │
                     │  └────┬─────┘  └──────────┘  └───────┘ │
                     │       │                                  │
                     │  ┌────▼──────────────────────────────┐  │
                     │  │         Agent Pipeline             │  │
                     │  │                                    │  │
                     │  │  [Intent] ─── [Memory] ─── [Policy]│  │
                     │  │       ↓            ↓          ↓    │  │
                     │  │  [asyncio.gather — concurrent]     │  │
                     │  │       ↓                            │  │
                     │  │  [Health Score] → [Plan/Clarify/   │  │
                     │  │                    Handoff]        │  │
                     │  │       ↓                            │  │
                     │  │  [Policy DAG] → [Tools] → [Trust]  │  │
                     │  │                    ↓               │  │
                     │  │  [Grounded Reply + CoVe verify]    │  │
                     │  │       ↓                            │  │
                     │  │  [Localize] → [Receipts] → SSE     │  │
                     │  └────────────────────────────────────┘  │
                     │                                          │
                     │  ┌──────────┐  ┌──────────┐  ┌───────┐ │
                     │  │ SQLite   │  │ChromaDB  │  │  LLM  │ │
                     │  │ (14 tbl) │  │(policies │  │Gemini │ │
                     │  │          │  │+ memory) │  │+fallbk│ │
                     │  └──────────┘  └──────────┘  └───────┘ │
                     └─────────────────────────────────────────┘
```

---

## 4. Component Descriptions

### 4.1 Agent Pipeline (`backend/agent/`)

The core inference loop. Each incoming message runs through these stages:

| Stage | Module | Key Behaviour |
|---|---|---|
| **Intent Classification** | `intent_classifier.py` | Multi-label JSON output; LLM with `application/json` MIME or deterministic regex fallback |
| **Customer Memory** | `memory_manager.py`, `memory_store.py`, `memory_graph.py` | Three-tier (stable/episodic/session); HippoRAG PPR graph + ChromaDB vector + SQLite fallback |
| **Policy RAG** | `policy_store.py`, `policy_retrieval.py` | Self-RAG retrieve decision + CRAG corrective routing over 8 policy docs in ChromaDB |
| **Conversation Health** | `health.py` | Composite score (0–100); gates proceed / clarify / escalate |
| **Clarification Engine** | `clarification.py` | Detects missing required slots; generates targeted one-question prompts |
| **Policy DAG** | `policy_graph.py` | Directed acyclic prerequisite graph; blocks high-risk actions at code level |
| **Guided Action** | `guided_action.py` | Wait-verify loop for physical actions (router reset → re-run diagnostic) |
| **Handoff** | `handoff.py` | Trigger detection (anger, health collapse, explicit request); generates context card |
| **Action Replay** | `action_replay.py` | Idempotency guard — surfaces prior taken actions before repeating |
| **Resolution Loop** | `resolution_loop.py` | Sequential resolution through the issue queue |
| **LLM Client** | `llm_client.py` | Gemini primary/secondary model split; sync `urllib` HTTP; no external dependency |

### 4.2 API Layer (`backend/api/`)

| Module | Prefix | Responsibility |
|---|---|---|
| `chat_routes.py` | `/api/chat` | SSE streaming inference, trust scoring, receipts, multi-language |
| `routes.py` | `/api/tools`, `/api/cases`, `/api/dashboard` | Tool endpoints, dashboard, case management |
| `dashboard_routes.py` | _(controller)_ | Data aggregation functions called by routes.py |
| `rag_routes.py` | `/api/rag` | Memory search, policy retrieve, graph visualization |
| `main.py` | — | FastAPI app init, lifespan, router wiring, CORS |

### 4.3 Tool Layer (`backend/tools.py`)

14 SQLite-backed tool endpoints, each emitting a policy DAG traversal record and UJCS score where applicable. All calls are appended to `audit_logs`.

| Tool | Action |
|---|---|
| `lookup_customer` | Returns customer profile + risk level |
| `get_invoice_history` | 12-month billing history |
| `check_duplicate_charge` | Scans for same-amount same-period duplicates |
| `check_outage_status` | Live outage lookup by location |
| `run_router_diagnostic` | Simulated router health check |
| `retrieve_policy` | Policy document retrieval (ChromaDB) |
| `apply_credit` | Issues credit; gated by `service_credit_dag` |
| `create_ticket` | Opens support ticket; gated by DAG |
| `schedule_technician` | Books field appointment; gated by `technician_dispatch_dag` |
| `change_plan` | Downgrades/upgrades plan; gated by `plan_downgrade_dag` |
| `generate_handoff_summary` | Packages escalation brief |
| `generate_context_card` | Full context for receiving human agent |
| `generate_opening_line` | Warm-handoff intro for human agent |
| `generate_audit_log` | Persists UJCS + policy path to `audit_logs` |

### 4.4 Data Layer (`backend/db/`)

**SQLite** — 14-table schema:

```
plans             customers         payments          invoices
outages           tickets           policies          diagnostics
credits           audit_logs        human_handoff_queue
memory_store      conversations     telemetry
```

**ChromaDB** — Two collections:
- `resolveflow_policies` — 8 policy documents chunked + embedded
- Customer memory — episodic + stable memory units with HippoRAG PPR graph in SQLite

### 4.5 Evaluation (`backend/evaluation/`)

Three-layer methodology:
1. **Deterministic** — DB-state assertions (tool called, record created, status updated)
2. **RAGAS** — Context recall + context precision over policy retrievals
3. **Business-Adherence** — Zero policy violations, zero missed escalations, zero inconsistent verdicts (arXiv 2601.00596)

---

## 5. Key Design Decisions

### 5.1 SSE over WebSockets for streaming
See [ADR-001](ADR-001-sse-streaming-pipeline.md). Short version: SSE is unidirectional (correct for server→client streaming), HTTP/1.1 compatible, simpler to implement, and easier to debug with standard browser tools.

### 5.2 HippoRAG PPR + ChromaDB + SQLite fallback for memory
See [ADR-002](ADR-002-hybrid-rag-memory.md). Hybrid RRF fusion across vector similarity and graph proximity gives better recall on multi-hop customer history queries. SQLite fallback ensures the knowledge base is never empty without the optional indexing step.

### 5.3 Policy DAG for compliance
See [ADR-003](ADR-003-policy-dag-compliance.md). Enforcement at code level (raises `PolicyActionBlocked`) vs. soft prompting means a malicious or confused LLM cannot bypass credit or cancellation limits. UJCS score gives a continuous compliance signal for the audit log.

### 5.4 RAGAS + Business-Adherence evaluation
See [ADR-004](ADR-004-evaluation-framework.md). RAGAS context recall/precision measures retrieval quality. Business-adherence (τ²-bench / Beyond IVR) measures policy compliance at the conversation level — the metric that actually correlates with real customer harm.

---

## 6. Concurrency Model

The SSE generator uses `asyncio.gather` to run intent classification, memory retrieval, and policy RAG **concurrently** in the hot path, masking the slowest of the three:

```
wall-clock ≈ max(t_intent, t_memory, t_policy)
           instead of t_intent + t_memory + t_policy
```

All blocking I/O (SQLite, Gemini HTTP) is wrapped with `asyncio.to_thread` so the event loop is never blocked. The localization LLM call (fix applied 2026-07-02) is also wrapped.

---

## 7. Trust & Safety Pipeline

```
draft = llm.generate(prompt)
score = _action_trust_score(draft, tool_results)
  ├─ deterministic guards: overclaims? missing evidence?
  └─ CoVe self-check: verifier LLM prompted to refute the draft

if score >= 0.6:  proceed
elif attempts == 1:
    revised = llm.generate(revision_prompt)
    re-score
    if score >= 0.6: proceed with revised
else:
    emit safe grounded fallback + trigger human escalation
```

Every emitted claim is bound to its source tool output via HMAC-SHA256 receipt (arXiv 2603.10060), shown in the UI as a **✓ Verified** badge.

---

## 8. Data Flow: Single Chat Turn

```
POST /api/chat/{customer_id}
  │
  ├─ asyncio.gather(
  │    _run_intent(message)          → intents, emotion, issue_queue
  │    _run_memory(customer_id)      → customer profile + memories
  │  )
  │
  ├─ health_score = compute_health(customer, emotion, intents, ...)
  │
  ├─ if health < 45: force_handoff = True
  │
  ├─ tools_block:
  │    billing_intent    → get_invoice_history, check_duplicate_charge
  │    outage_intent     → check_outage_status
  │    diagnostic_intent → run_router_diagnostic
  │    cancellation_intent → _get_subscription_status, _get_cancellation_policy,
  │                          _build_retention_offer
  │
  ├─ _grounded_reply(prompt, tool_results)
  │    ├─ generate draft
  │    ├─ _action_trust_score → score, issues
  │    ├─ if score < 0.6: revise once
  │    └─ if still < 0.6: grounded fallback + set force_handoff
  │
  ├─ _localize_response(text, customer, llm)   [asyncio.to_thread]
  │
  ├─ _maybe_build_handoff(...)
  │
  ├─ _attach_receipts(claims, tool_results)
  │
  └─ yield SSE event: {text, trust, receipts, handoff, language, tool_results}
```

---

## 9. Frontend Architecture

Next.js 16 / React 19 / Tailwind CSS. The current app exposes 16 page routes; these are the main demo-facing surfaces:

| Route | Purpose |
|---|---|
| `/` | Landing page with live KPIs |
| `/test` | Conversational cockpit (3-column: selector · chat · reasoning) |
| `/cases` | Paginated case browser |
| `/cases/[id]` | Case detail with audit log + handoff card |
| `/evaluation` | Evaluation results: RAGAS, business-adherence, scenario table |
| `/admin` | Admin dashboard with God-Mode insights |
| `/workspace` | Operator workspace |
| `/rag` | Knowledge-base search panel |

Additional operator/admin routes include `/actions`, `/agent-desk`, `/audit`, `/harness`, `/project`, `/security`, `/setup`, and `/tools`.

**Generative UI:** The SSE stream carries a `tool_results` array. The frontend maps `tool_name` → widget component (`InvoiceWidget`, `OutageWidget`, `CreditWidget`, `RetentionWidget`) and renders them inline in the chat bubble using Recharts + Tailwind.

---

## 10. Deployment

Single-machine, two-process:

```
uvicorn backend.api.main:app --reload --port 8000   # Python 3.10+
npm run dev (from frontend/)                         # Node 20+
```

Frontend proxies `/api/*` → `http://localhost:8000` via `next.config.ts`.

Dockerfiles and `docker-compose.yml` are available for local containerized runs. All persistence is SQLite + local ChromaDB directories unless you provide live external keys for Gemini or hosted deployment services.
