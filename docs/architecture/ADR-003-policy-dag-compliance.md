# ADR-003: Policy DAG for Compliance Enforcement

**Status:** Accepted
**Date:** 2026-05-21
**Deciders:** Project team

---

## Context

Several agent actions carry real customer impact: applying a credit, creating a cancellation, downgrading a plan, dispatching a technician. If the LLM hallucinates a precondition ("the customer confirmed") or misreads the policy, it can commit actions that violate business rules.

Two approaches were considered:
- **Soft enforcement:** include the policy in the LLM prompt and trust the model to follow it.
- **Hard enforcement:** a programmatic DAG that blocks actions unless prerequisites are verified.

---

## Decision

Use a **directed acyclic graph (DAG)** where each high-risk tool call must traverse a prerequisite node sequence before being allowed. Violations raise `PolicyActionBlocked` at the Python layer — the LLM cannot override this.

---

## Options Considered

### Option A: Prompt-Only Policy Enforcement
| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Safety | Low — LLM can "forget" or misinterpret |
| Auditability | Hard (no structured record of which policy clause was checked) |
| Determinism | None |

**Pros:** Zero code complexity; natural language policy is easy to update.
**Cons:** No guarantee of compliance; cannot prove to an auditor that the policy was enforced; a confused or adversarial prompt can bypass all constraints.

### Option B: Policy DAG (Code-Level Enforcement) ← **Chosen**
| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Safety | High — Python raises an exception; LLM cannot bypass |
| Auditability | High — full DAG path recorded in `audit_logs` |
| Determinism | Full |

**Pros:**
- Compliance is guaranteed regardless of LLM output
- Every traversal produces a `policy_dag_path` list for the audit log
- `PolicyActionBlocked` surfaces a structured error the agent can explain to the customer
- UJCS (Unit of Justified Compliance Score) gives a continuous compliance signal (0–1)
- Aligns with JourneyBench and SOP-Bench research on structured policy compliance

**Cons:**
- DAG must be maintained as policy evolves
- Requires translating prose policy into a machine-readable DAG

---

## DAG Structure

Each policy DAG is a JSON file in `data/policies/`. Example: `service_credit_dag`:

```
verify_customer → check_duplicate_charge → confirm_credit_amount
    → within_credit_limit → apply_credit ✓
```

The `PolicyGraphValidator` walks the required node sequence before allowing the action. If a required node is unvisited, it raises `PolicyActionBlocked(reason="prerequisite not met: {node_id}")`.

---

## UJCS Score

UJCS = `(nodes_correctly_visited / total_required_nodes)` weighted by node criticality.

| Score | `policy_status` |
|---|---|
| > 0.8 | `compliant` |
| 0.0–0.8 | `needs_review` |
| 0.0 | `non_compliant` |

UJCS is stored in `audit_logs` and surfaced on the case detail page and evaluation results.

---

## Trade-off Analysis

The Beyond IVR paper (arXiv 2601.00596) shows that even GPT-4-class agents violate business policy in ~30% of tested scenarios. Prompt-only enforcement is insufficient. Code-level enforcement provides a hard guarantee at the cost of maintaining the DAG; for a telecom support system where a wrongly applied credit is a real financial event, this trade-off is clearly correct.

---

## Consequences

- `apply_credit`, `create_ticket`, `schedule_technician`, `change_plan` all go through `PolicyGraphValidator` before executing.
- The LLM is told the action was blocked via `PolicyActionBlocked` and can apologize / explain to the customer.
- All DAG traversals are recorded in `audit_logs.policy_dag_path`.
- Business-adherence evaluation (`backend/evaluation/business_adherence.py`) specifically checks for policy violations surfaced as blocked actions.

## Action Items
- [x] Implement `PolicyGraphValidator` in `policy_graph.py`
- [x] Define DAG files for all 4 high-risk actions
- [x] Wire `PolicyActionBlocked` → HTTP 409 in tool endpoints
- [x] Record `policy_dag_path` + `ujcs` in `audit_logs` on every tool call
