"use client";

import Link from "next/link";
import useSWR from "swr";
import { ArrowRight, CheckCircle2, Clock, UsersRound } from "lucide-react";
import { GlassPanel, MiniMetric, PageHeader, ProgressRow, SectionLabel, StatusPill } from "@/components/BlueprintPrimitives";
import { api } from "@/lib/api";
import { formatPct } from "@/lib/utils";
import type { CaseListResponse, EvaluationReport, KpiOverview } from "@/lib/types";

const roster = [
  { name: "Owner", role: "Project lead", icon: UsersRound },
  { name: "Backend", role: "FastAPI + SQLite + Chroma + Gemini", icon: CheckCircle2 },
  { name: "Frontend", role: "Next.js operations console", icon: Clock },
];

export default function WorkspacePage() {
  const { data: kpi } = useSWR<KpiOverview>("workspace-kpi", api.overview.kpi);
  const { data: cases } = useSWR<CaseListResponse>("workspace-cases", () => api.cases.list(1, 5));
  const { data: evaluation } = useSWR<EvaluationReport>("workspace-evaluation", api.evaluation.results);

  return (
    <div className="p-6 max-w-7xl">
      <PageHeader
        eyebrow="Authenticated workspace"
        title="Team Dashboard"
        subtitle="Small-team mission control for the ResolveFlow project: latest operational run, evaluation posture, pending review surfaces, and submission health."
      />

      <div className="grid gap-4 md:grid-cols-4">
        <MiniMetric label="Active cases" value={cases?.total ?? "--"} sub="FastAPI case browser" />
        <MiniMetric label="Resolved by AI" value={kpi ? formatPct(kpi.resolved_by_ai_pct) : "--"} sub="No handoff" />
        <MiniMetric label="Policy compliant" value={kpi ? formatPct(kpi.policy_compliant_pct) : "--"} sub="Audit logs" />
        <MiniMetric label="Eval pass rate" value={evaluation ? formatPct(evaluation.pass_rate * 100) : "--"} sub={evaluation?.run_id ?? "latest run"} />
      </div>

      <div className="mt-6 grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
        <GlassPanel className="p-5">
          <SectionLabel>Project Status</SectionLabel>
          <div className="space-y-4">
            <ProgressRow label="Policy coverage" value={Math.round((evaluation?.avg_policy_compliance ?? 0.867) * 100)} />
            <ProgressRow label="RAGAS context precision" value={Math.round((evaluation?.avg_ragas_context_precision ?? 0.949) * 100)} tone="indigo" />
            <ProgressRow label="Dashboard readiness" value={86} tone="amber" />
            <ProgressRow label="Submission packet" value={72} tone="amber" />
          </div>
        </GlassPanel>

        <GlassPanel className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <SectionLabel>Recent Transcripts</SectionLabel>
            <Link href="/cases" className="inline-flex items-center gap-1 text-xs font-semibold" style={{ color: "#5eead4" }}>
              Open cases <ArrowRight size={12} />
            </Link>
          </div>
          <div className="space-y-2">
            {(cases?.cases ?? []).map((item) => (
              <Link key={item.case_id} href={`/cases/${encodeURIComponent(item.route_id ?? item.case_id)}`} className="block rounded-lg p-3 transition-colors hover:bg-white/4" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{item.customer_name}</p>
                    <p className="mt-1 text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>{item.case_id}</p>
                  </div>
                  <StatusPill tone={item.status === "resolved" ? "green" : item.status === "escalated" ? "rose" : "amber"}>{item.status.replace("_", " ")}</StatusPill>
                </div>
              </Link>
            ))}
          </div>
        </GlassPanel>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-3">
        {roster.map(({ name, role, icon: Icon }) => (
          <GlassPanel key={name} className="p-4">
            <Icon size={18} style={{ color: "#5eead4" }} />
            <p className="mt-3 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{name}</p>
            <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>{role}</p>
          </GlassPanel>
        ))}
      </div>
    </div>
  );
}
