# ResolveFlow AI — Build Checklist

Mark a task done by changing `[ ]` to `[x]`.

---

## Foundation (Days 1–3)

- [ ] Create SQLite schema (13 tables — see Appendix B)
- [ ] Seed 20 customers with realistic telecom data
- [ ] Seed 20 invoices, payment records, duplicate charge scenario
- [ ] Seed 10 outage records (verified + unverified)
- [ ] Write 8 policy text documents (credit, refund, cancellation, etc.)
- [ ] Write 20 customer test scenario scripts

---

## Feature 5 — Tool-Calling Layer (Days 4–7)

- [ ] FastAPI project scaffold (`backend/`)
- [ ] `lookup_customer()`
- [ ] `get_invoice_history()`
- [ ] `check_duplicate_charge()`
- [ ] `check_outage_status()`
- [ ] `run_router_diagnostic()`
- [ ] `retrieve_policy()`
- [ ] `apply_credit()` (with policy validation gate)
- [ ] `create_ticket()`
- [ ] `schedule_technician()`
- [ ] `change_plan()`
- [ ] `generate_handoff_summary()`
- [ ] `generate_audit_log()`
- [ ] Tool call logging to `audit_logs` table on every call

---

## Feature 3 — Policy-Grounded Retrieval (Days 8–11)

- [ ] Ingest 8 policy docs into ChromaDB (`resolveflow_policies` collection)
- [ ] Chunk to 300-token paragraphs with 50-token overlap
- [ ] Self-RAG: `[Retrieve]` token decision logic
- [ ] CRAG: relevance evaluator (LLM-as-judge prompt)
- [ ] CRAG: CORRECT path — strip decomposition + re-scoring
- [ ] CRAG: INCORRECT path — keyword query rewrite + retry
- [ ] CRAG: AMBIGUOUS path — combine internal + external strips
- [ ] `[IsSup]` and `[IsUse]` scoring on final answer

---

## Feature 1 — Multi-Issue Intent Detection (Days 12–15)

- [ ] LLM intent classifier with structured JSON output
- [ ] Detect multiple intents from single message
- [ ] Build `issue_queue` with priority ordering
- [ ] Sequential resolution loop (one issue at a time)
- [ ] Acknowledgment response covers all detected issues

---

## Feature 6 — Clarification Engine (Days 12–15)

- [ ] Slot schema defined for all intents (billing, outage, cancellation, etc.)
- [ ] Missing required slot detection
- [ ] `prioritize_slot()` — ask highest-priority slot first
- [ ] Targeted question generator (one slot per question, no vague asks)
- [ ] `decide_next_action()` — ANSWER / ASK / CALL_TOOL / HANDOFF decision

---

## Feature 6A — Guided User Action Coordinator (Days 12–15)

- [ ] `GuidedActionCoordinator` class with state enum
- [ ] `instruct()` — single-step instruction generation, enters WAITING
- [ ] `handle_user_report()` — re-runs verification tool (never trusts claim)
- [ ] Retry logic (MAX_ATTEMPTS = 2) with clearer second instruction
- [ ] Escalation path on FAILED state → Feature 8 handoff
- [ ] Audit log at every state transition
- [ ] Action-to-tool mapping (router_reset → `run_router_diagnostic`, etc.)

---

## Feature 7 — Conversation Health Score (Days 12–15)

- [ ] `intent_confidence` component from classifier softmax
- [ ] `missing_info_risk` component from slot schema
- [ ] `sentiment_score` component — LLM sentiment classifier on last 3 messages
- [ ] `loop_penalty` component — detect repeated questions (2×/3× threshold)
- [ ] `knowledge_coverage` component — tool call + CRAG confidence
- [ ] `compute_health_score()` — weighted formula H = 0.30·ic + 0.25·(1−mr) + 0.20·ss + 0.15·(1−lp) + 0.10·kc
- [ ] `get_recommended_action()` — threshold routing (≥70/50–70/30–50/<30)
- [ ] `compute_relationship_score()` — exponential decay over past 5 sessions
- [ ] Session-start behavior based on relationship score (HEALTHY / DRIFTING / AT-RISK)
- [ ] CASA empathy sequence for AT-RISK customers (score < 40)
- [ ] Persist `relationship_score_start`, `relationship_score_end`, `relationship_delta` to `conversations` table

---

## Feature 4 — Policy Graph / Policy DAG (Days 16–18)

- [ ] `PolicyNode` dataclass
- [ ] `service_credit_dag` — 6-node DAG with outage + duration + prior credit checks
- [ ] `duplicate_charge_refund_dag`
- [ ] `cancellation_retention_dag`
- [ ] `technician_dispatch_dag`
- [ ] `plan_downgrade_dag`
- [ ] `refund_exception_dag`
- [ ] `PolicyGraphValidator.run()` — DPA traversal engine
- [ ] UJCS computation per traversal
- [ ] Block action at code level if prerequisite DAG nodes not visited

---

## Feature 2 — Customer Memory Layer (Days 16–18)

- [ ] `decompose_to_memory_units()` — split session into atomic facts
- [ ] Embed and store in ChromaDB with metadata (type, timestamp, customer_id)
- [ ] LongMemEval Stage 2: `fact_augmented_expansion()` query expansion
- [ ] LongMemEval Stage 2: `time_aware_expansion()` temporal query expansion
- [ ] Vector search + BM25 with rank fusion
- [ ] HippoRAG: OpenIE triple extraction via LLM (1-shot prompt)
- [ ] HippoRAG: Build/update `memory_graph` table with nodes + edges
- [ ] HippoRAG: Synonymy edges at cosine similarity τ = 0.8
- [ ] HippoRAG: PPR retrieval — personalized vector + damping 0.5
- [ ] LongMemEval Stage 3: `llm_read_with_citation()` with abstention
- [ ] `MemoryManager.index_session()` — called at session close
- [ ] `MemoryManager.retrieve()` — merge vector + graph results

---

## Feature 12 — Admin Dashboard (Days 19–21)

- [ ] React + Next.js + Tailwind project scaffold (`frontend/`)
- [ ] Chat interface with message bubbles (customer / bot turns)
- [ ] Agent reasoning panel (right side):
  - [ ] Detected intents list
  - [ ] Health score live timeline (color-coded)
  - [ ] Relationship score arc display (`29 → 58, Churn risk: Reduced`)
  - [ ] Tools called with args + results
  - [ ] Policy DAG path (graphical)
  - [ ] Policies retrieved with confidence scores
  - [ ] Memory retrieved with citations
  - [ ] Guided action states (WAITING / VERIFYING / RESOLVED)
- [ ] Page 1 — Overview: KPI cards + 4 charts (resolution trend, issue types, tool frequency, health distribution)
- [ ] Page 2 — Case Browser: sortable table with click-through
- [ ] Page 3 — Case Detail: transcript + reasoning panel + proof trail + context card
- [ ] Page 4 — Evaluation: test scenario results + metric trend charts
- [ ] FastAPI endpoints: `/api/dashboard/overview`, `/api/cases`, `/api/cases/{id}`, `/api/cases/{id}/audit_log`, `/api/cases/{id}/context_card`, `/api/evaluation/results`, `/api/evaluation/run`

---

## Feature 8 — Warm Human Handoff (Days 22–24)

- [ ] Trigger condition detection (8 triggers — policy exception, score < 30, anger, loop, churn risk, tool failure, refund > ₹500, explicit request)
- [ ] Insert into `human_handoff_queue` table
- [ ] Customer-facing message ("connecting you to a specialist…")
- [ ] Handoff event logged to audit trail

---

## Feature 9 — Customer Context Card (Days 22–24)

- [ ] `generate_context_card()` — full card dict builder
- [ ] Resolved vs. remaining issues summary
- [ ] Policy DAG path traversed so far
- [ ] `generate_opening_line()` — recommended first sentence for human agent
- [ ] Rendered card display in dashboard (Case Detail, Handoff tab)

---

## Feature 10 — Resolution Proof Trail (Days 22–24)

- [ ] `build_audit_log()` — assembles evidence, tools, DAG path, actions
- [ ] UJCS computed and stored
- [ ] `policy_status` = "compliant" if UJCS > 0.8
- [ ] Human-readable tab + raw JSON tab in dashboard
- [ ] Persisted to `audit_logs` table

---

## Feature 11 — Agent Evaluation Harness (Days 25–27)

- [ ] `db.reset_to_initial_state()` — restore known DB state per test case
- [ ] All 10 test scenario definitions with `initial_state` + `goal_state`
  - [ ] case_01 — simple bill question
  - [ ] case_02 — duplicate charge
  - [ ] case_03 — outage credit
  - [ ] case_04 — cancellation intent
  - [ ] case_05 — policy exception (₹2000 refund)
  - [ ] case_06 — angry customer
  - [ ] case_07 — vague customer
  - [ ] case_08 — wrong refund request (>30 days)
  - [ ] case_09 — tool failure injection
  - [ ] case_10 — repeated question loop
- [ ] `run_evaluation()` — pass^k runner (k=5)
- [ ] 8-metric report generation
- [ ] Benchmark comparison output vs. τ-bench SOTA baselines

---

## Demo Polish (Days 28–30)

- [ ] Easy demo path: customer asks for bill → tool call → response
- [ ] Hard demo path: duplicate charge + outage + cancellation (all 3 intents)
- [ ] Hard demo includes Feature 6A router reset moment (WAITING → VERIFYING → 183 Mbps)
- [ ] Hard demo shows relationship score arc (29 → 58, churn recovered)
- [ ] README with system architecture diagram
- [ ] Demo video recorded
