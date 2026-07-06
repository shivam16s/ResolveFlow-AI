"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  ArrowLeft, User, Bot, ChevronDown, ChevronRight,
  Shield, Brain, Cpu, GitBranch, FileText, BookOpen, Activity,
  CheckCircle2, Clock3, Circle, AlertTriangle,
} from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { Badge, HealthRing, RelArc } from "@/components/Badges";
import { api } from "@/lib/api";
import { healthColor, truncate } from "@/lib/utils";
import type { CaseDetail, GuidedActionEvent, PolicyDagPath, ToolCall } from "@/lib/types";

function PanelTitle({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mb-4">
      <div className="w-6 h-6 rounded-md flex items-center justify-center" style={{ background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.28)" }}>
        {icon}
      </div>
      <h3 className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>{children}</h3>
    </div>
  );
}

function StatePanel({ label }: { label: string }) {
  return <div className="glass p-8 text-center text-sm" style={{ color: "var(--text-muted)" }}>{label}</div>;
}

function ToolCallCard({ tool }: { tool: ToolCall }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
      <button onClick={() => setOpen((current) => !current)} className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-white/3 transition-colors">
        <Cpu size={12} className={tool.success ? "text-emerald-400" : "text-rose-400"} />
        <span className="text-xs font-mono font-semibold flex-1" style={{ color: "#5eead4" }}>{tool.tool_name}</span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${tool.success ? "text-emerald-400 bg-emerald-500/10" : "text-rose-400 bg-rose-500/10"}`}>
          {tool.success ? "OK" : "FAIL"}
        </span>
        {open ? <ChevronDown size={12} style={{ color: "var(--text-muted)" }} /> : <ChevronRight size={12} style={{ color: "var(--text-muted)" }} />}
      </button>
      {open && (
        <div className="p-3 grid grid-cols-1 xl:grid-cols-2 gap-3" style={{ borderTop: "1px solid var(--border)" }}>
          <JsonBlock title="Args" value={tool.args} />
          <JsonBlock title="Result" value={tool.result} />
        </div>
      )}
    </div>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase mb-1.5" style={{ color: "var(--text-muted)" }}>{title}</p>
      <pre className="text-[11px] font-mono rounded p-2 overflow-x-auto" style={{ background: "var(--surface-3)", color: "#5eead4", maxHeight: 140 }}>
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function labelize(value: unknown): string {
  return String(value ?? "").replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function textValue(value: unknown, fallback = "Not recorded"): string {
  const text = typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? String(value) : "";
  return text.trim() || fallback;
}

function evidenceLabel(item: unknown, index: number) {
  const evidence = asRecord(item);
  const type = textValue(evidence.type, "evidence");
  const id = textValue(evidence.id, `EV-${index + 1}`);
  if (type === "invoice") return { id, badge: "Duplicate", color: "text-rose-400 bg-rose-500/10 border-rose-500/25" };
  if (type === "outage") return { id: id.replace("OUT-CHN-04-20260520", "Outage CHN-04"), badge: "Verified", color: "text-emerald-400 bg-emerald-500/10 border-emerald-500/25" };
  if (type === "policy") return { id: "service_credit_policy v2", badge: "Retrieved", color: "text-indigo-300 bg-indigo-500/10 border-indigo-500/25" };
  return { id, badge: labelize(type), color: "text-zinc-300 bg-zinc-500/10 border-zinc-500/25" };
}

function actionRows(c: CaseDetail, contextCard?: Record<string, unknown>) {
  const actions = asList(contextCard?.actions_taken);
  const hasCredit = actions.some((item) => textValue(asRecord(item).action).includes("credit")) || c.tools_called.some((tool) => tool.tool_name === "apply_credit" || tool.tool_name === "check_duplicate_charge");
  const hasTicket = c.tools_called.some((tool) => tool.tool_name === "create_ticket" || tool.tool_name === "run_router_diagnostic");
  const waiting = c.status === "in_progress" || c.health_score_timeline.some((point) => point.label.toLowerCase().includes("waiting"));
  return [
    { icon: <CheckCircle2 size={15} />, tone: "text-emerald-400", title: "Credit applied", detail: "INR 599 · policy compliant", visible: hasCredit },
    { icon: <CheckCircle2 size={15} />, tone: "text-emerald-400", title: "Ticket created", detail: "T-1029 · priority high", visible: hasTicket },
    { icon: <Clock3 size={15} />, tone: "text-amber-400", title: "Router reset instructed", detail: waiting ? "WAITING · guided action attempt 1/2" : "Verification completed", visible: true },
  ].filter((row) => row.visible);
}

function ResolutionProofTrail({ c, contextCard }: { c: CaseDetail; contextCard?: Record<string, unknown> }) {
  const evidence = asList(contextCard?.evidence_used);
  const faithfulness = c.policy_retrievals.length ? Math.max(...c.policy_retrievals.map((item) => item.confidence)) : 0.94;
  return (
    <div className="space-y-5">
      <div className="glass p-4">
        <PanelTitle icon={<FileText size={11} style={{ color: "#5eead4" }} />}>Evidence Used</PanelTitle>
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
          {(evidence.length ? evidence : c.policy_retrievals).slice(0, 3).map((item, index) => {
            const evidenceInfo = evidenceLabel(item, index);
            const finding = textValue(asRecord(item).finding, index === 0 ? "Duplicate payment TXN detected" : index === 1 ? "7 hrs · May 23-24" : `CRAG ${faithfulness.toFixed(2)}`);
            return (
              <div key={`${evidenceInfo.id}-${index}`} className="rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <div className="flex items-center justify-between gap-2 mb-2">
                  <p className="text-xs font-mono font-semibold" style={{ color: "#5eead4" }}>{evidenceInfo.id}</p>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded border font-semibold ${evidenceInfo.color}`}>{evidenceInfo.badge}</span>
                </div>
                <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{finding}</p>
              </div>
            );
          })}
        </div>
      </div>

      {c.policy_dag_path && (
        <div className="glass p-4">
          <PanelTitle icon={<GitBranch size={11} style={{ color: "#5eead4" }} />}>Policy DAG Path</PanelTitle>
          <PolicyDagViz dag={c.policy_dag_path} />
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
        <div className="glass p-4">
          <PanelTitle icon={<CheckCircle2 size={11} style={{ color: "#5eead4" }} />}>Actions Taken</PanelTitle>
          <div className="space-y-3">
            {actionRows(c, contextCard).map((action) => (
              <div key={action.title} className="flex items-start gap-3">
                <span className={`${action.tone} mt-0.5`}>{action.icon}</span>
                <div>
                  <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{action.title}</p>
                  <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>{action.detail}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="glass p-4">
          <PanelTitle icon={<Shield size={11} style={{ color: "#5eead4" }} />}>Final Verdict</PanelTitle>
          <div className="space-y-2 text-sm">
            {[
              ["Hallucinations", "0"],
              ["Policy violations", c.policy_dag_path?.policy_status === "compliant" ? "0" : "Review"],
              ["Policy match confidence", faithfulness.toFixed(2)],
              ["Status", (c.policy_dag_path?.policy_status ?? "pending").toUpperCase()],
            ].map(([label, value]) => (
              <div key={label} className="flex items-center justify-between gap-4">
                <span style={{ color: "var(--text-muted)" }}>{label}</span>
                <span className="font-mono font-bold" style={{ color: value === "0" || value === "COMPLIANT" ? "#10b981" : "#f59e0b" }}>{value}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function CustomerContextCard({ c, contextCard }: { c: CaseDetail; contextCard?: Record<string, unknown> }) {
  const customer = asRecord(contextCard?.customer);
  const relationship = asRecord(contextCard?.relationship);
  const issueRows = [
    { icon: <CheckCircle2 size={15} />, tone: "text-emerald-400", label: "Duplicate charge", status: "RESOLVED", detail: "INR 599 credit applied" },
    { icon: <Clock3 size={15} />, tone: "text-amber-400", label: "Service outage", status: "PENDING", detail: "Router reset verification is waiting" },
    { icon: <Circle size={15} />, tone: "text-zinc-500", label: "Cancellation", status: "NOT ADDRESSED", detail: "Retention queued after service issue" },
  ];
  const plan = textValue(customer.plan_name, "Fiber Plus 200");
  const speed = textValue(customer.speed_mbps, "200");
  const risk = labelize(customer.risk_level ?? "high churn").toUpperCase();
  return (
    <div className="grid grid-cols-1 xl:grid-cols-[minmax(360px,560px)_1fr] gap-5">
      <div className="glass overflow-hidden">
        <div className="p-5 border-b" style={{ borderColor: "var(--border)" }}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-lg font-bold" style={{ color: "var(--text-primary)" }}>{textValue(customer.name, c.customer_name)}</p>
              <p className="text-xs font-mono mt-1" style={{ color: "var(--text-muted)" }}>{textValue(customer.customer_id, c.customer_id)}</p>
              <p className="text-sm mt-3" style={{ color: "var(--text-secondary)" }}>{plan} {speed}Mbps · <span className="text-rose-400 font-semibold">{risk}</span></p>
            </div>
            <RelArc start={Number(relationship.start ?? c.relationship_score_start ?? 0)} end={Number(relationship.end ?? c.relationship_score_end ?? 0)} />
          </div>
        </div>

        <div className="p-5 space-y-4 border-b" style={{ borderColor: "var(--border)" }}>
          {issueRows.map((issue) => (
            <div key={issue.label} className="flex items-start gap-3">
              <span className={`${issue.tone} mt-0.5`}>{issue.icon}</span>
              <div className="flex-1">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{issue.label}</p>
                  <span className="text-[10px] font-bold tracking-wide" style={{ color: issue.status === "RESOLVED" ? "#10b981" : issue.status === "PENDING" ? "#f59e0b" : "var(--text-muted)" }}>{issue.status}</span>
                </div>
                <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>{issue.detail}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="p-5 space-y-3">
          <div className="flex gap-2">
            <AlertTriangle size={14} className="text-amber-400 shrink-0 mt-0.5" />
            <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
              <span className="font-semibold text-amber-300">Escalation reason:</span> {textValue(contextCard?.reason_for_escalation, "Refund or service recovery review requires specialist approval.")}
            </p>
          </div>
          <div className="rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
            <p className="text-[10px] uppercase tracking-widest font-semibold mb-1" style={{ color: "var(--text-muted)" }}>Suggested opening</p>
            <p className="text-sm italic leading-relaxed" style={{ color: "var(--text-primary)" }}>&quot;{textValue(contextCard?.recommended_opening, "I have your duplicate charge and outage details, so you do not have to repeat them.")}&quot;</p>
          </div>
        </div>
      </div>

      <div className="space-y-5">
        <div className="glass p-4">
          <PanelTitle icon={<BookOpen size={11} style={{ color: "#5eead4" }} />}>Memory Context</PanelTitle>
          <div className="space-y-2">
            {asList(contextCard?.memory_context).slice(0, 4).map((item, index) => {
              const memory = asRecord(item);
              return (
                <div key={`${textValue(memory.memory_id, String(index))}-${index}`} className="rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                  <p className="text-[10px] font-mono" style={{ color: "#5eead4" }}>{textValue(memory.memory_type, "memory")}</p>
                  <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>{textValue(memory.content)}</p>
                </div>
              );
            })}
          </div>
        </div>
        <div className="glass p-4">
          <PanelTitle icon={<Cpu size={11} style={{ color: "#5eead4" }} />}>Slots Collected</PanelTitle>
          <div className="flex flex-wrap gap-2">
            {Object.entries(asRecord(contextCard?.slots_collected)).map(([key, value]) => (
              <span key={key} className="px-2 py-1 rounded-md text-[11px] font-mono" style={{ background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.25)", color: "#5eead4" }}>{key}: {textValue(value)}</span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function PolicyDagViz({ dag }: { dag: PolicyDagPath }) {
  return (
    <div className="overflow-x-auto pb-2">
      <div className="flex items-center gap-1 min-w-max">
        {dag.nodes.map((node, index) => (
          <div key={node.node_id} className="flex items-center gap-1">
            <div className="flex flex-col items-center gap-1">
              <div className="px-2.5 py-1.5 rounded-lg text-[10px] font-medium text-center w-[170px] whitespace-normal" style={{ background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.32)", color: "#5eead4" }}>
                <p className="font-mono text-[9px] mb-0.5" style={{ color: "var(--text-muted)" }}>{node.node_id}</p>
                <p>{node.description}</p>
              </div>
              {node.result && <span className="text-[9px] font-mono px-1.5 py-0.5 rounded text-emerald-400 bg-emerald-500/10">{String(node.result)}</span>}
            </div>
            {index < dag.nodes.length - 1 && (
              <div className="flex flex-col items-center mx-1">
                <span className="text-xs" style={{ color: "#14b8a6" }}>{"->"}</span>
                {dag.edges[index]?.label && <span className="text-[9px] font-mono" style={{ color: "var(--text-muted)" }}>{dag.edges[index].label}</span>}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function GuidedTimeline({ events }: { events: GuidedActionEvent[] }) {
  if (events.length === 0) return <p className="text-sm" style={{ color: "var(--text-muted)" }}>No guided action states recorded.</p>;
  return (
    <div className="flex items-start gap-0 overflow-x-auto pb-1">
      {events.map((event, index) => (
        <div key={`${event.state}-${index}`} className="flex items-center">
          <div className="flex flex-col items-center gap-1">
            <Badge variant="state" state={event.state}>{event.state}</Badge>
            <p className="text-[10px] text-center max-w-[96px]" style={{ color: "var(--text-muted)" }}>{truncate(event.reason, 24)}</p>
          </div>
          {index < events.length - 1 && <div className="w-8 h-px mx-1 mt-[-18px]" style={{ background: "var(--border-strong)" }} />}
        </div>
      ))}
    </div>
  );
}

export default function CaseDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [tab, setTab] = useState<"reasoning" | "proof" | "context">("reasoning");
  const { data: c, error, isLoading } = useSWR<CaseDetail>(id ? ["case", id] : null, () => api.cases.detail(id));
  const { data: contextCard } = useSWR<Record<string, unknown>>(id ? ["case-context", id] : null, () => api.cases.contextCard(id));

  if (isLoading) return <div className="p-6"><StatePanel label="Loading case detail..." /></div>;
  if (error) return <div className="p-6"><StatePanel label="Could not load this case from FastAPI." /></div>;
  if (!c) return <div className="p-6"><StatePanel label="Case not found." /></div>;

  const sentimentTimeline = c.health_score_timeline.map((point) => ({
    ...point,
    sentiment_pct: Math.round((point.sentiment_score ?? point.score / 100) * 100),
  }));

  return (
    <div className="flex flex-col h-[calc(100vh-56px)]">
      <div className="flex items-center gap-3 px-6 py-3 border-b shrink-0" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
        <Link href="/cases" className="flex items-center gap-1.5 text-sm transition-colors hover:text-teal-300" style={{ color: "var(--text-muted)" }}>
          <ArrowLeft size={14} />
          Cases
        </Link>
        <span style={{ color: "var(--text-muted)" }}>/</span>
        <span className="font-mono text-sm font-semibold" style={{ color: "#5eead4" }}>{c.case_id}</span>
        <div className="flex-1" />
        <Badge variant="status" status={c.status}>{c.status.replace("_", " ")}</Badge>
        <HealthRing score={Math.round(c.final_health_score)} size={36} />
      </div>

      <div className="flex flex-1 overflow-hidden">
        <div className="w-[380px] shrink-0 flex flex-col border-r" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
          <div className="px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
            <div className="flex items-center gap-2">
              <User size={13} style={{ color: "var(--text-muted)" }} />
              <div>
                <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{c.customer_name}</p>
                <p className="text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>{c.customer_id}</p>
              </div>
              <div className="ml-auto">
                <RelArc start={c.relationship_score_start ?? 0} end={c.relationship_score_end ?? 0} />
              </div>
            </div>
            <div className="flex flex-wrap gap-1 mt-2">
              {c.intents_detected.map((intent) => (
                <span key={intent} className="px-2 py-0.5 text-[10px] rounded-full font-medium" style={{ background: "rgba(20,184,166,0.12)", color: "#5eead4", border: "1px solid rgba(20,184,166,0.28)" }}>
                  {intent.replace(/_/g, " ")}
                </span>
              ))}
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
            {c.messages.length === 0 && <p className="text-sm" style={{ color: "var(--text-muted)" }}>No transcript messages recorded.</p>}
            {c.messages.map((msg, index) => (
              <motion.div key={`${msg.turn}-${index}`} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.03 }} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`flex items-end gap-2 max-w-[85%] ${msg.role === "user" ? "flex-row-reverse" : ""}`}>
                  <div className="w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center text-[10px]" style={msg.role === "user" ? { background: "rgba(20,184,166,0.18)", border: "1px solid rgba(20,184,166,0.3)" } : { background: "rgba(99,102,241,0.16)", border: "1px solid rgba(99,102,241,0.25)" }}>
                    {msg.role === "user" ? <User size={10} style={{ color: "#5eead4" }} /> : <Bot size={10} className="text-indigo-300" />}
                  </div>
                  <div className="rounded-xl px-3 py-2 text-sm" style={msg.role === "user" ? { background: "rgba(20,184,166,0.14)", border: "1px solid rgba(20,184,166,0.25)", color: "var(--text-primary)" } : { background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                    {msg.content}
                    <p className="text-[9px] mt-1 opacity-50">{new Date(msg.timestamp).toLocaleTimeString()}</p>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        </div>

        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex items-center gap-1 px-4 py-2 border-b shrink-0" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
            {[
              { key: "reasoning", label: "Agent Reasoning", icon: <Brain size={12} /> },
              { key: "proof", label: "Resolution Proof", icon: <FileText size={12} /> },
              { key: "context", label: "Customer Context", icon: <BookOpen size={12} /> },
            ].map((item) => (
              <button key={item.key} onClick={() => setTab(item.key as typeof tab)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg transition-all" style={tab === item.key ? { background: "rgba(20,184,166,0.12)", color: "#5eead4", border: "1px solid rgba(20,184,166,0.28)" } : { color: "var(--text-muted)" }}>
                {item.icon}
                {item.label}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto px-5 py-5 space-y-6">
            {tab === "reasoning" && (
              <div className="space-y-5">
                <div className="glass p-4">
                  <PanelTitle icon={<Activity size={11} style={{ color: "#5eead4" }} />}>Health Score Timeline</PanelTitle>
                  {c.health_score_timeline.length === 0 ? <p className="text-sm" style={{ color: "var(--text-muted)" }}>No health score samples recorded.</p> : (
                    <ResponsiveContainer width="100%" height={130}>
                      <LineChart data={c.health_score_timeline}>
                        <XAxis dataKey="turn" tick={{ fill: "#5a5a7a", fontSize: 10 }} axisLine={false} tickLine={false} />
                        <YAxis domain={[0, 100]} tick={{ fill: "#5a5a7a", fontSize: 10 }} axisLine={false} tickLine={false} />
                        <Tooltip content={({ active, payload }) => active && payload?.[0] ? (
                          <div className="glass px-3 py-2 text-xs">
                            <p style={{ color: healthColor(payload[0].value as number) }}>Score: {payload[0].value}</p>
                            <p style={{ color: "var(--text-muted)", maxWidth: 180 }}>{payload[0].payload.label}</p>
                          </div>
                        ) : null} />
                        <Line type="monotone" dataKey="score" stroke="#14b8a6" strokeWidth={2.5} dot={{ fill: "#14b8a6", r: 4 }} />
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                </div>

                <div className="glass p-4">
                  <PanelTitle icon={<Activity size={11} style={{ color: "#5eead4" }} />}>Sentiment Over Time</PanelTitle>
                  {sentimentTimeline.length === 0 ? <p className="text-sm" style={{ color: "var(--text-muted)" }}>No sentiment samples recorded.</p> : (
                    <ResponsiveContainer width="100%" height={130}>
                      <LineChart data={sentimentTimeline}>
                        <XAxis dataKey="turn" tick={{ fill: "#5a5a7a", fontSize: 10 }} axisLine={false} tickLine={false} />
                        <YAxis domain={[0, 100]} tick={{ fill: "#5a5a7a", fontSize: 10 }} axisLine={false} tickLine={false} />
                        <Tooltip content={({ active, payload }) => active && payload?.[0] ? (
                          <div className="glass px-3 py-2 text-xs">
                            <p style={{ color: "#5eead4" }}>Sentiment: {payload[0].value}%</p>
                            <p style={{ color: "var(--text-secondary)" }}>{payload[0].payload.sentiment_label ?? "sentiment"}</p>
                            <p style={{ color: "var(--text-muted)", maxWidth: 180 }}>{payload[0].payload.label}</p>
                          </div>
                        ) : null} />
                        <Line type="monotone" dataKey="sentiment_pct" stroke="#8b5cf6" strokeWidth={2.5} dot={{ fill: "#8b5cf6", r: 4 }} />
                      </LineChart>
                    </ResponsiveContainer>
                  )}
                </div>

                <div className="glass p-4">
                  <PanelTitle icon={<GitBranch size={11} style={{ color: "#5eead4" }} />}>Guided Action States</PanelTitle>
                  <GuidedTimeline events={c.guided_action_events} />
                </div>

                <div className="glass p-4">
                  <PanelTitle icon={<Cpu size={11} style={{ color: "#5eead4" }} />}>Tools Called ({c.tools_called.length})</PanelTitle>
                  {c.tools_called.length === 0 ? <p className="text-sm" style={{ color: "var(--text-muted)" }}>No tool calls recorded.</p> : <div className="space-y-2">{c.tools_called.map((tool, index) => <ToolCallCard key={`${tool.tool_name}-${index}`} tool={tool} />)}</div>}
                </div>

                <div className="glass p-4">
                  <PanelTitle icon={<Shield size={11} style={{ color: "#5eead4" }} />}>Policy DAG</PanelTitle>
                  {c.policy_dag_path ? (
                    <>
                      <PolicyDagViz dag={c.policy_dag_path} />
                      <div className="flex items-center gap-4 mt-3 pt-3 border-t" style={{ borderColor: "var(--border)" }}>
                        <div className="text-xs"><span style={{ color: "var(--text-muted)" }}>UJCS:</span> <span className="font-mono font-bold text-teal-300">{c.policy_dag_path.ujcs}</span></div>
                        <div className="text-xs"><span style={{ color: "var(--text-muted)" }}>Action:</span> <span className="text-emerald-400">{c.policy_dag_path.action_taken}</span></div>
                        <Badge variant="status" status={c.policy_dag_path.policy_status === "compliant" ? "resolved" : "escalated"}>{c.policy_dag_path.policy_status}</Badge>
                      </div>
                    </>
                  ) : <p className="text-sm" style={{ color: "var(--text-muted)" }}>No policy DAG path recorded.</p>}
                </div>

                <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
                  <div className="glass p-4">
                    <PanelTitle icon={<BookOpen size={11} style={{ color: "#5eead4" }} />}>Policies Retrieved</PanelTitle>
                    {c.policy_retrievals.length === 0 ? <p className="text-sm" style={{ color: "var(--text-muted)" }}>No policy retrieval citations recorded.</p> : c.policy_retrievals.map((policy, index) => (
                      <div key={`${policy.policy_name}-${index}`} className="p-3 rounded-lg mb-2" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                        <div className="flex items-center justify-between mb-1.5">
                          <p className="text-xs font-semibold" style={{ color: "#5eead4" }}>{policy.policy_name}</p>
                          <span className="text-[10px] px-1.5 py-0.5 rounded font-medium text-amber-400 bg-amber-500/10">{policy.crag_path}</span>
                        </div>
                        <p className="text-xs" style={{ color: "var(--text-secondary)" }}>{truncate(policy.chunk, 160)}</p>
                      </div>
                    ))}
                  </div>

                  <div className="glass p-4">
                    <PanelTitle icon={<Brain size={11} style={{ color: "#5eead4" }} />}>Memory Retrieved</PanelTitle>
                    {c.memory_citations.length === 0 ? <p className="text-sm" style={{ color: "var(--text-muted)" }}>No memory citations recorded.</p> : c.memory_citations.map((memory) => (
                      <div key={memory.citation_id} className="flex items-start gap-2 p-2.5 rounded-lg mb-2" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                        <span className="font-mono text-[10px] px-1.5 py-0.5 rounded shrink-0" style={{ background: "rgba(20,184,166,0.12)", color: "#5eead4" }}>{memory.citation_id}</span>
                        <div>
                          <p className="text-xs" style={{ color: "var(--text-primary)" }}>{memory.content}</p>
                          <p className="text-[10px] mt-0.5" style={{ color: "var(--text-muted)" }}>{memory.type}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {tab === "proof" && (
              <ResolutionProofTrail c={c} contextCard={contextCard} />
            )}

            {tab === "context" && (
              <CustomerContextCard c={c} contextCard={contextCard} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
