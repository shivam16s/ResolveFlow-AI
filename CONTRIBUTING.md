# Contributing to ResolveFlow AI

This document covers dev setup, conventions, and how to add features or fix bugs.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.10+ | Required; 3.11 recommended |
| Node.js | 20+ | Required by Next.js 16 |
| npm | 9+ | Comes with Node 20 |
| Git | Any | — |
| Docker | Recent Docker Desktop/Engine | Optional one-command full stack |

---

## Quick Setup

### Docker

```bash
docker compose up --build
```

Compose builds the Python 3.11 backend image, seeds `/app/data/resolveflow.db`
on startup, builds the standalone Next.js frontend image, and exposes:

- Backend API: `http://localhost:8000`
- Frontend app: `http://localhost:3000`

Use `docker compose down -v` when you want a fresh seeded SQLite volume.

### Local

```bash
# 1. Clone
git clone <repo-url>
cd resolveflow-ai

# 2. Backend — create and activate venv
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. Install backend dependencies
pip install -r requirements.txt

# 4. Configure environment (optional — full fallback mode without a key)
cp .env.example .env
# Edit .env to add GEMINI_API_KEY if you want live LLM reasoning

# 5. Seed the demo database
python -m backend.db.seed_demo_dashboard

# 6. (Optional) Index memory into ChromaDB
python -m backend.scripts.index_demo_data

# 7. Start the backend
uvicorn backend.api.main:app --reload --port 8000

# 8. Frontend (new terminal)
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

---

## Project Layout

```
backend/
  agent/          Intent, memory, policy RAG, health, clarification, handoff, DAG
  api/            FastAPI routes: chat (SSE), tools, dashboard, RAG, eval
  db/             SQLite schema, seeders, reset, validation
  evaluation/     pass^k runner, RAGAS, business-adherence, benchmark
  scripts/        index_demo_data.py indexer
  dashboard.py    HTML rendering helpers
  tools.py        12 SQLite-backed tool functions

frontend/
  src/app/        Next.js 16 pages (test, cases, evaluation, admin, rag, …)
  src/components/ Shared components (GenerativeUI, ResolveLandingPage, …)
  src/lib/        Type definitions, API client utilities

data/
  policies/       8 policy documents (Markdown)
  evaluation_scenarios.json
  resolveflow_demo.db   (git-ignored; created by seed_demo_dashboard)

docs/
  architecture/   System design, ADRs
  api/            API reference
  testing/        Testing strategy

scripts/
  test_*.py       One test script per backend feature
```

---

## Development Conventions

### Python (backend)
- **PEP-8** compliant. Run `flake8 backend/` before committing.
- Type annotations on all public functions.
- No comments that restate what the code does. Only comments explaining *why* (hidden invariant, workaround, non-obvious constraint).
- No multi-paragraph docstrings. One short line max.
- Functions that call SQLite take `db_path: Path` as an explicit argument — never use a global DB path.
- All blocking I/O inside `async` functions must use `asyncio.to_thread(...)`.

### TypeScript / React (frontend)
- Follow the conventions in `frontend/AGENTS.md` before writing any Next.js code.
- Read `node_modules/next/dist/docs/` for the installed version's API.
- `"use client"` only at the component boundary, not in every file.

### Tests
- One `scripts/test_<feature>.py` per feature.
- No mocking of SQLite. Use a real temp DB seeded in-test.
- LLM is always allowed to fall back deterministically — tests must pass without `GEMINI_API_KEY`.
- Each test file must print `"<feature> tests passed"` on success.

---

## Running Tests

```bash
# All tests
# macOS/Linux:
for f in scripts/test_*.py; do python -B "$f" || break; done

# Windows PowerShell:
Get-ChildItem scripts/test_*.py | ForEach-Object { python -B $_.FullName }

# Single test:
python -B scripts/test_duplicate_charge.py
```

Expected: 58 scripts, all passing. No API key required.

---

## Adding a Feature

1. **Backend module** — add or edit in `backend/agent/` or `backend/api/`
2. **Tool** — add to `backend/tools.py` + wire an endpoint in `backend/api/routes.py`
3. **Test** — create `scripts/test_<feature>.py` (see [Testing Strategy](docs/testing/TESTING_STRATEGY.md))
4. **Frontend** — add to `frontend/src/app/` or `frontend/src/components/`
5. **Docs** — update the relevant ADR in `docs/architecture/` or the API reference in `docs/api/`

---

## Fixing a Bug

1. Write a failing test that reproduces the bug first.
2. Fix the code.
3. Confirm the test now passes.
4. Confirm all 58 existing tests still pass.

---

## Environment Variables

All optional. See [`.env.example`](.env.example) for full reference.

| Variable | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` | _(none)_ | Enables live LLM. Without it: deterministic fallbacks. |
| `GEMINI_PRIMARY_MODEL` | `gemini-2.5-flash` | Heavy model (planning, response) |
| `GEMINI_SECONDARY_MODEL` | `gemini-2.5-flash-lite` | Light model (classification, scoring) |
| `RESOLVEFLOW_NOW` | `2026-06-01` | Demo time anchor |
| `RESOLVEFLOW_DB_PATH` | `data/resolveflow_demo.db` | SQLite path override |
| `RESOLVEFLOW_RECEIPT_SECRET` | _(fallback key — set this!)_ | HMAC key for tamper-evident receipts |

---

## Common Commands

```bash
# Re-seed the demo database from scratch
python -m backend.db.seed_demo_dashboard

# Re-index memory into ChromaDB
python -m backend.scripts.index_demo_data

# Lint backend
flake8 backend/

# Frontend type-check
cd frontend && npx tsc --noEmit

# Frontend lint
cd frontend && npm run lint

# Start both services (two terminals):
uvicorn backend.api.main:app --reload --port 8000
cd frontend && npm run dev
```

---

## Architecture Decisions

Major architectural choices are recorded as ADRs in [`docs/architecture/`](docs/architecture/):

| ADR | Decision |
|---|---|
| [ADR-001](docs/architecture/ADR-001-sse-streaming-pipeline.md) | SSE over WebSockets for streaming |
| [ADR-002](docs/architecture/ADR-002-hybrid-rag-memory.md) | HippoRAG PPR + ChromaDB + SQLite fallback for memory |
| [ADR-003](docs/architecture/ADR-003-policy-dag-compliance.md) | Policy DAG for code-level compliance enforcement |
| [ADR-004](docs/architecture/ADR-004-evaluation-framework.md) | Three-layer evaluation (deterministic + RAGAS + business-adherence) |
