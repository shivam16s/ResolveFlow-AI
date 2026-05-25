// ── Core entity types ────────────────────────────────────────────────────────

export interface KpiOverview {
  total_cases_today: number;
  resolved_by_ai_pct: number;
  escalated_pct: number;
  policy_compliant_pct: number;
  credits_applied_count: number;
  credits_applied_total_inr: number;
  tickets_created: number;
  high_risk_customers: number;
  avg_health_score: number;
}

export interface ResolutionPoint { date: string; resolved: number; escalated: number }
export interface IssueTypePoint  { name: string; value: number; color: string }
export interface ToolFreqPoint   { tool: string; calls: number }
export interface HealthBucket    { range: string; count: number; color: string }

export interface OverviewCharts {
  resolution_trend: ResolutionPoint[];
  issue_distribution: IssueTypePoint[];
  tool_frequency: ToolFreqPoint[];
  health_distribution: HealthBucket[];
}

// ── Case list ─────────────────────────────────────────────────────────────────

export type CaseStatus = "resolved" | "escalated" | "in_progress" | "open";

export interface CaseRow {
  case_id: string;
  route_id?: string;
  customer_name: string;
  customer_id: string;
  issues: string[];
  status: CaseStatus;
  health_score: number;
  relationship_score_start: number | null;
  relationship_score_end: number | null;
  created_at: string;
  turns: number;
}

export interface CaseListResponse {
  cases: CaseRow[];
  total: number;
  page: number;
  limit: number;
}

// ── Case detail ───────────────────────────────────────────────────────────────

export type MessageRole = "user" | "assistant" | "system";

export interface Message {
  role: MessageRole;
  content: string;
  timestamp: string;
  turn: number;
}

export interface ToolCall {
  tool_name: string;
  args: Record<string, unknown>;
  result: Record<string, unknown>;
  timestamp: string;
  success: boolean;
}

export interface PolicyDagNode {
  node_id: string;
  description: string;
  visited: boolean;
  result?: string;
}

export interface PolicyDagEdge {
  from: string;
  to: string;
  label: string;
  traversed: boolean;
}

export interface PolicyDagPath {
  dag_name: string;
  nodes: PolicyDagNode[];
  edges: PolicyDagEdge[];
  ujcs: number;
  action_taken: string;
  policy_status: "compliant" | "non_compliant" | "needs_review" | "pending";
}

export interface HealthScorePoint {
  turn: number;
  score: number;
  label: string;
}

export type GuidedState = "IDLE" | "WAITING" | "VERIFYING" | "RESOLVED" | "FAILED" | "ESCALATED";

export interface GuidedActionEvent {
  state: GuidedState;
  reason: string;
  attempt: number;
  timestamp: string;
  signal_strength?: number;
}

export interface MemoryCitation {
  citation_id: string;
  content: string;
  type: string;
  timestamp: string;
  confidence: number;
}

export interface PolicyRetrieval {
  policy_name: string;
  chunk: string;
  confidence: number;
  crag_path: "CORRECT" | "INCORRECT" | "AMBIGUOUS";
}

export interface CaseDetail {
  case_id: string;
  customer_id: string;
  customer_name: string;
  status: CaseStatus;
  messages: Message[];
  tools_called: ToolCall[];
  policy_dag_path: PolicyDagPath | null;
  health_score_timeline: HealthScorePoint[];
  relationship_score_start: number | null;
  relationship_score_end: number | null;
  guided_action_events: GuidedActionEvent[];
  memory_citations: MemoryCitation[];
  policy_retrievals: PolicyRetrieval[];
  intents_detected: string[];
  final_health_score: number;
  ujcs: number | null;
  created_at: string;
}

// ── Audit log ─────────────────────────────────────────────────────────────────

export interface AuditLogEntry {
  case_id: string;
  customer_id: string;
  session_id: string;
  tools_called: string[];
  evidence_used: string[];
  action_taken: string;
  policy_dag_path: string[];
  ujcs: number | null;
  policy_status: "compliant" | "non_compliant" | "needs_review" | "pending";
  human_readable: string;
  created_at: string;
}

// ── Evaluation ────────────────────────────────────────────────────────────────

export interface ScenarioResult {
  case_id: string;
  scenario_name: string;
  pass_k: number;
  avg_turns: number;
  policy_compliance: number;
  ragas_faithfulness: number;
  ragas_context_precision: number;
  non_collaborative_degradation: number;
  status: "pass" | "fail" | "partial";
}

export interface EvaluationReport {
  run_id: string;
  run_at: string;
  total_scenarios: number;
  pass_rate: number;
  avg_pass_k: number;
  avg_policy_compliance: number;
  avg_ragas_faithfulness: number;
  scenarios: ScenarioResult[];
}
