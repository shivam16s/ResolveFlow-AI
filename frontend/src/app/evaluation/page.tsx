"use client";

import { useState } from "react";
import useSWR from "swr";
import { motion } from "framer-motion";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, RadarChart, Radar, PolarGrid, PolarAngleAxis, Cell,
} from "recharts";
import { FlaskConical, Play, CheckCircle2, XCircle, AlertCircle, RefreshCw, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { formatPct, formatDate } from "@/lib/utils";
import type { EvaluationReport, ScenarioResult } from "@/lib/types";

const EVALUATION_POLL_ATTEMPTS = 180;
const EVALUATION_POLL_INTERVAL_MS = 1000;

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForEvaluationRun(expectedRunId: string, immediateSummary?: EvaluationReport) {
  for (let attempt = 0; attempt < EVALUATION_POLL_ATTEMPTS; attempt += 1) {
    const latest = await api.evaluation.results();
    if (latest.run_id === expectedRunId) {
      return latest;
    }
    await delay(EVALUATION_POLL_INTERVAL_MS);
  }

  if (immediateSummary?.run_id === expectedRunId) {
    return immediateSummary;
  }

  throw new Error(`Evaluation run ${expectedRunId} did not become the latest saved result yet`);
}

function StatusIcon({ status }: { status: ScenarioResult["status"] }) {
  if (status === "pass") return <CheckCircle2 size={14} className="text-emerald-400" />;
  if (status === "fail") return <XCircle size={14} className="text-rose-400" />;
  return <AlertCircle size={14} className="text-amber-400" />;
}

function StatePanel({ label }: { label: string }) {
  return <div className="glass p-8 text-center text-sm" style={{ color: "var(--text-muted)" }}>{label}</div>;
}

export default function EvaluationPage() {
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const { data: report, error, isLoading, mutate } = useSWR<EvaluationReport>("eval-results", api.evaluation.results);

  async function runEval() {
    setRunning(true);
    setRunError(null);
    try {
      const started = await api.evaluation.run();
      const expectedRunId = started.run_id ?? started.job_id;
      if (started.summary?.run_id === expectedRunId) {
        await mutate(started.summary, { revalidate: false });
        return;
      }
      const latest = await waitForEvaluationRun(expectedRunId, started.summary);
      await mutate(latest, { revalidate: false });
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "Evaluation run failed");
    } finally {
      setRunning(false);
    }
  }

  if (isLoading && !report) return <div className="p-6 max-w-7xl"><StatePanel label="Loading evaluation results..." /></div>;
  if (error && !report) return <div className="p-6 max-w-7xl"><StatePanel label="Could not load evaluation results from FastAPI." /></div>;
  if (!report) return <div className="p-6 max-w-7xl"><StatePanel label="No evaluation run available." /></div>;

  const contextValues = report.scenarios
    .map((item) => item.ragas_context_precision)
    .filter((value): value is number => typeof value === "number");
  const contextPrecision = typeof report.avg_ragas_context_precision === "number"
    ? report.avg_ragas_context_precision
    : contextValues.length
      ? contextValues.reduce((sum, value) => sum + value, 0) / contextValues.length
      : 0;
  const ba = report.business_adherence ?? null;
  const temperatureRows = report.temperature_results ?? [];
  const hasTemperatureVariation = temperatureRows.some((row) => row.temperature !== null);
  const radarData = [
    { metric: "Pass@5", value: report.avg_pass_k * 100 },
    { metric: "Policy", value: report.avg_policy_compliance * 100 },
    { metric: "Adherence", value: (ba?.business_adherence_score ?? 0) * 100 },
    { metric: "Context", value: contextPrecision * 100 },
    { metric: "Pass Rate", value: report.pass_rate * 100 },
  ];
  const scenarioChartData = report.scenarios.map((scenario, index) => ({
    ...scenario,
    chart_label: scenario.case_id.match(/case_(\d+)/)?.[1]
      ? `Case ${scenario.case_id.match(/case_(\d+)/)?.[1]}`
      : `Case ${index + 1}`,
  }));

  return (
    <div className="p-6 max-w-7xl">
      <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold gradient-text">Agent Evaluation Harness</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
            Latest saved run from FastAPI
          </p>
          <p className="text-xs mt-0.5 font-mono" style={{ color: "var(--text-muted)" }}>
            Run: {report.run_id} - {formatDate(report.run_at)}
          </p>
        </div>
        <button
          onClick={runEval}
          disabled={running}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg transition-all"
          style={running
            ? { background: "var(--surface-3)", color: "var(--text-muted)", border: "1px solid var(--border)", cursor: "not-allowed" }
            : { background: "rgba(20,184,166,0.12)", color: "#5eead4", border: "1px solid rgba(20,184,166,0.28)" }}
        >
          {running ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />}
          {running ? "Running" : "Run Evaluation"}
        </button>
      </motion.div>

      {runError && (
        <div className="glass px-4 py-3 mb-5 text-sm text-rose-300" style={{ borderColor: "rgba(239,68,68,0.35)", background: "rgba(239,68,68,0.08)" }}>
          {runError}
        </div>
      )}

      <div className="glass px-4 py-3 mb-5 text-sm" style={{ color: "var(--text-secondary)", borderColor: "rgba(20,184,166,0.22)", background: "rgba(20,184,166,0.06)" }}>
        {hasTemperatureVariation
          ? `Temperature-varied mode: pass@${temperatureRows[0]?.pass_indices.length || 5} is grouped below by live LLM temperature; latest pass rate is ${formatPct(report.pass_rate * 100)} versus tau-bench-style SOTA below 50%.`
          : `Deterministic fallback mode: Pass@5 equals Pass@1 unless live LLM temperature variation is enabled; latest pass rate is ${formatPct(report.pass_rate * 100)} versus tau-bench-style SOTA below 50%.`}
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {[
          { title: "Pass Rate", value: formatPct(report.pass_rate * 100), color: report.pass_rate >= 0.8 ? "#10b981" : "#f59e0b" },
          { title: "Avg Pass@5", value: formatPct(report.avg_pass_k * 100), color: "#14b8a6" },
          { title: "Policy Compliance", value: formatPct(report.avg_policy_compliance * 100), color: "#10b981" },
          { title: "RAGAS Context Precision", value: formatPct((report.avg_ragas_context_precision ?? contextPrecision) * 100), color: "#6366f1" },
        ].map((item, index) => (
          <motion.div key={item.title} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.04 }} className="glass p-4">
            <p className="text-xs font-medium uppercase tracking-wider mb-2" style={{ color: "var(--text-muted)" }}>{item.title}</p>
            <p className="text-2xl font-bold font-mono" style={{ color: item.color }}>{item.value}</p>
          </motion.div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 mb-6">
        <div className="glass p-5">
          <p className="text-sm font-semibold mb-4" style={{ color: "var(--text-primary)" }}>Metric Radar</p>
          <ResponsiveContainer width="100%" height={220}>
            <RadarChart data={radarData}>
              <PolarGrid stroke="rgba(255,255,255,0.06)" />
              <PolarAngleAxis dataKey="metric" tick={{ fill: "#9090b0", fontSize: 10 }} />
              <Radar name="Agent" dataKey="value" stroke="#14b8a6" fill="#14b8a6" fillOpacity={0.2} strokeWidth={1.5} />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        <div className="glass p-5 lg:col-span-2">
          <p className="text-sm font-semibold mb-4" style={{ color: "var(--text-primary)" }}>Scenario Pass@5 Scores</p>
          <ResponsiveContainer width="100%" height={380}>
            <BarChart data={scenarioChartData} layout="vertical" barSize={16} margin={{ left: 8, right: 16 }}>
              <XAxis type="number" domain={[0, 1]} tickFormatter={(value) => `${(Number(value) * 100).toFixed(0)}%`} tick={{ fill: "#5a5a7a", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="chart_label" interval={0} tick={{ fill: "#9090b0", fontSize: 11 }} tickMargin={8} axisLine={false} tickLine={false} width={72} />
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" horizontal={false} />
              <Tooltip
                formatter={(value) => [`${(Number(value) * 100).toFixed(1)}%`, "Pass@5"]}
                labelFormatter={(_, payload) => payload?.[0]?.payload?.scenario_name ?? "Scenario"}
                contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 8 }}
              />
              <Bar dataKey="pass_k" radius={[0, 4, 4, 0]} name="Pass@5">
                {scenarioChartData.map((scenario) => (
                  <Cell key={scenario.case_id} fill={scenario.status === "pass" ? "#10b981" : scenario.status === "partial" ? "#f59e0b" : "#ef4444"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {ba && (
        <div className="glass p-5 mb-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <ShieldCheck size={14} style={{ color: "#10b981" }} />
              <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Business-Adherence</p>
              <span className="text-[11px] ml-1" style={{ color: "var(--text-muted)" }}>Beyond IVR (arXiv 2601.00596)</span>
            </div>
            <div className="text-right">
              <p className="text-2xl font-bold font-mono" style={{ color: ba.business_adherence_score >= 0.95 ? "#10b981" : ba.business_adherence_score >= 0.85 ? "#14b8a6" : "#f59e0b" }}>
                {formatPct(ba.business_adherence_score * 100)}
              </p>
              <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>{ba.grade}</p>
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {ba.dimensions.map((dim) => (
              <div key={dim.dimension} className="p-3 rounded-lg" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <p className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>{dim.label}</p>
                <p className="text-lg font-bold font-mono mt-1" style={{ color: dim.violations === 0 ? "#10b981" : "#f59e0b" }}>{formatPct(dim.adherence_rate * 100)}</p>
                <p className="text-[11px] mt-0.5" style={{ color: "var(--text-muted)" }}>{dim.violations}/{dim.opportunities} violations</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {temperatureRows.length > 0 && (
        <div className="glass p-5 mb-6">
          <div className="flex items-center gap-2 mb-4">
            <FlaskConical size={14} style={{ color: "#5eead4" }} />
            <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Per-Temperature Results</p>
            <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              {hasTemperatureVariation ? "live variation enabled" : "deterministic fallback"}
            </span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            {temperatureRows.map((row) => (
              <div key={row.label} className="p-3 rounded-lg" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <p className="text-[11px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
                  {row.temperature === null ? "fallback" : `temp ${row.temperature.toFixed(2)}`}
                </p>
                <p className="text-xl font-bold font-mono mt-1" style={{ color: row.pass_rate >= 0.8 ? "#10b981" : row.pass_rate >= 0.5 ? "#f59e0b" : "#ef4444" }}>
                  {formatPct(row.pass_rate * 100)}
                </p>
                <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>
                  {row.runs} runs · score {formatPct(row.avg_score * 100)}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="glass overflow-hidden">
        <div className="px-5 py-4 border-b flex items-center gap-2" style={{ borderColor: "var(--border)" }}>
          <FlaskConical size={14} style={{ color: "#5eead4" }} />
          <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Scenario Results</p>
          <span className="text-xs ml-2" style={{ color: "var(--text-muted)" }}>{report.total_scenarios} scenarios</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b" style={{ borderColor: "var(--border)" }}>
                {["Status", "Scenario", "Pass@5", "Policy", "Context Recall", "Context Prec."].map((heading) => (
                  <th key={heading} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{heading}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.scenarios.map((scenario) => (
                <tr key={scenario.case_id} className="border-b transition-colors hover:bg-white/2" style={{ borderColor: "var(--border)" }}>
                  <td className="px-4 py-4"><StatusIcon status={scenario.status} /></td>
                  <td className="px-4 py-4">
                    <p className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{scenario.scenario_name}</p>
                    <p className="text-[11px] font-mono mt-0.5" style={{ color: "var(--text-muted)" }}>{scenario.case_id}</p>
                  </td>
                  <td className="px-4 py-4 font-mono font-bold" style={{ color: scenario.pass_k >= 0.8 ? "#10b981" : scenario.pass_k >= 0.5 ? "#f59e0b" : "#ef4444" }}>{formatPct(scenario.pass_k * 100)}</td>
                  <td className="px-4 py-4 font-mono" style={{ color: "#10b981" }}>{formatPct(scenario.policy_compliance * 100)}</td>
                  <td className="px-4 py-4 font-mono" style={{ color: scenario.ragas_context_recall == null ? "var(--text-muted)" : "#5eead4" }}>{scenario.ragas_context_recall == null ? "—" : formatPct(scenario.ragas_context_recall * 100)}</td>
                  <td className="px-4 py-4 font-mono" style={{ color: scenario.ragas_context_precision == null ? "var(--text-muted)" : "#f59e0b" }}>{scenario.ragas_context_precision == null ? "—" : formatPct(scenario.ragas_context_precision * 100)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
