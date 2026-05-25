"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import {
  ArrowRight,
  Bot,
  Brain,
  CheckCircle2,
  Cpu,
  FileText,
  GitBranch,
  MessageSquareText,
  ShieldCheck,
  User,
} from "lucide-react";
import { Badge, HealthRing, RelArc } from "@/components/Badges";
import { api } from "@/lib/api";
import { formatDate, healthColor, truncate } from "@/lib/utils";
import type { CaseDetail, CaseListResponse, CaseRow, PolicyDagPath, ToolCall } from "@/lib/types";

const routeButtons = [
  { href: "/overview", label: "Overview" },
  { href: "/cases", label: "Cases" },
  { href: "/evaluation", label: "Evaluation" },
];

function Glass({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <section className={`glass ${className}`}>
      {children}
    </section>
  );
}

function SectionTitle({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <span className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.25)" }}>
        {icon}
      </span>
      <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>{label}</p>
    </div>
  );
}

function initials(name: string) {
  return name.split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase();
}

function routeId(row: CaseRow) {
  return row.route_id ?? row.case_id;
}

function DemoCaseButton({ row, active, onClick }: { row: CaseRow; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="min-w-[230px] text-left rounded-lg p-3 transition-all"
      style={
        active
          ? { background: "rgba(20,184,166,0.13)", border: "1px solid rgba(20,184,166,0.38)" }
          : { background: "var(--surface-2)", border: "1px solid var(--border)" }
      }
    >
      <div className="flex items-center justify-between gap-3">
        <span className="font-mono text-xs font-semibold" style={{ color: active ? "#5eead4" : "var(--text-secondary)" }}>{row.case_id}</span>
        <HealthRing score={Math.round(row.health_score)} size={30} />
      </div>
      <p className="mt-1 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{row.customer_name}</p>
      <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>{row.issues.slice(0, 3).join(" · ")}</p>
    </button>
  );
}

function ChatTranscript({ detail }: { detail: CaseDetail }) {
  return (
    <Glass className="flex flex-col min-h-[610px] overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold" style={{ background: "rgba(99,102,241,0.22)", color: "#c7d2fe", border: "1px solid rgba(99,102,241,0.35)" }}>
            {initials(detail.customer_name)}
          </div>
          <div>
            <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{detail.customer_name}</p>
            <p className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>{detail.case_id} · {detail.status.replace("_", " ")}</p>
          </div>
        </div>
        <Badge variant="status" status={detail.status}>{detail.status.replace("_", " ")}</Badge>
      </div>

      <div className="flex-1 p-4 space-y-4 overflow-y-auto">
        {detail.messages.map((message, index) => {
          const user = message.role === "user";
          return (
            <div key={`${message.turn}-${index}`} className={`flex ${user ? "justify-end" : "justify-start"}`}>
              <div className={`flex gap-2 max-w-[82%] ${user ? "flex-row-reverse" : ""}`}>
                <span className="w-7 h-7 rounded-full flex items-center justify-center shrink-0" style={user ? { background: "rgba(20,184,166,0.14)", color: "#5eead4" } : { background: "rgba(99,102,241,0.18)", color: "#a5b4fc" }}>
                  {user ? <User size={13} /> : <Bot size={13} />}
                </span>
                <div className="rounded-xl px-3 py-2 text-sm leading-relaxed" style={user ? { background: "rgba(20,184,166,0.15)", border: "1px solid rgba(20,184,166,0.26)", color: "var(--text-primary)" } : { background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                  {message.content}
                  <p className="text-[10px] mt-1" style={{ color: "var(--text-muted)" }}>{new Date(message.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}</p>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="px-4 py-3 border-t flex gap-2" style={{ borderColor: "var(--border)" }}>
        <input
          readOnly
          value={detail.status === "in_progress" ? "Customer response pending..." : ""}
          placeholder="Demo chat input is read-only"
          className="flex-1 rounded-lg px-3 py-2 text-sm outline-none"
          style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
        />
        <button className="px-4 py-2 rounded-lg text-sm font-semibold" style={{ background: "var(--accent-dim)", border: "1px solid var(--border-strong)", color: "#a5b4fc" }}>
          Send
        </button>
      </div>
    </Glass>
  );
}

function ScorePanel({ detail }: { detail: CaseDetail }) {
  const latest = detail.final_health_score;
  const waiting = detail.status === "in_progress";
  return (
    <Glass className="p-4">
      <SectionTitle icon={<Brain size={14} style={{ color: "#5eead4" }} />} label="Live Reasoning" />
      <div className="grid grid-cols-[auto_1fr] gap-4 items-center">
        <HealthRing score={Math.round(latest)} size={72} />
        <div>
          <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            {waiting ? "WAITING - dual control" : detail.status.replace("_", " ").toUpperCase()}
          </p>
          <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
            {waiting ? "The agent has verified evidence and is waiting for one customer-side action before continuing." : "The case is no longer waiting on the customer."}
          </p>
          <div className="mt-3">
            <RelArc start={detail.relationship_score_start ?? 0} end={detail.relationship_score_end ?? 0} />
          </div>
        </div>
      </div>
      <div className="mt-4 space-y-2">
        {detail.health_score_timeline.slice(-4).map((point) => (
          <div key={`${point.turn}-${point.score}`} className="grid grid-cols-[38px_1fr_38px] gap-2 items-center">
            <span className="text-[10px] font-mono" style={{ color: "var(--text-muted)" }}>T{point.turn}</span>
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--surface-3)" }}>
              <div className="h-full rounded-full" style={{ width: `${point.score}%`, background: healthColor(point.score) }} />
            </div>
            <span className="text-[10px] font-mono text-right" style={{ color: healthColor(point.score) }}>{Math.round(point.score)}</span>
          </div>
        ))}
      </div>
    </Glass>
  );
}

function IntentPanel({ detail }: { detail: CaseDetail }) {
  return (
    <Glass className="p-4">
      <SectionTitle icon={<MessageSquareText size={14} style={{ color: "#5eead4" }} />} label="Detected Issues" />
      <div className="space-y-3">
        {detail.intents_detected.map((intent, index) => (
          <div key={intent} className="flex items-start gap-2.5">
            <span className="w-2 h-2 rounded-full mt-1.5" style={{ background: index === 0 ? "#10b981" : index === 1 ? "#f59e0b" : "#818cf8" }} />
            <div>
              <p className="text-sm font-medium capitalize" style={{ color: "var(--text-primary)" }}>{intent.replace(/_/g, " ")}</p>
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                {index === 0 ? "Resolved first when evidence is complete." : index === 1 ? "Active verification path." : "Queued after higher-priority issues."}
              </p>
            </div>
          </div>
        ))}
      </div>
    </Glass>
  );
}

function ToolPanel({ tools }: { tools: ToolCall[] }) {
  return (
    <Glass className="p-4">
      <SectionTitle icon={<Cpu size={14} style={{ color: "#5eead4" }} />} label={`Tools Called (${tools.length})`} />
      <div className="flex flex-wrap gap-1.5">
        {tools.map((tool, index) => (
          <span key={`${tool.tool_name}-${index}`} className="px-2 py-1 rounded-md text-[11px] font-mono" style={{ background: tool.success ? "rgba(16,185,129,0.12)" : "rgba(239,68,68,0.12)", border: `1px solid ${tool.success ? "rgba(16,185,129,0.24)" : "rgba(239,68,68,0.24)"}`, color: tool.success ? "#5eead4" : "#f87171" }}>
            {tool.tool_name}
          </span>
        ))}
      </div>
    </Glass>
  );
}

function PolicyPath({ dag }: { dag: PolicyDagPath | null }) {
  if (!dag) {
    return (
      <Glass className="p-4">
        <SectionTitle icon={<GitBranch size={14} style={{ color: "#5eead4" }} />} label="Policy DAG" />
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>No policy path recorded.</p>
      </Glass>
    );
  }
  return (
    <Glass className="p-4">
      <SectionTitle icon={<GitBranch size={14} style={{ color: "#5eead4" }} />} label="Policy DAG Path" />
      <div className="space-y-2">
        {dag.nodes.map((node, index) => (
          <div key={node.node_id} className="flex items-center gap-2">
            <CheckCircle2 size={14} className="text-emerald-400 shrink-0" />
            <span className="text-xs font-mono px-2 py-1 rounded-md" style={{ background: "var(--surface-3)", color: "#5eead4", border: "1px solid var(--border)" }}>{node.node_id}</span>
            {index < dag.nodes.length - 1 && <ArrowRight size={12} style={{ color: "var(--text-muted)" }} />}
          </div>
        ))}
      </div>
      <div className="mt-4 pt-3 border-t flex items-center justify-between text-xs" style={{ borderColor: "var(--border)" }}>
        <span style={{ color: "var(--text-muted)" }}>UJCS <b className="font-mono text-teal-300">{dag.ujcs}</b></span>
        <Badge variant="status" status={dag.policy_status === "compliant" ? "resolved" : "escalated"}>{dag.policy_status}</Badge>
      </div>
    </Glass>
  );
}

function EvidencePanel({ detail }: { detail: CaseDetail }) {
  return (
    <Glass className="p-4">
      <SectionTitle icon={<FileText size={14} style={{ color: "#5eead4" }} />} label="Memory + Policy Evidence" />
      <div className="grid md:grid-cols-2 gap-3">
        <div className="space-y-2">
          <p className="text-[11px] uppercase tracking-widest font-semibold" style={{ color: "var(--text-muted)" }}>Memory citations</p>
          {detail.memory_citations.slice(0, 4).map((memory) => (
            <div key={memory.citation_id} className="rounded-lg p-2" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
              <p className="text-[10px] font-mono" style={{ color: "#5eead4" }}>{memory.citation_id}</p>
              <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>{truncate(memory.content, 110)}</p>
            </div>
          ))}
        </div>
        <div className="space-y-2">
          <p className="text-[11px] uppercase tracking-widest font-semibold" style={{ color: "var(--text-muted)" }}>Policy retrievals</p>
          {detail.policy_retrievals.slice(0, 4).map((policy, index) => (
            <div key={`${policy.policy_name}-${index}`} className="rounded-lg p-2" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
              <p className="text-[10px] font-mono" style={{ color: "#fbbf24" }}>{policy.crag_path} · {policy.confidence}</p>
              <p className="text-xs mt-1" style={{ color: "var(--text-secondary)" }}>{truncate(policy.chunk, 110)}</p>
            </div>
          ))}
        </div>
      </div>
    </Glass>
  );
}

export default function DemoPage() {
  const [selected, setSelected] = useState<string>("");
  const { data: list, error: listError, isLoading: listLoading } = useSWR<CaseListResponse>("demo-cases", () => api.cases.list(1, 8));
  const cases = useMemo(() => list?.cases ?? [], [list?.cases]);
  const selectedRoute = selected || (cases[0] ? routeId(cases[0]) : "");

  const { data: detail, error: detailError, isLoading: detailLoading } = useSWR<CaseDetail>(
    selectedRoute ? ["demo-case", selectedRoute] : null,
    () => api.cases.detail(selectedRoute),
  );

  return (
    <div className="p-6 max-w-[1500px] space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold gradient-text">Live Demo Chat</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
            Multiple backend-seeded conversations with transcript, tools, memory, health, and policy proof.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {routeButtons.map((item) => (
            <Link key={item.href} href={item.href} className="px-3 py-2 rounded-lg text-xs font-semibold transition-colors hover:text-teal-300" style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
              {item.label}
            </Link>
          ))}
        </div>
      </div>

      <Glass className="p-3">
        <div className="flex items-center justify-between gap-3 mb-3">
          <div>
            <p className="text-xs uppercase tracking-widest font-semibold" style={{ color: "var(--text-muted)" }}>Demo chats from FastAPI</p>
            <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>{list?.total ?? 0} live cases available</p>
          </div>
          {detail && (
            <Link href={`/cases/${encodeURIComponent(selectedRoute)}`} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold" style={{ background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.28)", color: "#5eead4" }}>
              Open full case <ArrowRight size={12} />
            </Link>
          )}
        </div>
        {listLoading && <p className="text-sm" style={{ color: "var(--text-muted)" }}>Loading demo chats...</p>}
        {listError && <p className="text-sm text-rose-400">Could not load demo chats from FastAPI.</p>}
        <div className="flex gap-3 overflow-x-auto pb-1">
          {cases.map((row) => (
            <DemoCaseButton key={row.case_id} row={row} active={selectedRoute === routeId(row)} onClick={() => setSelected(routeId(row))} />
          ))}
        </div>
      </Glass>

      {detailLoading && <Glass className="p-10 text-center text-sm"><span style={{ color: "var(--text-muted)" }}>Loading selected conversation...</span></Glass>}
      {detailError && <Glass className="p-10 text-center text-sm text-rose-400">Could not load selected conversation.</Glass>}

      {detail && (
        <>
          <div className="grid xl:grid-cols-[minmax(520px,1fr)_420px] gap-5">
            <ChatTranscript detail={detail} />
            <div className="space-y-4">
              <ScorePanel detail={detail} />
              <IntentPanel detail={detail} />
              <ToolPanel tools={detail.tools_called} />
            </div>
          </div>

          <div className="grid xl:grid-cols-[440px_1fr] gap-5">
            <PolicyPath dag={detail.policy_dag_path} />
            <EvidencePanel detail={detail} />
          </div>

          <Glass className="p-4 flex flex-wrap items-center gap-3">
            <ShieldCheck size={16} className="text-emerald-400" />
            <span className="text-sm" style={{ color: "var(--text-secondary)" }}>
              {detail.case_id} · {detail.customer_name} · created {formatDate(detail.created_at)} · policy status {detail.policy_dag_path?.policy_status ?? "pending"}
            </span>
          </Glass>
        </>
      )}
    </div>
  );
}
