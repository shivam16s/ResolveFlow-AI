"use client";

import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, Play, RotateCcw, SlidersHorizontal } from "lucide-react";
import { GlassPanel, PageHeader, SectionLabel, StatusPill } from "@/components/BlueprintPrimitives";
import { api } from "@/lib/api";
import type { EvaluationReport } from "@/lib/types";

const presets = [
  ["Impatient user", "Repeated urgency, low patience, expects no re-asking.", "case_11"],
  ["Tangential user", "Digresses into unrelated complaints before returning to the task.", "case_12"],
  ["Unavailable service", "Requests an action that policy or product catalog cannot satisfy.", "case_13"],
  ["Angry repeat request", "Explicit anger plus an already-completed action replay.", "demo"],
];

const harnessSteps = [
  { title: "Compose", body: "Pick persona, issue, and adversarial behavior.", icon: SlidersHorizontal },
  { title: "Run", body: "Stream the scenario against the same FastAPI backend.", icon: Play },
  { title: "Inspect", body: "Open failed assertions and rerun from the failing state.", icon: AlertTriangle },
  { title: "Rerun", body: "Compare current output with latest saved evaluation result.", icon: RotateCcw },
];

export default function AdminHarnessPage() {
  const { data } = useSWR<EvaluationReport>("admin-eval", api.evaluation.results);

  return (
    <div className="p-6 max-w-7xl">
      <PageHeader
        eyebrow="Controlled breakage"
        title="Admin and Test Harness"
        subtitle="A builder surface for adversarial customer presets, scenario reruns, failure inspection, and demo-safe replay behavior."
        action={
          <Link href="/test" className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold" style={{ background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.34)", color: "#5eead4" }}>
            Open isolated test <Play size={14} />
          </Link>
        }
      />

      <div className="grid gap-5 xl:grid-cols-[0.8fr_1.2fr]">
        <GlassPanel className="p-5">
          <SectionLabel>Scenario Builder</SectionLabel>
          <div className="space-y-3">
            {presets.map(([name, detail, id]) => (
              <div key={name} className="rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{name}</p>
                  <StatusPill tone={id === "demo" ? "teal" : "amber"}>{id}</StatusPill>
                </div>
                <p className="mt-2 text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{detail}</p>
              </div>
            ))}
          </div>
        </GlassPanel>

        <GlassPanel className="p-5">
          <SectionLabel>Latest Batch Results</SectionLabel>
          <div className="space-y-2">
            {(data?.scenarios ?? []).slice(-6).map((scenario) => (
              <div key={scenario.case_id} className="grid grid-cols-[1fr_80px_80px] gap-3 rounded-lg p-3 text-sm" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <div>
                  <p className="font-semibold" style={{ color: "var(--text-primary)" }}>{scenario.scenario_name}</p>
                  <p className="mt-1 text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>{scenario.case_id}</p>
                </div>
                <span className="font-mono" style={{ color: scenario.pass_k >= 1 ? "#34d399" : "#fbbf24" }}>{Math.round(scenario.pass_k * 100)}%</span>
                <StatusPill tone={scenario.non_collaborative_degradation > 0 ? "amber" : "green"}>NCD {scenario.non_collaborative_degradation.toFixed(2)}</StatusPill>
              </div>
            ))}
          </div>
        </GlassPanel>
      </div>

      <div className="mt-6 grid gap-4 md:grid-cols-3">
        {harnessSteps.map(({ title, body, icon: Icon }) => (
          <GlassPanel key={title} className="p-4">
            <Icon size={18} style={{ color: "#5eead4" }} />
            <p className="mt-3 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{title}</p>
            <p className="mt-1 text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{body}</p>
          </GlassPanel>
        ))}
      </div>
    </div>
  );
}
