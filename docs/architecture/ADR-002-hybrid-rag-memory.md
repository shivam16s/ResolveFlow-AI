# ADR-002: Hybrid RAG — HippoRAG PPR + ChromaDB + SQLite Fallback

**Status:** Accepted
**Date:** 2026-05-22
**Deciders:** Project team

---

## Context

The agent needs two distinct retrieval subsystems:

1. **Policy retrieval** — fetch the governing clause from one of 8 policy documents before taking a high-risk action (Self-RAG / CRAG pattern).
2. **Customer memory retrieval** — recall facts about the specific customer (billing history, past complaints, relationship context) from prior session transcripts (LongMemEval / HippoRAG pattern).

Both must work without an expensive embedding API call on the critical path, and the memory subsystem must not return empty results just because the optional indexing step was skipped.

---

## Decision

**Policy retrieval:** ChromaDB vector store (`resolveflow_policies` collection) with chunked policy documents. Retrieve-or-skip decision uses a Self-RAG gate; a CRAG corrective step reformulates the query on low-confidence retrieval.

**Customer memory retrieval:** Hybrid RRF fusion of:
1. ChromaDB vector similarity (dense)
2. SQLite-backed HippoRAG PPR graph traversal (graph proximity)
3. SQLite keyword fallback (`memory_store` table) — always available, even without indexing

---

## Options Considered

### Option A: Pure Vector Search (ChromaDB only)
**Pros:** Simple, fast.
**Cons:** Returns 0 results if the collection is empty (no indexing step run); misses multi-hop facts that require graph traversal (e.g., "what plan was the customer on when they last complained about billing?").

### Option B: Pure Graph (Neo4j / NetworkX)
**Pros:** Best multi-hop recall.
**Cons:** External dependency (Neo4j) or in-memory only (NetworkX, lost on restart); no semantic similarity on free-text queries.

### Option C: Hybrid RRF + SQLite Fallback ← **Chosen**
**Pros:**
- Vector handles semantic similarity ("similar complaints")
- Graph (PPR) handles relational proximity ("this customer's plan → related billing events")
- SQLite fallback (`memory_store`) ensures non-empty results for all customers, no indexing required
- RRF fusion is parameter-free and well-studied
- All storage is local (no external service)

**Cons:**
- More moving parts than pure vector
- Indexing step required to populate the vector + graph stores (optional but recommended)

---

## Memory Architecture

```
Customer Memory (3 tiers):
  Stable    — long-lived facts (plan, risk level, location, preferences)
  Episodic  — per-session summaries (indexed after session close)
  Session   — in-flight context (current turn)

Storage:
  memory_store (SQLite)   ← always populated by seed / session indexer
  ChromaDB collection     ← populated by index_demo_data.py (optional)
  memory_graph (SQLite)   ← OpenIE triples → HippoRAG nodes + edges (optional)

Retrieval path:
  query
    ├─ ChromaDB vector search      → ranked list A
    ├─ PPR graph traversal          → ranked list B
    └─ RRF fusion                   → fused_score
  if fused_score list is empty:
    └─ SQLite keyword fallback      → keyword-scored list from memory_store
```

## HippoRAG PPR Details

OpenIE triples are extracted from session transcripts by an LLM call at indexing time. Triples are stored as nodes + weighted edges in `memory_graph`. At retrieval time, Personalized PageRank (PPR) seeds from the query terms and propagates through the graph to surface contextually proximate facts.

---

## Trade-off Analysis

The fallback is the key architectural decision. Without it, the knowledge-base page would show empty results for any deployment that skipped the indexing step. The fallback uses term-overlap scoring (simple but reliable) and sorts by `updated_at` descending so the most recent memories appear first when no terms match.

---

## Consequences

- The `MemoryManager` class always returns results — either from vector+graph or from SQLite fallback.
- `_memory_store_fallback` in `rag_routes.py` is the fallback implementation; it emits `sources: ["memory_store"]` so callers can distinguish real hybrid results from fallback results.
- The ChromaDB memory collection and `memory_graph` table are optional; the demo seeds `memory_store` directly.
- Running `python -m backend.scripts.index_demo_data` upgrades from fallback to full hybrid retrieval.

## Action Items
- [x] Implement `MemoryManager` with RRF fusion
- [x] Implement OpenIE extractor (`openie.py`)
- [x] Implement `_memory_store_fallback` in `rag_routes.py`
- [x] Add `index_demo_data.py` indexer script
- [ ] Add temperature-varied indexing to improve graph diversity
