# ResolveFlow AI — Build Checklist

Mark a task done by changing `[ ]` to `[x]`.

---

## Foundation (Days 1–3)

- [x] Create SQLite schema (13 tables — see Appendix B)
- [x] Seed 20 customers with realistic telecom data
- [x] Seed 20 invoices, payment records, duplicate charge scenario
- [x] Seed 10 outage records (verified + unverified)
- [x] Write 8 policy text documents (credit, refund, cancellation, etc.)
- [x] Write 20 customer test scenario scripts
- [x] Two-model LLM client split: `LLMClient(model="primary"|"secondary")`
- [x] Foundation integrity validator for schema, seed counts, policies, and scenarios

---

## Feature 1 — Multi-Issue Intent Detecti

- [x] LLM intent classifier with structured JSON output
- [x] Detect multiple intents from single message
- [x] Build `issue_queue` with priority ordering
- [x] Sequential resolution loop (one issue at a time)
- [x] Acknowledgment response covers all detected issuon (Days 4–6)
      es
- [x] Slot completion progress per issue
- [x] Next missing slot helper for queued issues

---

## Feature 2 — Customer Memory Layer (Days 7–10)

- [x] `decompose_to_memory_units()` — split session into atomic facts
- [x] Embed and store in ChromaDB with metadata (type, timestamp, customer_id)
- [x] LongMemEval Stage 2: `fact_augmented_expansion()` query expansion
- [x] LongMemEval Stage 2: `time_aware_expansion()` temporal query expansion
- [x] Vector search + BM25 with rank fusion
- [x] HippoRAG: OpenIE triple extraction via LLM (1-shot prompt)
- [x] HippoRAG: Build/update `memory_graph` table with nodes + edges
- [x] HippoRAG: Synonymy edges at cosine similarity τ = 0.8
- [x] HippoRAG: PPR retrieval — personalized vector + damping 0.5
- [x] LongMemEval Stage 3: `llm_read_with_citation()` with abstention
- [x] `MemoryManager.index_session()` — called at session close
- [x] `MemoryManager.retrieve()` — merge vector + graph results
- [x] Memory citation context builder for final answer prompts
- [x] Formatted memory citation block with stable IDs

---

## Feature 3 — Policy-Grounded Retrieval (Days 11–14)

- [x] Ingest 8 policy docs into ChromaDB (`resolveflow_policies` collection)
- [x] Chunk to 300-token paragraphs with 50-token overlap
- [x] Self-RAG: `[Retrieve]` token decision logic
- [x] CRAG: relevance evaluator (LLM-as-judge prompt)
- [x] CRAG: CORRECT path — strip decomposition + re-scoring
- [x] CRAG: INCORRECT path — keyword query rewrite + retry
- [x] CRAG: AMBIGUOUS path — combine internal + external strips
- [x] `[IsSup]` and `[IsUse]` scoring on final answer
- [x] Final answer evidence gate using support/usefulness thresholds

---

## Feature 4 — Policy Graph / Policy DAG (Days 15–17)

- [x] `PolicyNode` dataclass
- [x] `service_credit_dag` — 6-node DAG with outage + duration + prior credit checks
- [x] `duplicate_charge_refund_dag`
- [x] `cancellation_retention_dag`
- [x] `technician_dispatch_dag`
- [x] `plan_downgrade_dag`
- [x] `refund_exception_dag`
- [x] `PolicyGraphValidator.run()` — DPA traversal engine
- [x] UJCS computation per traversal
- [x] Block action at code level if prerequisite DAG nodes not visited

---

## Feature 5 — Tool-Calling Layer (Days 18–21)

- [x] FastAPI project scaffold (`backend/`)
- [x] `lookup_customer()`
- [x] `get_invoice_history()`
- [x] `check_duplicate_charge()`
- [x] `check_outage_status()`
- [x] `run_router_diagnostic()`
- [x] `retrieve_policy()`
- [x] `apply_credit()` (with policy validation gate)
- [x] `create_ticket()`
- [x] `schedule_technician()`
- [x] `change_plan()`
- [x] `generate_handoff_summary()`
- [x] `generate_audit_log()`
- [x] Tool call logging to `audit_logs` table on every call

---

## Feature 6 — Clarification Engine (Days 22–24)

- [x] Slot schema defined for all intents (billing, outage, cancellation, etc.)
- [x] Missing required slot detection
- [x] `prioritize_slot()` — ask highest-priority slot first
- [x] Targeted question generator (one slot per question, no vague asks)
- [x] `decide_next_action()` — ANSWER / ASK / CALL_TOOL / HANDOFF decision

---

## Feature 6A — Guided User Action Coordinator (Days 22–24)

- [x] `GuidedActionCoordinator` class with state enum
- [x] `instruct()` — single-step instruction generation, enters WAITING
- [x] `handle_user_report()` — re-runs verification tool (never trusts claim)
- [x] Retry logic (MAX_ATTEMPTS = 2) with clearer second instruction
- [x] Escalation path on FAILED state → Feature 8 handoff
- [x] Audit log at every state transition
- [x] Action-to-tool mapping (router_reset → `run_router_diagnostic`, etc.)

---

## Feature 7 — Conversation Health Score (Days 25–27)

- [x] `intent_confidence` component from classifier softmax
- [x] `missing_info_risk` component from slot schema
- [x] `sentiment_score` component — LLM sentiment classifier on last 3 messages
- [x] `loop_penalty` component — detect repeated questions (2×/3× threshold)
- [x] `knowledge_coverage` component — tool call + CRAG confidence
- [x] `compute_health_score()` — weighted formula H = 0.30·ic + 0.25·(1−mr) + 0.20·ss + 0.15·(1−lp) + 0.10·kc
- [x] `get_recommended_action()` — threshold routing (≥70/50–70/30–50/<30)
- [x] `compute_relationship_score()` — exponential decay over past 5 sessions
- [x] Session-start behavior based on relationship score (HEALTHY / DRIFTING / AT-RISK)
- [x] CASA empathy sequence for AT-RISK customers (score < 40)
- [x] Persist `relationship_score_start`, `relationship_score_end`, `relationship_delta` to `conversations` table

---

## Feature 8 — Warm Human Handoff (Days 28–29)

- [x] Trigger condition detection (8 triggers — policy exception, score < 30, anger, loop, churn risk, tool failure, refund > ₹500, explicit request)
- [x] Insert into `human_handoff_queue` table
- [x] Customer-facing message ("connecting you to a specialist…")
- [x] Handoff event logged to audit trail

---

## Feature 9 — Customer Context Card (Days 28–29)

- [x] `generate_context_card()` — full card dict builder
- [x] Resolved vs. remaining issues summary
- [x] Policy DAG path traversed so far
- [x] `generate_opening_line()` — recommended first sentence for human agent
- [x] Rendered card display in dashboard (Case Detail, Handoff tab)

---

## Feature 10 — Resolution Proof Trail (Days 28–29)

- [x] `build_audit_log()` — assembles evidence, tools, DAG path, actions
- [x] UJCS computed and stored
- [x] `policy_status` = "compliant" if UJCS > 0.8
- [x] Human-readable tab + raw JSON tab in dashboard
- [x] Persisted to `audit_logs` table

---

## Feature 11 — Agent Evaluation Harness (Days 30–32)

- [x] `db.reset_to_initial_state()` — restore known DB state per test case
- [x] All 13 test scenario definitions with `initial_state` + `goal_state`
  - [x] case_01 — simple bill question
  - [x] case_02 — duplicate charge
  - [x] case_03 — outage credit
  - [x] case_04 — cancellation intent
  - [x] case_05 — policy exception (₹2000 refund)
  - [x] case_06 — angry customer
  - [x] case_07 — vague customer
  - [x] case_08 — wrong refund request (>30 days)
  - [x] case_09 — tool failure injection
  - [x] case_10 — repeated question loop
  - [x] case_11 — impatient user (preserves queue, no restart)
  - [x] case_12 — tangential user (digression + return to primary issue)
  - [x] case_13 — unavailable service request (policy denial, no hallucination)
- [x] `run_evaluation()` — pass^k runner (k=5, 13 cases)
- [x] 9-metric report generation (includes `non_collaborative_degradation`)
- [x] RAGAS evaluation layer — faithfulness + context precision on all policy retrievals
- [x] Three-layer evaluation methodology (deterministic + RAGAS + human review)
- [x] Benchmark comparison output vs. τ-bench SOTA baselines (includes RAGAS rows)

---

## Feature 12 — Admin Dashboard (Days 33–36)

- [x] React + Next.js + Tailwind project scaffold (`frontend/`)
- [x] Chat interface with message bubbles (customer / bot turns)
- [x] Agent reasoning panel (right side):
  - [x] Detected intents list
  - [x] Health score live timeline (color-coded)
  - [x] Relationship score arc display (`29 → 58, Churn risk: Reduced`)
  - [x] Tools called with args + results
  - [x] Policy DAG path (graphical)
  - [x] Policies retrieved with confidence scores
  - [x] Memory retrieved with citations
  - [x] Guided action states (WAITING / VERIFYING / RESOLVED)
- [x] Page 1 — Overview: KPI cards + 4 charts (resolution trend, issue types, tool frequency, health distribution)
- [x] Page 2 — Case Browser: sortable table with click-through
- [x] Page 3 — Case Detail: transcript + reasoning panel + proof trail + context card
- [x] Page 4 — Evaluation: test scenario results + metric trend charts
- [x] FastAPI endpoints: `/api/dashboard/overview`, `/api/cases`, `/api/cases/{id}`, `/api/cases/{id}/audit_log`, `/api/cases/{id}/context_card`, `/api/evaluation/results`, `/api/evaluation/run`

---

## Demo Polish (Days 37–39)

- [ ] Easy demo path: customer asks for bill → tool call → response
- [ ] Hard demo path: duplicate charge + outage + cancellation (all 3 intents)
- [ ] Hard demo includes Feature 6A router reset moment (WAITING → VERIFYING → 183 Mbps)
- [ ] Hard demo shows relationship score arc (29 → 58, churn recovered)
- [ ] README with system architecture diagram
- [ ] Primary demo video recorded (deterministic mock backend, two takes)
- [ ] Fallback demo video recorded (pre-rendered, no live system dependency)
- [ ] Both video URLs tested from incognito browser
- [ ] Submission final checklist completed (see Appendix D)
