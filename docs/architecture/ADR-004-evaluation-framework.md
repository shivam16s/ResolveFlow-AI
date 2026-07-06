# ADR-004: Three-Layer Evaluation Framework

**Status:** Accepted
**Date:** 2026-05-23
**Deciders:** Project team

---

## Context

Agent evaluation for customer-support systems is notoriously hard. Pure accuracy metrics miss policy compliance failures; pure LLM-judge metrics are expensive and non-deterministic; RAGAS alone measures retrieval quality but not whether the agent committed the correct action.

We need a framework that is:
1. **Reproducible** — same inputs → same result, no randomness
2. **Multi-dimensional** — correctness + retrieval quality + policy compliance
3. **Research-grounded** — defensible against published benchmarks
4. **Fast** — runs in CI without a live LLM key

---

## Decision

A **three-layer methodology**:
1. **Deterministic pass/fail** — DB-state assertions + policy-gate checks
2. **RAGAS context metrics** — context recall + context precision over policy retrievals
3. **Business-adherence scoring** — policy violation rate, missed escalation rate, inconsistency rate

---

## Options Considered

### Option A: LLM-as-Judge Only
**Pros:** Handles nuanced open-ended responses.
**Cons:** Expensive, non-deterministic, not runnable without API key, cannot verify DB state.

### Option B: RAGAS Only
**Pros:** Well-established RAG evaluation; measures retrieval quality.
**Cons:** Measures retrieval, not action correctness; misses whether the agent committed the right action or violated policy.

### Option C: Three-Layer ← **Chosen**
**Pros:**
- Layer 1 (deterministic) verifies the ground truth: did the tool get called? did the record get created?
- Layer 2 (RAGAS) measures the quality of what was retrieved
- Layer 3 (business-adherence) measures whether the agent violated business policy — the metric most correlated with real customer harm (per arXiv 2601.00596)
- All three layers run without a live LLM key
- Defensible in academic framing against τ-bench, τ²-bench, LongMemEval

**Cons:**
- More engineering to implement
- Pass rates are deterministic (`pass@5 == pass@1`); temperature variation is planned work

---

## Layer 1: Deterministic Evaluation

**Runner:** `backend/evaluation/runner.py`
**Scenarios:** `data/evaluation_scenarios.json` (13 strict scenarios)

Each scenario specifies:
- `goal_state` — required tool calls, DB records, audit assertions
- `success_criteria` — natural-language description
- `expected_artifacts` — specific field values to assert

The runner plays back the scenario, then queries the DB to verify the expected state. No LLM involved in the assertion step.

**Current results:** 100% pass rate across 13 scenarios (v3).

### DB-State Verification

```python
# Example: verify credit was applied to the correct invoice
assert credit_row["amount"] == expected_artifacts["credit_amount"]
assert credit_row["applied_to_invoice"] == expected_artifacts["invoice_id"]
assert credit_row["policy_status"] == "compliant"
```

---

## Layer 2: RAGAS Context Metrics

**Module:** `backend/evaluation/ragas.py`

For each policy retrieval in the evaluation run, compute:

| Metric | Definition |
|---|---|
| `context_recall` | Fraction of answer terms present in the retrieved context |
| `context_precision` | Fraction of retrieved context terms present in the answer |

These are computed without an LLM (term-overlap approximation) to keep the suite runnable offline. Numeric tokens are excluded from term sets to avoid artificially high scores from shared numeric values.

**Current results:** context_recall ≈ 0.22 (sparse policies vs. answer terms), context_precision ≈ 0.95 (retrieved context is precise).

---

## Layer 3: Business-Adherence (arXiv 2601.00596)

**Module:** `backend/evaluation/business_adherence.py`

Scores three failure modes identified in the Beyond IVR paper:

| Dimension | Definition | Measurement |
|---|---|---|
| `POLICY_VIOLATION` | Agent committed an action explicitly forbidden by policy | Check for `PolicyActionBlocked` in tool failure messages |
| `MISSED_ESCALATION` | Agent failed to escalate when the scenario required it | Check for expected `handoff` in scenarios flagged `requires_escalation` |
| `INCONSISTENT_RULES` | Agent gave different verdicts on identical scenarios | Compare pass/fail across repeated runs of the same scenario |

**Current results:** 100% across all three dimensions (0/26 policy violations, 0/10 missed escalations, 0/13 inconsistencies).

---

## Benchmark Framing

`backend/evaluation/benchmark.py` compares ResolveFlow against published τ-bench-style baselines (< 50% pass rate for realistic tool-use customer-service agents). The comparison is used to contextualize the 100% pass rate: our scenarios are deterministic and authored by the same team, not held-out, so the comparison is for framing only.

---

## Trade-off Analysis

The 100% pass rate over 13 deterministic scenarios is a strong result but has acknowledged limitations: the scenarios are authored by the same team, the runner is deterministic, and the scenario count is small. This is disclosed prominently in the README and the evaluation page. Adding temperature variation and a larger held-out scenario set is future work.

---

## Consequences

- Evaluation results are available at `/api/evaluation/results` and on the `/evaluation` frontend page.
- Runs are persisted as `data/eval_YYYYMMDD_HHMMSS_*.json`.
- The evaluation suite runs in < 30s without an API key and < 2m with Gemini.
- `avg_ragas_context_recall` and `avg_ragas_context_precision` are now distinct metrics (previously conflated — fixed 2026-07-01).
- `business_adherence` is exposed as a top-level field in the API response and rendered as a separate panel on the evaluation page.

## Action Items
- [x] Implement pass^k runner with DB-state verification
- [x] Implement RAGAS term-overlap approximation (offline)
- [x] Implement business-adherence scorer (3 dimensions)
- [x] Wire results to `/api/evaluation/results`
- [ ] Add temperature-varied `pass@k` with seed variation
- [ ] Expand scenario set to ≥ 25 scenarios
