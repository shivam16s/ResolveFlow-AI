"use client";

import Link from "next/link";
import useSWR from "swr";
import { ArrowRight, Brain, ClipboardCheck, Database, GitBranch, ShieldCheck, Video } from "lucide-react";
import { GlassPanel, MiniMetric, PageHeader, SectionLabel, StatusPill } from "@/components/BlueprintPrimitives";
import { api } from "@/lib/api";
import { formatPct } from "@/lib/utils";
import type { EvaluationReport, KpiOverview } from "@/lib/types";

const capabilities = [
  ["Multi-issue intake", "Detects billing, outage, cancellation, refund, router, and plan-change issues in one customer message."],
  ["Memory lanes", "Separates stable profile facts, episodic history, and current-session facts instead of one opaque memory blob."],
  ["Policy-aware actioning", "Runs DAG validation before credits, tickets, handoff, technician scheduling, or plan changes."],
  ["Audit + evaluation", "Shows evidence, tool calls, policy path, UJCS, RAGAS, and scenario outcomes for judging."],
];

const pipeline = [
  { label: "Intake", icon: Brain },
  { label: "Memory", icon: Database },
  { label: "Retrieval", icon: ShieldCheck },
  { label: "Policy DAG", icon: GitBranch },
  { label: "Tools", icon: ClipboardCheck },
  { label: "Evaluation", icon: Video },
];

export default function ProjectOverviewPage() {
  const { data: kpi } = useSWR<KpiOverview>("project-kpi", api.overview.kpi);
  const { data: evaluation } = useSWR<EvaluationReport>("project-eval", api.evaluation.results);

  return (
    <div className="p-6 max-w-7xl">
      <PageHeader
        eyebrow="FlowZint Customer Care Bot"
        title="ResolveFlow AI"
        subtitle="A glass-box customer-care operations console for multi-issue telecom support, long-term memory, policy-grounded retrieval, tool execution, audit handoff, and measured evaluation."
        action={
          <Link href="/demo" className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold" style={{ background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.34)", color: "#5eead4" }}>
            Live demo <ArrowRight size={15} />
          </Link>
        }
      />

      <div className="grid gap-4 md:grid-cols-4">
        <MiniMetric label="Pass rate" value={evaluation ? formatPct(evaluation.pass_rate * 100) : "--"} sub="Strict scenario runner" />
        <MiniMetric label="Policy compliance" value={evaluation ? formatPct(evaluation.avg_policy_compliance * 100) : "--"} sub="Audit/DAG evidence" />
        <MiniMetric label="RAGAS faithfulness" value={evaluation ? formatPct(evaluation.avg_ragas_faithfulness * 100) : "--"} sub="Policy retrieval layer" />
        <MiniMetric label="Cases today" value={kpi?.total_cases_today ?? "--"} sub="Operational DB" />
      </div>

      <div className="mt-6 grid gap-5 xl:grid-cols-[1.1fr_0.9fr]">
        <GlassPanel className="p-5">
          <SectionLabel>Architecture Snapshot</SectionLabel>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {pipeline.map(({ label, icon: Icon }, index) => (
              <div key={label} className="relative rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <Icon size={18} style={{ color: "#5eead4" }} />
                <p className="mt-3 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{label}</p>
                <p className="mt-1 text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>0{index + 1}</p>
              </div>
            ))}
          </div>
        </GlassPanel>

        <GlassPanel className="p-5">
          <SectionLabel>Judging Evidence</SectionLabel>
          <div className="space-y-3">
            {["Model novelty: memory + HippoRAG + CRAG + DAG gates", "Real-world applicability: telecom billing/outage workflows", "Architecture clarity: inspectable state, tools, policy, evidence", "Documentation clarity: readiness checklist and replayable demo"].map((item, index) => (
              <div key={item} className="flex items-start gap-3">
                <StatusPill tone={index === 0 ? "indigo" : "teal"}>0{index + 1}</StatusPill>
                <p className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>{item}</p>
              </div>
            ))}
          </div>
        </GlassPanel>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {capabilities.map(([title, body]) => (
          <GlassPanel key={title} className="p-4">
            <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{title}</p>
            <p className="mt-2 text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{body}</p>
          </GlassPanel>
        ))}
      </div>
    </div>
  );
}
