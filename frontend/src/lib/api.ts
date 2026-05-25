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
} from "./types";

export const api = {
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
