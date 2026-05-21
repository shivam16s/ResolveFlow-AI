# ResolveFlow AI

An agentic AI system for telecom customer support, built with tool-calling, policy-grounded retrieval, and multi-issue intent detection.

## Overview

ResolveFlow AI is a production-style AI agent that handles complex telecom customer support scenarios including billing disputes, outage checks, router diagnostics, plan changes, and technician scheduling.

## Key Features

- **Tool-Calling Layer** — FastAPI backend exposing structured tools (lookup, billing, diagnostics, credits, tickets)
- **Policy-Grounded Retrieval (CRAG/Self-RAG)** — ChromaDB-backed policy retrieval with relevance evaluation and correction paths
- **Multi-Issue Intent Detection** — Identifies and resolves multiple concurrent customer issues in a single session
- **Audit Logging** — Every tool call logged to `audit_logs` table for compliance and traceability

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
