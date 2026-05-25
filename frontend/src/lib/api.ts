// API calls go to Next.js dev server which proxies /api/* → backend (see next.config.ts)
const BASE = "";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Accept": "application/json", "Content-Type": "application/json" },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Accept": "application/json", "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

import type {
  AuditLogEntry,
  CaseDetail,
  CaseListResponse,
  EvaluationReport,
  KpiOverview,
  OverviewCharts,
  MemorySearchResult,
  PolicyRetrievalResult,
  MemoryGraphData,
  HealthResponse,
  ToolResponse,
  ApplyCreditRequest,
  CreateTicketRequest,
  ScheduleTechnicianRequest,
  ChangePlanRequest,
  HandoffSummaryRequest,
  ContextCardRequest,
  OpeningLineRequest,
  AuditLogRequest,
} from "./types";

export const api = {
  health: {
    check: () => get<HealthResponse>("/api/health"),
  },
  tools: {
    lookupCustomer: (customer_id: string) => get<ToolResponse>(`/api/tools/lookup_customer/${encodeURIComponent(customer_id)}`),
    getInvoiceHistory: (customer_id: string, months = 3) => get<ToolResponse>(`/api/tools/get_invoice_history/${encodeURIComponent(customer_id)}?months=${months}`),
    checkDuplicateCharge: (customer_id: string, lookback_days = 30) => get<ToolResponse>(`/api/tools/check_duplicate_charge/${encodeURIComponent(customer_id)}?lookback_days=${lookback_days}`),
    checkOutageStatus: (location: string, customer_id?: string) => get<ToolResponse>(`/api/tools/check_outage_status?location=${encodeURIComponent(location)}${customer_id ? `&customer_id=${encodeURIComponent(customer_id)}` : ""}`),
    runRouterDiagnostic: (customer_id: string) => get<ToolResponse>(`/api/tools/run_router_diagnostic/${encodeURIComponent(customer_id)}`),
    retrievePolicy: (policy_name: string, query?: string, top_k = 3) => get<ToolResponse>(`/api/tools/retrieve_policy/${encodeURIComponent(policy_name)}?top_k=${top_k}${query ? `&query=${encodeURIComponent(query)}` : ""}`),
    applyCredit: (data: ApplyCreditRequest) => post<ToolResponse>("/api/tools/apply_credit", data),
    createTicket: (data: CreateTicketRequest) => post<ToolResponse>("/api/tools/create_ticket", data),
    scheduleTechnician: (data: ScheduleTechnicianRequest) => post<ToolResponse>("/api/tools/schedule_technician", data),
    changePlan: (data: ChangePlanRequest) => post<ToolResponse>("/api/tools/change_plan", data),
    generateHandoffSummary: (data: HandoffSummaryRequest) => post<ToolResponse>("/api/tools/generate_handoff_summary", data),
    generateContextCard: (data: ContextCardRequest) => post<ToolResponse>("/api/tools/generate_context_card", data),
    generateOpeningLine: (data: OpeningLineRequest) => post<ToolResponse>("/api/tools/generate_opening_line", data),
    generateAuditLog: (data: AuditLogRequest) => post<ToolResponse>("/api/tools/generate_audit_log", data),
  },
  rag: {
    memorySearch: (customer_id: string, query: string, top_k = 5, memory_type?: string) => 
      post<{results: MemorySearchResult[], query: string, customer_id: string}>("/api/rag/memory/search", { customer_id, query, top_k, memory_type }),
    policyRetrieve: (query: string, policy_name: string, top_k = 3) =>
      post<{results: PolicyRetrievalResult[], query: string, policy_name: string}>("/api/rag/policy/retrieve", { query, policy_name, top_k }),
    memoryGraph: (customer_id: string) =>
      get<MemoryGraphData>(`/api/rag/memory/graph/${encodeURIComponent(customer_id)}`),
    customers: () =>
      get<{customers: {customer_id: string, name: string, risk_segment: string}[]}>("/api/rag/customers"),
  },
  overview: {
    kpi:    () => get<KpiOverview>("/api/dashboard/overview"),
    charts: () => get<OverviewCharts>("/api/dashboard/charts"),
  },
  cases: {
    list:       (page = 1, limit = 20) => get<CaseListResponse>(`/api/cases?page=${page}&limit=${limit}`),
    detail:     (id: string)           => get<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`),
    auditLog:   (id: string)           => get<AuditLogEntry>(`/api/cases/${encodeURIComponent(id)}/audit_log`),
    contextCard:(id: string)           => get<Record<string, unknown>>(`/api/cases/${encodeURIComponent(id)}/context_card`),
  },
  evaluation: {
    results: () => get<EvaluationReport>("/api/evaluation/results"),
    run:     () => post<{ job_id: string }>("/api/evaluation/run"),
  },
};
