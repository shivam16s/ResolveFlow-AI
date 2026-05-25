"use client";

import Link from "next/link";
import useSWR from "swr";
import { CheckCircle2, Code2, ExternalLink, Video } from "lucide-react";
import { GlassPanel, PageHeader, ProgressRow, SectionLabel, StatusPill } from "@/components/BlueprintPrimitives";
import { api } from "@/lib/api";
import { formatPct } from "@/lib/utils";
import type { EvaluationReport, KpiOverview } from "@/lib/types";

const checklist = [
  ["Project title", "ResolveFlow AI", true],
  ["Track", "Customer Care Bot", true],
  ["Description", "50+ word public project description", true],
  ["Public repo", "GitHub repository link required", false],
  ["Demo video", "Public video link required", false],
  ["Live URL", "Optional, this dashboard can be hosted", false],
  ["Architecture evidence", "frontend.txt page map + visible console", true],
  ["Evaluation evidence", "13 scenario report + RAGAS + NCD", true],
];

const submissionLinks = [
  { label: "Repo", icon: Code2, body: "Add public GitHub URL" },
  { label: "Demo video", icon: Video, body: "Add public video URL" },
  { label: "Live app", icon: ExternalLink, body: "Optional hosted URL" },
];

export default function SubmissionPage() {
  const { data: kpi } = useSWR<KpiOverview>("submission-kpi", api.overview.kpi);
  const { data: evaluation } = useSWR<EvaluationReport>("submission-eval", api.evaluation.results);
  const completed = checklist.filter((item) => item[2]).length;

  return (
    <div className="p-6 max-w-7xl">
      <PageHeader
        eyebrow="FlowZint packet"
        title="Submission Readiness"
        subtitle="A pre-flight page for the official portal fields: title, track, description, public repo, public demo video, optional live URL, architecture proof, and evaluation proof."
      />

      <div className="grid gap-4 md:grid-cols-4">
        <GlassPanel className="p-4 md:col-span-2">
          <SectionLabel>Readiness</SectionLabel>
          <ProgressRow label={`${completed}/${checklist.length} checks complete`} value={Math.round((completed / checklist.length) * 100)} tone="amber" />
        </GlassPanel>
        <GlassPanel className="p-4">
          <p className="text-[11px] font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>Pass rate</p>
          <p className="mt-3 text-2xl font-bold font-mono" style={{ color: "#5eead4" }}>{evaluation ? formatPct(evaluation.pass_rate * 100) : "--"}</p>
        </GlassPanel>
        <GlassPanel className="p-4">
          <p className="text-[11px] font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>Policy KPI</p>
          <p className="mt-3 text-2xl font-bold font-mono" style={{ color: "#5eead4" }}>{kpi ? formatPct(kpi.policy_compliant_pct) : "--"}</p>
        </GlassPanel>
      </div>

      <div className="mt-6 grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
        <GlassPanel className="p-5">
          <SectionLabel>Checklist</SectionLabel>
          <div className="space-y-2">
            {checklist.map(([label, detail, done]) => (
              <div key={String(label)} className="flex items-center gap-3 rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <CheckCircle2 size={16} style={{ color: done ? "#34d399" : "var(--text-muted)" }} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{label}</p>
                  <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>{detail}</p>
                </div>
                <StatusPill tone={done ? "green" : "amber"}>{done ? "ready" : "needs link"}</StatusPill>
              </div>
            ))}
          </div>
        </GlassPanel>

        <GlassPanel className="p-5">
          <SectionLabel>Portal Preview</SectionLabel>
          <div className="space-y-4">
            <div className="rounded-lg p-4" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
              <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>Project Description</p>
              <p className="mt-3 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                ResolveFlow AI is a customer-care operations console for telecom support that resolves multi-issue conversations with memory, policy-grounded retrieval, verified tool calls, audit trails, safe handoff, and measured evaluation.
              </p>
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              {submissionLinks.map(({ label, icon: Icon, body }) => (
                <Link key={label} href="/project" className="rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                  <Icon size={16} style={{ color: "#5eead4" }} />
                  <span className="mt-2 block text-sm font-semibold">{label}</span>
                  <span className="mt-1 block text-xs" style={{ color: "var(--text-secondary)" }}>{body}</span>
                </Link>
              ))}
            </div>
          </div>
        </GlassPanel>
      </div>
    </div>
  );
}
