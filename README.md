# ResolveFlow AI — Policy-Grounded Customer Care Agent

> A production-grade AI customer support agent that resolves complex service issues using memory, Retrieval-Augmented Generation (RAG), backend tool-calling, clarification logic, warm handoff to human agents, and auditable proof trails.

[![Backend: FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688)](https://fastapi.tiangolo.com/)
[![Frontend: Next.js](https://img.shields.io/badge/Frontend-Next.js-black)](https://nextjs.org/)
[![LLM: Gemini](https://img.shields.io/badge/LLM-Gemini%202.5-blue)](https://ai.google.dev/)
[![Vector DB: ChromaDB](https://img.shields.io/badge/VectorDB-ChromaDB-orange)](https://www.trychroma.com/)

---

## Table of Contents

- [Project Overview](#project-overview)
- [Architecture](#architecture)
- [Repository Structure](#repository-structure)
- [Backend: File-by-File](#backend-file-by-file)
  - [API Layer](#api-layer)
  - [Agent Layer](#agent-layer)
  - [Database Layer](#database-layer)
- [Frontend: File-by-File](#frontend-file-by-file)
- [Setup & Running](#setup--running)
- [Environment Variables](#environment-variables)

---

## Project Overview

ResolveFlow AI is built to handle the full lifecycle of a customer support interaction:
1. **Intent Classification** — Understands what the customer wants.
2. **Slot Filling** — Gathers all required information through targeted questions.
3. **Policy-Grounded RAG** — Looks up company policy before taking any action.
4. **Tool Calling** — Executes real backend actions (refunds, plan changes, technician dispatch, etc.).
5. **Memory** — Remembers past conversations and facts from prior interactions.
6. **Health Scoring** — Monitors conversation quality and triggers interventions.
7. **Warm Handoff** — Escalates to a human agent with a full context card.
8. **Audit Trail** — Every action is logged for compliance.

---

## Architecture

```
┌─────────────────────────────┐       ┌──────────────────────────────────┐
│       Next.js Frontend      │◄─────►│         FastAPI Backend          │
│  (Chat UI, Dashboard, RAG)  │  HTTP │  (Gemini LLM + Tools + Agent)   │
└─────────────────────────────┘       └────────────┬─────────────────────┘
                                                   │
                      ┌────────────────────────────┼────────────────────────┐
                      │                            │                        │
               ┌──────▼──────┐            ┌────────▼────────┐    ┌─────────▼──────┐
               │  SQLite DB  │            │   ChromaDB       │    │  Google Gemini │
               │(customers,  │            │(Policy vectors + │    │   API (LLM)   │
               │billing,     │            │ Memory vectors)  │    └────────────────┘
               │tickets etc.)│            └──────────────────┘
               └─────────────┘
```

---

## Repository Structure

```
ResolveFlow-AI/
├── backend/
│   ├── __init__.py               # Package root: exports tools & dashboard renderers
│   ├── tools.py                  # All callable backend tools (lookup, refund, etc.)
│   ├── dashboard.py              # HTML rendering for agent dashboard tabs
│   ├── Dockerfile                # Backend Docker image
│   ├── agent/                    # Core AI agent logic (21 modules)
│   │   ├── __init__.py           # Package exports for all agent components
│   │   ├── llm_client.py         # Gemini LLM wrapper (primary/secondary models)
│   │   ├── intent_classifier.py  # Classifies customer intent via LLM
│   │   ├── slot_schema.py        # Defines required data slots per intent
│   │   ├── clarification.py      # Decides next action after intent is known
│   │   ├── guided_action.py      # State machine for executing multi-step actions
│   │   ├── health.py             # Conversation health scoring (CASA framework)
│   │   ├── handoff.py            # Warm handoff detection & escalation
│   │   ├── acknowledgment.py     # Generates empathetic opening messages
│   │   ├── action_replay.py      # Confirms replaying previously taken actions
│   │   ├── issue_queue.py        # Manages a queue of open issues per session
│   │   ├── resolution_loop.py    # Sequential loop to resolve issues one by one
│   │   ├── memory.py             # Decomposes conversations into memory units
│   │   ├── memory_graph.py       # PPR-based graph retrieval over memory nodes
│   │   ├── memory_manager.py     # Orchestrates memory search + citation
│   │   ├── memory_reader.py      # LLM-based cited answer generation from memory
│   │   ├── memory_store.py       # ChromaDB vector store for memory units
│   │   ├── openie.py             # Open Information Extraction for memory facts
│   │   ├── policy_graph.py       # Policy as DAG: condition-gated action nodes
│   │   ├── policy_retrieval.py   # Self-RAG + CRAG for policy evidence lookup
│   │   └── policy_store.py       # ChromaDB vector store for policy documents
│   ├── api/
│   │   ├── app.py                # FastAPI app factory, lifespan, CORS
│   │   ├── routes.py             # REST endpoints: health, dashboard, tools
│   │   ├── chat_routes.py        # WebSocket/streaming chat endpoints
│   │   ├── rag_routes.py         # RAG policy query endpoints
│   │   └── main.py               # Uvicorn entry point
│   └── db/
│       ├── schema.sql            # Full SQLite schema definition
│       ├── init_db.py            # Creates the DB from schema.sql
│       ├── reset.py              # Wipes & re-seeds the database
│       ├── validation.py         # FK and data integrity checks
│       ├── seed_customers.py     # Seeds ~50 demo customer profiles
│       ├── seed_billing.py       # Seeds invoice/billing records
│       ├── seed_outages.py       # Seeds service outage records
│       ├── seed_policies.py      # Seeds policy documents
│       └── seed_demo_dashboard.py # Seeds rich demo data for the dashboard
├── frontend/
│   ├── src/
│   │   ├── app/                  # Next.js App Router pages
│   │   ├── components/           # Reusable UI components
│   │   │   ├── AppShell.tsx      # Root layout shell
│   │   │   ├── ResolveLandingPage.tsx # Main chat + conversation UI
│   │   │   ├── GenerativeUI.tsx  # Dynamic AI-generated UI cards
│   │   │   ├── Sidebar.tsx       # Navigation sidebar
│   │   │   ├── TopBar.tsx        # Top navigation bar
│   │   │   ├── KpiCard.tsx       # Metric display cards for dashboard
│   │   │   ├── Badges.tsx        # Status/label badges
│   │   │   └── BlueprintPrimitives.tsx # Design-system primitives
│   │   └── lib/                  # Utility functions and API clients
│   ├── package.json              # Node.js dependencies
│   ├── next.config.ts            # Next.js configuration
│   └── Dockerfile                # Frontend Docker image
├── data/                         # ChromaDB persistent storage (policy + memory)
├── docs/                         # Project documentation
├── scripts/                      # Utility scripts
├── docker-compose.yml            # Full-stack Docker orchestration
├── requirements.txt              # Python dependencies
├── .env.example                  # Environment variable template
└── README.md                     # This file
```

---

## Backend: File-by-File

### API Layer

#### `backend/api/app.py` — FastAPI Application Factory
The core server setup.
- **`create_app()`**: Creates the FastAPI instance. Registers all routers (`health_router`, `dashboard_router`, `tools_router`, `chat_router`, `rag_router`). Adds CORS middleware allowing localhost:3000 and localhost:3001.
- **`lifespan(app)`**: Async context manager for startup/shutdown. On startup, initialises `ChromaPolicyStore` and ingests all `.md` policy documents from the policy directory into ChromaDB (skipping re-ingestion if already populated to avoid SQLite write-lock contention). On shutdown, gracefully closes the `MemoryManager`.
- **`_runtime_db_path()`**: Reads `RESOLVEFLOW_DB_PATH` env var; falls back to a demo DB path.

#### `backend/api/routes.py` — Core REST Endpoints
Exposes three routers:
- **`health_router`** (`GET /api/health`): Returns server status and policy store health.
- **`dashboard_router`**: Powers the agent dashboard — fetches case lists, conversation histories, audit logs, and handoff queues from SQLite.
- **`tools_router`**: Exposes all backend tools as callable HTTP endpoints for direct invocation or testing.

#### `backend/api/chat_routes.py` — Streaming Chat Endpoints
The real-time chat API surface.
- Handles `POST /api/chat` and streaming SSE endpoints for the conversation flow.
- Orchestrates the full agent pipeline: intent classification → slot filling → policy retrieval → tool execution → response generation → memory storage.
- Manages session state including memory context, conversation history, and health score.

#### `backend/api/rag_routes.py` — RAG Query Endpoints
- **`POST /api/rag/query`**: Accepts a policy question and returns the most relevant policy evidence, running it through the CRAG evaluation pipeline.

---

### Agent Layer (`backend/agent/`)

#### `llm_client.py` — Gemini LLM Wrapper
- **`LLMClient`**: A thin wrapper around the Google Gemini API.
- Supports two model tiers: `GEMINI_PRIMARY_MODEL` (default: `gemini-2.5-flash`) for complex reasoning tasks and `GEMINI_SECONDARY_MODEL` (`gemini-2.5-flash-lite`) for cheaper classifications.
- Provides `chat()`, `generate()`, and `generate_structured()` methods for typed Pydantic response parsing.

#### `intent_classifier.py` — Intent Classification
- **`IntentClassifier`**: Uses the LLM with a few-shot prompt to classify customer intent into categories like `billing_dispute`, `cancellation_request`, `technical_issue`, `plan_change`, `refund_request`, etc.
- **`IntentClassification`**: Pydantic model capturing the identified intent, confidence score, and extracted entities.

#### `slot_schema.py` — Required Information Slots
- **`SLOT_SCHEMA`**: A dictionary mapping each intent to a list of `SlotDefinition` objects (e.g., for a refund, you need `invoice_id`, `reason`).
- **`detect_missing_required_slots()`**: Compares filled slots against the schema to identify what information is still missing.
- **`generate_targeted_question()`**: Uses the LLM to generate a natural-language question specifically asking for the missing slot value.
- **`prioritize_slot()`**: Determines the most important missing slot to ask for first.

#### `clarification.py` — Next Action Decision
- **`decide_next_action()`**: After intent classification and slot checking, decides the next step: ask a clarification question, retrieve policy, take an action, or escalate.
- **`NEXT_ACTIONS`**: Enum of possible next action types.
- **`TOOL_BY_INTENT`**: Maps each intent to the appropriate backend tool.

#### `guided_action.py` — Multi-Step Action State Machine
- **`GuidedActionCoordinator`**: Manages the execution of complex multi-step actions (e.g., a plan downgrade requires: validate eligibility → confirm with customer → execute change → send confirmation).
- **`VALID_STATE_TRANSITIONS`**: Defines the allowed state machine transitions.
- **`GuidedActionState`**: Enum tracking current position in the action flow (e.g., `AWAITING_CONFIRMATION`, `EXECUTING`, `COMPLETED`).
- **`GuidedActionAuditEvent`**: Records every step taken for the audit log.

#### `health.py` — Conversation Health Scoring (CASA Framework)
Computes a real-time multi-component health score to monitor conversation quality.

| Component | What it measures |
|---|---|
| `SentimentScoreComponent` | Customer sentiment via LLM sentiment analysis |
| `IntentConfidenceComponent` | How confident the classifier is |
| `KnowledgeCoverageComponent` | Whether policy RAG found relevant evidence |
| `MissingInfoRiskComponent` | How many critical slots are still unfilled |
| `LoopPenaltyComponent` | Detects repetitive/looping conversations |

- **`compute_health_score()`**: Aggregates all components into a weighted `HealthScore`.
- **`get_recommended_action()`**: Returns actionable advice based on score (e.g., "offer empathy", "escalate to human").
- **`casa_empathy_sequence()`**: Generates a CASA-framework empathy response sequence for low-health conversations.

#### `handoff.py` — Warm Handoff to Human Agents
- **`detect_handoff_triggers()`**: Scans conversation for escalation signals: customer anger, repeated failed resolution attempts, explicit request for a human, legal threats.
- **`HandoffTrigger`**: Pydantic model capturing the trigger type and confidence.
- **`generate_handoff_customer_message()`**: Generates an empathetic message informing the customer they are being transferred.
- **`insert_human_handoff_queue()`**: Writes the handoff to the SQLite `handoff_queue` table with a full context summary.
- **`log_handoff_event_to_audit()`**: Persists the event to the `audit_log` table.

#### `acknowledgment.py` — Empathetic Opening
- **`generate_acknowledgment()`**: Using CASA principles, generates a warm, empathetic first message that validates the customer's emotions before diving into problem-solving.

#### `action_replay.py` — Idempotent Action Replay
- **`load_taken_actions()`**: Retrieves all actions already taken in this session from the audit log.
- **`confirm_action_replay()`**: If a customer requests an action that has already been attempted, the LLM confirms whether to replay it, offering a summary of what was done previously.

#### `issue_queue.py` — Multi-Issue Management
- **`IssueQueue`**: Manages a list of distinct customer issues in a single session (a customer might have a billing dispute AND a connectivity problem).
- **`build_issue_queue()`**: Parses the conversation to identify all distinct issues.
- **`slot_progress_for_issue()`**: Returns the slot-fill completion status for a specific issue.

#### `resolution_loop.py` — Sequential Issue Resolution
- **`SequentialResolutionLoop`**: Iterates through the `IssueQueue` and resolves issues one at a time, updating state after each resolution.
- **`ResolutionRun`**: Captures the result of each resolution attempt.

#### `memory.py` — Episodic Memory Decomposition
- **`decompose_to_memory_units()`**: Takes a completed conversation and breaks it down into atomic `MemoryUnit` objects — facts, preferences, complaints, and resolutions.
- **`fact_augmented_expansion()`**: Enriches memory queries with related facts.
- **`time_aware_expansion()`**: Adds temporal context to memory lookups (e.g., "recent billing issues").

#### `memory_store.py` — ChromaDB Memory Vector Store
- **`ChromaMemoryStore`**: Wraps ChromaDB to store and retrieve `MemoryUnit` objects as dense vector embeddings.
- **`MemorySearchResult`**: Contains retrieved memory units with similarity scores and metadata.

#### `memory_graph.py` — Graph-Based Memory Retrieval (PPR)
- **`initialize_memory_graph()`**: Builds an in-memory graph where nodes are memory units and edges represent semantic or temporal relationships.
- **`update_memory_graph()`**: Adds new memory units and edges to the graph.
- **`ppr_retrieve()`**: Runs **Personalised PageRank (PPR)** over the memory graph, starting from the query node, to find the most contextually relevant memories beyond simple cosine similarity.
- **`add_synonymy_edges()`**: Uses `OpenIETriple` facts to connect semantically equivalent concepts in the graph.

#### `memory_manager.py` — Memory Orchestrator
- **`MemoryManager`**: Top-level class that coordinates `ChromaMemoryStore` + `memory_graph` retrieval.
- **`build_memory_citation_context()`**: Assembles retrieved memories into a formatted context block with source citations for the LLM prompt.
- **`MergedMemoryResult`**: Combines vector search results with PPR graph results, de-duplicated and ranked.

#### `memory_reader.py` — Cited Memory Answer Generation
- **`llm_read_with_citation()`**: Takes a question and a set of retrieved memory snippets, and uses the LLM to generate an answer that explicitly cites which memory units support each claim.
- **`CitedMemoryAnswer`**: Pydantic model containing the answer text and a list of `MemorySnippet` citations.

#### `openie.py` — Open Information Extraction
- **`extract_openie_triples()`**: Uses the LLM to extract `(subject, relation, object)` triples from conversation text (e.g., `("customer", "has_plan", "Premium")`).
- **`OpenIETriple`**: Pydantic model for a single extracted triple.
- These triples are used to build synonymy edges in the memory graph and to populate knowledge about the customer.

#### `policy_store.py` — ChromaDB Policy Vector Store
- **`ChromaPolicyStore`**: Ingests `.md` policy documents from disk, chunks them, embeds them, and stores them in ChromaDB.
- **`ingest_policy_docs()`**: Idempotent ingestion — skips re-ingestion if the collection already has the expected number of chunks.
- **`PolicyChunk`** / **`PolicyDocument`**: Pydantic models for raw policy text and chunked policy strips.

#### `policy_retrieval.py` — Self-RAG + CRAG Pipeline
The most sophisticated component — implements a multi-stage policy retrieval pipeline.

| Stage | Module | Description |
|---|---|---|
| **Decide** | `SelfRAGRetrieveDecider` | Decides whether retrieval is even needed for this query |
| **Rewrite** | `CRAGKeywordRewriter` | Rewrites the query with better keywords for vector search |
| **Retrieve** | `ChromaPolicyStore` | Fetches top-k candidate policy strips from ChromaDB |
| **Evaluate** | `CRAGRelevanceEvaluator` | LLM scores each strip for relevance to the query |
| **Route** | CRAG path router | Routes to correct, ambiguous, or incorrect path |
| **Correct** | `crag_incorrect_path` | If all strips are irrelevant, rewrites query and re-retrieves |
| **Gate** | `answer_passes_evidence_gate` | Ensures the final answer is grounded in retrieved evidence |

- **`PolicyStrip`**: A single passage from a policy document.
- **`ScoredPolicyStrip`**: A strip + relevance score from the evaluator.
- **`decompose_policy_to_strips()`**: Splits a long policy document into retrieval-sized strips.

#### `policy_graph.py` — Policy as a Decision DAG
Represents business policies as **Directed Acyclic Graphs (DAGs)** of conditional nodes.

- **`PolicyDAG`**: A directed graph where each node is a `PolicyNode` with a condition (e.g., `"account_age_days > 90"`) and a set of allowed actions.
- **`assert_action_allowed()`**: Traverses the DAG to verify that a proposed action (e.g., "issue full refund") is permitted given the current customer context.
- **`PolicyActionBlocked`**: Raised when a proposed action violates a policy condition.
- **`compute_ujcs()`**: Computes a Uniform Joint Condition Score to evaluate how well a proposed action fits the policy.

Pre-built DAGs for common scenarios:
| DAG | Scenario |
|---|---|
| `cancellation_retention_dag` | Customer wants to cancel → offer retention discounts |
| `duplicate_charge_refund_dag` | Duplicate billing → verify and refund |
| `plan_downgrade_dag` | Customer wants to downgrade their plan |
| `refund_exception_dag` | Out-of-policy refund requests |
| `service_credit_dag` | Issue service credits for outages |
| `technician_dispatch_dag` | Schedule on-site technician visit |

---

### Database Layer (`backend/db/`)

#### `schema.sql` — SQLite Database Schema
Defines all tables:
- **`customers`**: Customer profiles (account, plan, status).
- **`billing_records`**: Invoice history.
- **`outage_events`**: Service outage records.
- **`conversations`** / **`messages`**: Full conversation history.
- **`audit_log`**: Immutable log of every agent action.
- **`handoff_queue`**: Human escalation queue.
- **`tool_calls`**: Record of every backend tool invocation.

#### `init_db.py` — Database Initialisation
- **`init_db()`**: Creates all tables from `schema.sql` if they don't exist.

#### `seed_*.py` — Demo Data Seeding
- **`seed_customers.py`**: Creates ~50 realistic customer profiles with varied plan types, account ages, and statuses.
- **`seed_billing.py`**: Creates invoice histories including some with duplicate charges and outstanding balances.
- **`seed_outages.py`**: Seeds active and historical outage events for realistic tool responses.
- **`seed_policies.py`**: Seeds policy metadata records.
- **`seed_demo_dashboard.py`**: Creates a rich, multi-session demo dataset with complete conversation histories, tool calls, and handoff events for showcasing the dashboard.

#### `reset.py` — Database Reset
- Drops all tables and re-runs all seed scripts. Used for demo resets.

---

### `backend/tools.py` — Backend Tool Implementations
The largest file (~3200 lines). Implements all callable tools the AI agent can use:

| Tool | Description |
|---|---|
| `lookup_customer()` | Fetches customer profile, billing status, and plan details from SQLite |
| `get_invoice_history()` | Returns recent invoice records for a customer |
| `check_duplicate_charge()` | Detects duplicate billing entries for the same period |
| `apply_credit()` | Applies a service credit to a customer account |
| `change_plan()` | Executes a plan upgrade or downgrade |
| `create_ticket()` | Opens a support ticket in the system |
| `check_outage_status()` | Checks if there is an active outage affecting the customer's area |
| `run_router_diagnostic()` | Simulates a remote router diagnostics check |
| `schedule_technician()` | Schedules an on-site technician visit with a confirmation number |
| `retrieve_policy()` | Runs the full CRAG + Self-RAG policy retrieval pipeline |
| `generate_opening_line()` | Generates the first agent message using CASA empathy framework |
| `generate_context_card()` | Creates a formatted HTML context card for the agent dashboard |
| `generate_handoff_summary()` | Produces a full-context summary for the human agent receiving a handoff |
| `build_audit_log()` / `generate_audit_log()` | Assembles and formats the compliance audit log |

---

### `backend/dashboard.py` — Agent Dashboard HTML Renderer
Generates server-side HTML for the agent dashboard UI tabs:
- **`render_audit_log_tabs_html()`**: Generates tabbed HTML showing the timeline of all agent actions (tool calls, policy decisions, handoffs).
- **`render_case_handoff_tab()`**: Renders the handoff context card HTML for a specific case.
- **`render_handoff_context_card_html()`**: Generates a formatted context card for the receiving human agent (customer summary, open issues, taken actions, recommended next steps).

---

## Frontend: File-by-File

### `frontend/src/components/`

#### `ResolveLandingPage.tsx` — Main Chat Interface (23KB)
The primary UI component. Renders:
- The chat message thread with user/agent bubbles.
- Agent typing indicators.
- The health score badge and status.
- Generative UI card slots for tool results (e.g., invoice tables, outage alerts).
- The message input bar.
- Handles API calls to the chat backend via SSE streaming.

#### `GenerativeUI.tsx` — Dynamic AI-Generated UI Cards
Renders rich, structured UI cards that the AI returns alongside text responses:
- `InvoiceCard`: Shows billing records in a formatted table.
- `OutageAlertCard`: Displays active service outage information.
- `TechnicianCard`: Shows scheduled technician appointment details.
- `ContextCard`: Agent-facing summary of the customer session.
- `HandoffCard`: Handoff confirmation card shown to the customer.

#### `AppShell.tsx` — Root Layout
Wraps the application in the global layout: `Sidebar` + `TopBar` + main content area.

#### `Sidebar.tsx` — Navigation Sidebar
Left-side navigation with links to the Chat, Dashboard, and RAG Explorer pages.

#### `TopBar.tsx` — Top Navigation Bar
Displays the app name, current session info, and the conversation health score badge.

#### `KpiCard.tsx` — Dashboard KPI Cards
Reusable component for displaying Key Performance Indicator metrics (e.g., "Total Cases Today: 42", "Avg Resolution Time: 3.2 min").

#### `Badges.tsx` — Status Badges
Reusable badge components for displaying statuses: `OPEN`, `RESOLVED`, `ESCALATED`, `PENDING`, intent labels, severity levels, etc.

#### `BlueprintPrimitives.tsx` — Design System Primitives
Base-level design system components (buttons, cards, inputs, modals) that all other components are built on.

---

## Setup & Running

### Option 1: Docker Compose (Recommended)
```bash
# Copy and fill in your API key
cp .env.example .env

# Start the full stack
docker compose up --build
```
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000/docs

### Option 2: Manual Development Setup

**Backend:**
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Initialise and seed the database
python -m backend.db.init_db
python -m backend.db.seed_customers
python -m backend.db.seed_billing
python -m backend.db.seed_outages
python -m backend.db.seed_demo_dashboard

# Start the API server
uvicorn backend.api.app:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev    # http://localhost:3000
```

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `GEMINI_API_KEY` | **Required.** Google AI API key | — |
| `GEMINI_MODEL` | Primary Gemini model | `gemini-2.5-flash` |
| `GEMINI_PRIMARY_MODEL` | Primary model for complex tasks | `gemini-2.5-flash` |
| `GEMINI_SECONDARY_MODEL` | Cheaper model for classifications | `gemini-2.5-flash-lite` |
| `RESOLVEFLOW_DB_PATH` | Path to the SQLite database | `backend/db/resolveflow.db` |
| `RESOLVEFLOW_NOW` | Simulated current date for demos | Current date |
| `RESOLVEFLOW_RECEIPT_SECRET` | Secret for signing action receipts | — |
| `RESOLVEFLOW_AGENT_DESK_TOKEN` | Auth token for agent desk integration | — |
