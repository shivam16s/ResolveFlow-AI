# ResolveFlow AI — Testing Strategy

**Date:** 2026-07-02
**Coverage:** 58 test scripts across all backend modules

---

## Overview

ResolveFlow uses a **test-per-feature** strategy: every backend module has a corresponding `scripts/test_<module>.py` that exercises its public API with zero mocking (except the LLM, which falls back deterministically when no API key is set).

The test suite runs in < 30 seconds without a Gemini key and is the primary CI gate.

---

## Testing Pyramid

```
        ┌─────────────┐
        │  Eval Suite │  (13 scenarios, DB-state + RAGAS + business-adherence)
        │  3 scripts  │
        ├─────────────┤
        │  Integration│  (FastAPI TestClient, real SQLite, seeded data)
        │  ~40 scripts│
        ├─────────────┤
        │   Unit      │  (pure functions, data transformations)
        │  ~10 scripts│
        └─────────────┘
```

---

## Test Categories

### Unit Tests
Pure function tests with no I/O:

| Script | Module | What it tests |
|---|---|---|
| `test_acknowledgment_response.py` | `agent/acknowledgment.py` | Acknowledgment text generation for each intent type |
| `test_issue_queue_priority.py` | `agent/issue_queue.py` | Issue queue ordering and slot population |
| `test_slot_schema.py` | `agent/slot_schema.py` | Slot validation and required-field detection |
| `test_memory_decomposition.py` | `agent/memory.py` | Memory unit decomposition from transcripts |
| `test_memory_fact_augmented.py` | `agent/memory_reader.py` | Fact-augmented memory expansion |
| `test_openie_extraction.py` | `agent/openie.py` | OpenIE triple extraction and normalization |
| `test_clarification_decision.py` | `agent/clarification.py` | Clarification trigger conditions |
| `test_health_score_component.py` | `agent/health.py` | Health score component calculations |
| `test_handoff_trigger_detection.py` | `agent/handoff.py` | Handoff trigger conditions |
| `test_context_card.py` | `tools.py` | Context card field completeness |

### Integration Tests (Real SQLite + SeededDB)
These tests use the demo database schema and the real `seed_billing` / `seed_customers` seeders. No mocking — if the DB schema changes, these break:

| Script | What it tests |
|---|---|
| `test_lookup_customer.py` | Customer lookup: found, not-found, validation |
| `test_invoice_history.py` | Invoice fetch: correct months, customer scoping |
| `test_duplicate_charge.py` | Duplicate detection: INV-8821 planted scenario |
| `test_outage_status.py` | Outage lookup: active, cleared, location variants |
| `test_router_diagnostic.py` | Router diagnostic: pass, fail states |
| `test_apply_credit.py` | Credit: apply, idempotency, DAG block |
| `test_create_ticket.py` | Ticket creation, status, priority |
| `test_schedule_technician.py` | Technician scheduling, DAG gate |
| `test_change_plan.py` | Plan change, downgrade DAG |
| `test_audit_log.py` | Audit log persistence, field completeness |
| `test_db_reset.py` | Reset idempotency, schema preservation |
| `test_memory_store.py` | Memory store CRUD, idempotency |
| `test_memory_graph.py` | Graph node/edge insertion, PPR seed |
| `test_memory_manager.py` | Hybrid retrieval, fallback path |
| `test_chroma_memory_store.py` | ChromaDB memory collection CRUD |
| `test_llm_client.py` | LLMClient fallback mode, `generate()` interface |
| `test_intent_classifier_json.py` | Classifier JSON output, fallback rule-based |

### API / FastAPI Integration Tests
Test the full HTTP stack using `httpx.AsyncClient` + `TestClient`:

| Script | What it tests |
|---|---|
| `test_fastapi_scaffold.py` | All routes registered, health endpoint |
| `test_foundation_validation.py` | Schema integrity, seeded data invariants |
| `test_action_replay.py` | Idempotency guard (same action not repeated) |
| `test_guided_action_coordinator.py` | Wait-verify loop (router reset → re-diagnostic) |
| `test_chat_cancellation_flow.py` | Full cancellation flow: detect → offer → confirm |
| `test_chat_repeat_awareness.py` | Repeat-action detection across turns |
| `test_handoff_audit_event.py` | Handoff event written to audit log |
| `test_handoff_customer_message.py` | Customer-facing handoff message content |
| `test_handoff_queue_insertion.py` | Handoff queue DB insert |
| `test_handoff_summary.py` | Handoff summary completeness |
| `test_dashboard_audit_tabs.py` | Audit tab HTML rendering |
| `test_dashboard_handoff_card.py` | Handoff card HTML rendering |
| `test_demo_chat_response.py` | End-to-end chat response structure |

### Evaluation Tests

| Script | What it tests |
|---|---|
| `test_evaluation_runner.py` | Pass^k runner: correct DB-state assertions |
| `test_evaluation_scenario.py` | Scenario deserialization + field validation |
| `test_evaluation_report.py` | 9-metric report shape, RAGAS fields |
| `test_ragas_evaluation.py` | Context recall / precision computation |
| `test_benchmark_comparison.py` | Benchmark comparison table generation |
| `test_three_layer_evaluation.py` | Full 3-layer methodology integration |
| `test_business_adherence.py` | Business-adherence scorer: violation, escalation, inconsistency |

### Policy & RAG Tests

| Script | What it tests |
|---|---|
| `test_policy_ingestion.py` | Policy chunking, overlap, embedding |
| `test_policy_graph_node.py` | DAG node prerequisite enforcement |
| `test_policy_retrieval_decision.py` | Self-RAG retrieve/skip gate |
| `test_retrieve_policy.py` | Policy retrieval endpoint |
| `test_sequential_resolution_loop.py` | Multi-issue sequential resolution |
| `test_tool_audit_logging.py` | Every tool call appears in audit_logs |

---

## Running the Tests

```bash
# All tests (from repo root, venv active)
# macOS/Linux:
for f in scripts/test_*.py; do python -B "$f" || break; done

# Windows PowerShell:
Get-ChildItem scripts/test_*.py | ForEach-Object { python -B $_.FullName }

# Single test:
python -B scripts/test_duplicate_charge.py
```

Expected output: each script prints `<module> tests passed` or `PASS <description> tests`.

---

## Key Design Principles

### 1. No LLM mocking — deterministic fallbacks
The LLM client falls back to rule-based classification and templated responses when no `GEMINI_API_KEY` is set. Tests exercise these fallbacks. This means:
- Tests always pass in CI without API credentials
- Fallback behaviour is tested as a first-class path
- There is no divergence between "tested" and "deployed" behaviour

### 2. Seeded, deterministic database
All integration tests use `backend.db.seed_billing` and `backend.db.seed_customers` to populate a fresh SQLite DB. The seed is deterministic (no `random` at import time). The planted scenario (`INV-8821` duplicate for `CUST-1001`) is asserted across multiple test files.

### 3. Real DB, no ORM mocking
Tests call the actual SQLite functions. A schema change that breaks the app will break the tests. This is intentional — ORM mocking would hide schema drift.

### 4. Test isolation via temp databases
Each test that writes to the DB creates a fresh `sqlite3` connection to a temporary path (via `tempfile.mkstemp()`), then deletes it on teardown. No shared state between test files.

### 5. One assertion per intent
Each test function tests one behaviour. Multi-behaviour tests that fail give ambiguous diagnostics.

---

## Coverage Gaps

| Area | Gap | Planned fix |
|---|---|---|
| Frontend | No automated browser tests | Manual testing; Playwright tests planned |
| Multi-language | No assertion that translated text preserves IDs/amounts | Unit test for `_localize_response` |
| Temperature variation | `pass@k == pass@1` (deterministic eval) | Add seed/temperature sweep |
| RAG page UI | No test for `rag_routes` fallback result rendering | Component test |
| WebSocket/SSE | No test for mid-stream cancellation | Async integration test |

---

## Adding a New Test

1. Create `scripts/test_<feature>.py`
2. Import the module under test directly: `from backend.agent.my_module import my_function`
3. Use a temp DB: `db_path = tmp_path / "test.db"` (or `tempfile.mkstemp()[1]`)
4. Seed it: call `init_schema(db_path)` + relevant seeders
5. Assert with plain `assert`; print `"<feature> tests passed"` on success
6. Run: `python -B scripts/test_<feature>.py`
