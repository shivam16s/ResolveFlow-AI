"use client";

import Link from "next/link";
import useSWR from "swr";
import { FileDown, Handshake, ListChecks, ShieldCheck } from "lucide-react";
import { GlassPanel, PageHeader, SectionLabel, StatusPill } from "@/components/BlueprintPrimitives";
import { api } from "@/lib/api";
import type { AuditLogEntry, CaseListResponse } from "@/lib/types";

const auditLinks = [
  { label: "Timeline", icon: ListChecks },
  { label: "Compliance", icon: ShieldCheck },
  { label: "Export", icon: FileDown },
];

export default function AuditHandoffPage() {
  const { data: cases } = useSWR<CaseListResponse>("audit-cases", () => api.cases.list(1, 10));
  const selected = cases?.cases?.[0];
  const selectedId = selected?.route_id ?? selected?.case_id ?? "";
  const { data: audit } = useSWR<AuditLogEntry>(selectedId ? ["audit-root", selectedId] : null, () => api.cases.auditLog(selectedId));

  return (
    <div className="p-6 max-w-7xl">
      <PageHeader
        eyebrow="Trust layer"
        title="Audit and Handoff"
        subtitle="A completed or partial interaction becomes an inspectable artifact: evidence, retrieved policy, tool calls, DAG path, action summary, and specialist handoff context."
      />

      <div className="grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
        <GlassPanel className="p-5">
          <SectionLabel>Audit Timeline</SectionLabel>
          <div className="space-y-3">
            {[
              ["Conversation turn", selected ? `${selected.customer_name} · ${selected.issues.join(", ")}` : "No case loaded"],
              ["Tools called", audit?.tools_called?.join(", ") || "Waiting for audit log"],
              ["Evidence used", audit?.evidence_used?.join(", ") || "No evidence loaded"],
              ["Policy DAG path", audit?.policy_dag_path?.join(" -> ") || "No DAG path loaded"],
              ["Action taken", audit?.action_taken || "No action recorded"],
            ].map(([title, body], index) => (
              <div key={title} className="grid grid-cols-[28px_1fr] gap-3 rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <span className="mt-0.5 flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-mono" style={{ background: "rgba(20,184,166,0.12)", color: "#5eead4", border: "1px solid rgba(20,184,166,0.26)" }}>{index + 1}</span>
                <div>
                  <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{title}</p>
                  <p className="mt-1 text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{body}</p>
                </div>
              </div>
            ))}
          </div>
        </GlassPanel>

        <GlassPanel className="p-5">
          <div className="mb-4 flex items-center justify-between">
            <SectionLabel>Handoff Card</SectionLabel>
            <StatusPill tone={audit?.policy_status === "compliant" ? "green" : "amber"}>{audit?.policy_status ?? "pending"}</StatusPill>
          </div>
          <div className="rounded-lg p-4" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
            <Handshake size={20} style={{ color: "#5eead4" }} />
            <p className="mt-3 text-lg font-semibold" style={{ color: "var(--text-primary)" }}>{selected?.customer_name ?? "No case selected"}</p>
            <p className="mt-1 text-xs font-mono" style={{ color: "var(--text-muted)" }}>{selected?.case_id ?? "case pending"}</p>
            <div className="mt-4 space-y-2 text-sm" style={{ color: "var(--text-secondary)" }}>
              <p>Resolved issues: billing evidence, policy path, tool outputs.</p>
              <p>Remaining issues: customer-side verification or human review when DAG blocks automation.</p>
              <p>Suggested opening: I have the duplicate charge and service context already attached.</p>
            </div>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            {auditLinks.map(({ label, icon: Icon }) => (
              <Link key={label} href={selectedId ? `/cases/${encodeURIComponent(selectedId)}` : "/cases"} className="rounded-lg p-3 text-sm font-semibold" style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                <Icon size={16} style={{ color: "#5eead4" }} />
                <span className="mt-2 block">{label}</span>
              </Link>
            ))}
          </div>
        </GlassPanel>
      </div>
    </div>
  );
}
