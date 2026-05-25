"use client";

import Link from "next/link";
import useSWR from "swr";
import { ArrowRight, CheckCircle2, ShieldAlert, Wrench } from "lucide-react";
import { GlassPanel, PageHeader, SectionLabel, StatusPill } from "@/components/BlueprintPrimitives";
import { api } from "@/lib/api";
import type { CaseListResponse } from "@/lib/types";

const actionContracts = [
  ["apply_credit", "Policy validation gate required", "allowed with proof"],
  ["create_ticket", "Issue type and priority must match DAG result", "allowed"],
  ["schedule_technician", "Outage/diagnostic prerequisite required", "guarded"],
  ["change_plan", "Retention and downgrade policy required", "guarded"],
  ["generate_handoff_summary", "Trigger condition or failed action required", "safe"],
];

export default function ActionConsolePage() {
  const { data } = useSWR<CaseListResponse>("action-cases", () => api.cases.list(1, 6));
  const activeCases = (data?.cases ?? []).filter((item) => item.status !== "resolved");

  return (
    <div className="p-6 max-w-7xl">
      <PageHeader
        eyebrow="Operator surface"
        title="Action Console"
        subtitle="Risky customer-care operations are surfaced as proposed actions with policy state, target entity, dry-run status, and links back to the originating case."
      />

      <div className="grid gap-5 xl:grid-cols-[1fr_0.9fr]">
        <GlassPanel className="p-5">
          <SectionLabel>Proposed Action Queue</SectionLabel>
          <div className="space-y-3">
            {(activeCases.length ? activeCases : data?.cases ?? []).slice(0, 5).map((item) => (
              <Link key={item.case_id} href={`/cases/${encodeURIComponent(item.route_id ?? item.case_id)}`} className="block rounded-lg p-3 transition-colors hover:bg-white/4" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{item.customer_name}</p>
                    <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>{item.issues.join(" · ")}</p>
                  </div>
                  <ArrowRight size={14} style={{ color: "#5eead4" }} />
                </div>
              </Link>
            ))}
          </div>
        </GlassPanel>

        <GlassPanel className="p-5">
          <SectionLabel>Tool Contracts</SectionLabel>
          <div className="space-y-2">
            {actionContracts.map(([tool, rule, state], index) => (
              <div key={tool} className="rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    {index < 2 ? <CheckCircle2 size={15} style={{ color: "#34d399" }} /> : <ShieldAlert size={15} style={{ color: "#fbbf24" }} />}
                    <p className="font-mono text-xs" style={{ color: "var(--text-primary)" }}>{tool}</p>
                  </div>
                  <StatusPill tone={state === "safe" ? "green" : state === "guarded" ? "amber" : "teal"}>{state}</StatusPill>
                </div>
                <p className="mt-2 text-xs" style={{ color: "var(--text-secondary)" }}>{rule}</p>
              </div>
            ))}
          </div>
        </GlassPanel>
      </div>

      <GlassPanel className="mt-6 p-5">
        <SectionLabel>Simulate First, Commit Second</SectionLabel>
        <div className="grid gap-3 md:grid-cols-4">
          {["Validate policy DAG", "Confirm prerequisite nodes", "Run tool dry-run", "Write audit log"].map((label, index) => (
            <div key={label} className="rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
              <Wrench size={16} style={{ color: "#5eead4" }} />
              <p className="mt-3 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{label}</p>
              <p className="mt-1 text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>step 0{index + 1}</p>
            </div>
          ))}
        </div>
      </GlassPanel>
    </div>
  );
}
