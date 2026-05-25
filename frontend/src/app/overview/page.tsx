"use client";

import useSWR from "swr";
import {
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { motion } from "framer-motion";
import {
  Activity, CheckCircle2, AlertTriangle, ShieldCheck,
  CreditCard, Ticket, UsersRound, HeartPulse,
} from "lucide-react";
import { KpiCard } from "@/components/KpiCard";
import { api } from "@/lib/api";
import { formatPct, formatInr } from "@/lib/utils";
import type { KpiOverview, OverviewCharts } from "@/lib/types";

function PanelState({ label }: { label: string }) {
  return (
    <div className="glass p-5 min-h-[180px] flex items-center justify-center text-sm" style={{ color: "var(--text-muted)" }}>
      {label}
    </div>
  );
}

function ChartTip({ active, payload, label }: { active?: boolean; payload?: { name: string; value: number; color: string }[]; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="glass px-3 py-2 text-xs" style={{ border: "1px solid var(--border-strong)" }}>
      <p className="font-medium mb-1" style={{ color: "var(--text-secondary)" }}>{label}</p>
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }}>
          {p.name}: <span className="font-bold">{p.value}</span>
        </p>
      ))}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-xs font-semibold uppercase tracking-widest mb-4" style={{ color: "var(--text-muted)" }}>
      {children}
    </h2>
  );
}

export default function OverviewPage() {
  const { data: kpi, error: kpiError, isLoading: kpiLoading } = useSWR<KpiOverview>("dashboard-kpi", api.overview.kpi);
  const { data: charts, error: chartError, isLoading: chartLoading } = useSWR<OverviewCharts>("dashboard-charts", api.overview.charts);

  const kpis = kpi ? [
    { title: "Total Cases Today", value: kpi.total_cases_today, sub: "SQLite conversations", icon: <Activity size={13} style={{ color: "#14b8a6" }} /> },
    { title: "Resolved by AI", value: formatPct(kpi.resolved_by_ai_pct), sub: "No handoff", icon: <CheckCircle2 size={13} className="text-emerald-400" /> },
    { title: "Escalated to Human", value: formatPct(kpi.escalated_pct), sub: "Handoff required", icon: <AlertTriangle size={13} className="text-amber-400" /> },
    { title: "Policy Compliant", value: formatPct(kpi.policy_compliant_pct), sub: "From audit logs", icon: <ShieldCheck size={13} className="text-indigo-400" /> },
    { title: "Credits Applied", value: kpi.credits_applied_count, sub: formatInr(kpi.credits_applied_total_inr), icon: <CreditCard size={13} className="text-purple-400" /> },
    { title: "Tickets Created", value: kpi.tickets_created, sub: "Support tickets", icon: <Ticket size={13} style={{ color: "#14b8a6" }} /> },
    { title: "High-Risk Customers", value: kpi.high_risk_customers, sub: "Risk or churn score", icon: <UsersRound size={13} className="text-rose-400" /> },
    { title: "Avg Health Score", value: kpi.avg_health_score, sub: "Observed sessions", icon: <HeartPulse size={13} style={{ color: kpi.avg_health_score >= 70 ? "#10b981" : kpi.avg_health_score >= 50 ? "#f59e0b" : "#ef4444" }} /> },
  ] : [];

  return (
    <div className="p-6 max-w-7xl">
      <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="mb-8">
        <h1 className="text-xl font-bold gradient-text">Operations Overview</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          Live metrics from the ResolveFlow SQLite store
        </p>
      </motion.div>

      <section className="mb-8">
        <SectionTitle>Key Performance Indicators</SectionTitle>
        {kpiError && !kpi && <PanelState label="Could not load dashboard KPIs from FastAPI." />}
        {kpiLoading && !kpi && <PanelState label="Loading KPIs..." />}
        {kpi && (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {kpis.map((item, index) => (
              <KpiCard key={item.title} {...item} delay={index * 0.04} />
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionTitle>Analytics</SectionTitle>
        {chartError && !charts && <PanelState label="Could not load dashboard charts from FastAPI." />}
        {chartLoading && !charts && <PanelState label="Loading analytics..." />}
        {charts && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="glass p-5">
              <p className="text-sm font-semibold mb-4" style={{ color: "var(--text-primary)" }}>Resolution Trend</p>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={charts.resolution_trend}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                  <XAxis dataKey="date" tick={{ fill: "#5a5a7a", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "#5a5a7a", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip content={<ChartTip />} />
                  <Line type="monotone" dataKey="resolved" stroke="#10b981" strokeWidth={2} dot={{ fill: "#10b981", r: 3 }} name="Resolved" />
                  <Line type="monotone" dataKey="escalated" stroke="#f97316" strokeWidth={2} dot={{ fill: "#f97316", r: 3 }} name="Escalated" strokeDasharray="4 2" />
                </LineChart>
              </ResponsiveContainer>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="glass p-5">
              <p className="text-sm font-semibold mb-4" style={{ color: "var(--text-primary)" }}>Issue Type Distribution</p>
              {charts.issue_distribution.length === 0 ? (
                <div className="h-[200px] flex items-center justify-center text-sm" style={{ color: "var(--text-muted)" }}>No issue data yet.</div>
              ) : (
                <div className="flex items-center gap-4">
                  <ResponsiveContainer width={170} height={170}>
                    <PieChart>
                      <Pie data={charts.issue_distribution} cx="50%" cy="50%" innerRadius={45} outerRadius={70} dataKey="value" paddingAngle={3}>
                        {charts.issue_distribution.map((item) => <Cell key={item.name} fill={item.color} />)}
                      </Pie>
                      <Tooltip contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 8 }} />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="flex flex-col gap-1.5 flex-1">
                    {charts.issue_distribution.map((item) => (
                      <div key={item.name} className="flex items-center gap-2 text-xs">
                        <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: item.color }} />
                        <span style={{ color: "var(--text-secondary)" }}>{item.name}</span>
                        <span className="ml-auto font-mono font-bold" style={{ color: "var(--text-primary)" }}>{item.value}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="glass p-5">
              <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Tool Call Frequency</p>
                <div className="flex items-center gap-3 text-[11px]" style={{ color: "var(--text-muted)" }}>
                  <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm" style={{ background: "#14b8a6" }} /> Primary/high-frequency</span>
                  <span className="inline-flex items-center gap-1.5"><span className="w-2 h-2 rounded-sm" style={{ background: "#6366f1" }} /> Supporting tools</span>
                </div>
              </div>
              {charts.tool_frequency.length === 0 ? (
                <div className="h-[200px] flex items-center justify-center text-sm" style={{ color: "var(--text-muted)" }}>No tool calls recorded yet.</div>
              ) : (
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={charts.tool_frequency} layout="vertical" barSize={14}>
                    <XAxis type="number" tick={{ fill: "#5a5a7a", fontSize: 11 }} axisLine={false} tickLine={false} />
                    <YAxis type="category" dataKey="tool" tick={{ fill: "#9090b0", fontSize: 11 }} axisLine={false} tickLine={false} width={116} />
                    <Tooltip contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 8 }} />
                    <Bar dataKey="calls" radius={[0, 4, 4, 0]} name="Calls">
                      {charts.tool_frequency.map((_, index) => <Cell key={index} fill={index < 3 ? "#14b8a6" : "#6366f1"} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="glass p-5">
              <p className="text-sm font-semibold mb-4" style={{ color: "var(--text-primary)" }}>Health Score Distribution</p>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={charts.health_distribution} barSize={40}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
                  <XAxis dataKey="range" tick={{ fill: "#9090b0", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "#5a5a7a", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 8 }} />
                  <Bar dataKey="count" radius={[4, 4, 0, 0]} name="Sessions">
                    {charts.health_distribution.map((item) => <Cell key={item.range} fill={item.color} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </motion.div>
          </div>
        )}
      </section>
    </div>
  );
}
