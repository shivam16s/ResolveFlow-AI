"use client";

import { CheckCircle2, Database, FileText, GitBranch, PlugZap, Settings2 } from "lucide-react";
import { GlassPanel, PageHeader, SectionLabel, StatusPill } from "@/components/BlueprintPrimitives";

const steps = [
  { name: "Domain", status: "done", detail: "Telecom customer care with billing, outage, plan, and cancellation intents.", icon: Settings2 },
  { name: "Schema", status: "done", detail: "14-table SQLite operational model with audit, memory, handoff, policy, telemetry, and evaluation stores.", icon: Database },
  { name: "Policies", status: "done", detail: "8 policy documents ingested and chunked for policy-grounded retrieval.", icon: FileText },
  { name: "Tools", status: "done", detail: "Customer lookup, billing, outage, diagnostics, credit, tickets, plan change, handoff.", icon: PlugZap },
  { name: "Memory", status: "done", detail: "Chroma memory collection plus HippoRAG graph nodes, edges, synonymy, and PPR retrieval.", icon: GitBranch },
  { name: "Test", status: "active", detail: "Live demo stream and evaluation runner connected to FastAPI.", icon: CheckCircle2 },
];

const contracts = [
  ["GET /api/chat/message/stream", "SSE chat pipeline: intent, memory, policy, tools, DAG, response"],
  ["POST /api/rag/policy/retrieve", "Policy-grounded retrieval over the resolveflow_policies collection"],
  ["POST /api/rag/memory/search", "Hybrid vector + BM25 + graph memory search"],
  ["POST /api/security/attack", "Policy-DAG red-team attack verification"],
  ["GET /api/evaluation/results", "Saved deterministic + RAGAS + business-adherence evaluation report"],
];

export default function SetupPage() {
  return (
    <div className="p-6 max-w-7xl">
      <PageHeader
        eyebrow="Builder surface"
        title="Project Setup"
        subtitle="The setup page makes the domain contracts visible: schema, policy pack, tool registry, memory configuration, and validation status."
      />

      <div className="grid gap-4 lg:grid-cols-3">
        {steps.map(({ name, status, detail, icon: Icon }) => (
          <GlassPanel key={name} className="p-4">
            <div className="mb-4 flex items-center justify-between">
              <Icon size={18} style={{ color: "#5eead4" }} />
              <StatusPill tone={status === "done" ? "green" : "amber"}>{status}</StatusPill>
            </div>
            <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{name}</p>
            <p className="mt-2 text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{detail}</p>
          </GlassPanel>
        ))}
      </div>

      <div className="mt-6 grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
        <GlassPanel className="p-5">
          <SectionLabel>Policy Graph Preview</SectionLabel>
          <div className="space-y-2">
            {["outage_verified", "duration_6h", "no_prior_credit", "auto_apply_credit", "manual_review_credit", "handoff_human"].map((node, index) => (
              <div key={node} className="flex items-center gap-3 rounded-lg p-2" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <span className="w-6 text-center text-[10px] font-mono" style={{ color: "#5eead4" }}>{index + 1}</span>
                <span className="text-xs font-mono" style={{ color: "var(--text-secondary)" }}>{node}</span>
              </div>
            ))}
          </div>
        </GlassPanel>

        <GlassPanel className="p-5">
          <SectionLabel>Live API Contracts</SectionLabel>
          <div className="space-y-2">
            {contracts.map(([endpoint, detail]) => (
              <div key={endpoint} className="rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <p className="font-mono text-xs" style={{ color: "#5eead4" }}>{endpoint}</p>
                <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>{detail}</p>
              </div>
            ))}
          </div>
        </GlassPanel>
      </div>
    </div>
  );
}
