# ResolveFlow AI

An agentic AI system for telecom customer support, built with tool-calling, policy-grounded retrieval, and multi-issue intent detection.

## Overview

ResolveFlow AI is a production-style AI agent that handles complex telecom customer support scenarios including billing disputes, outage checks, router diagnostics, plan changes, and technician scheduling.

## Key Features

- **Tool-Calling Layer** — FastAPI backend exposing structured tools (lookup, billing, diagnostics, credits, tickets)
- **Policy-Grounded Retrieval (CRAG/Self-RAG)** — ChromaDB-backed policy retrieval with relevance evaluation and correction paths
- **Multi-Issue Intent Detection** — Identifies and resolves multiple concurrent customer issues in a single session
- **Audit Logging** — Every tool call logged to `audit_logs` table for compliance and traceability

## Evaluation Notes

The current evaluation runner is deterministic, so `pass@5` is equivalent to `pass@1` until temperature and seed variation are added. The latest strict backend run passes 13/13 scenarios with database-state verification, policy gates, audit checks, targeted clarification checks, and multi-issue acknowledgment coverage.

| Run | Pass Rate | Change | Notes |
| --- | ---: | --- | --- |
| v1 | 69.2% | Initial strict run | Exposed failures in angry, vague, and impatient-user cases. |
| v2 | 76.9% | DB-state verification active | Confirmed remaining failures were real agent behavior, not fake metrics. |
| v3 | 100.0% | Clarification and acknowledgment fixes | Cases 06, 07, and 11 now pass with strict checks. |

Benchmark framing: deterministic ResolveFlow evaluation is compared against published tau-bench-style SOTA below 50% for realistic tool-use customer-service agents. LLM temperature variation is planned as a future extension.

## Tech Stack

- Python / FastAPI
- SQLite (customer, invoice, outage, audit data)
- ChromaDB (policy vector store)
- LLM with tool-use and self-reflection (Self-RAG / CRAG)

## Project Structure

```
backend/        # FastAPI tool-calling layer
docs/           # Problem documentation and research
tasks.md        # Build checklist
solution.txt    # Solution design notes
```

## Getting Started

_Setup instructions coming soon._
