"use client";

import { useState, type ReactNode } from "react";
import useSWR from "swr";
import { Clock3, Headphones, MessageSquareText, Send, ShieldCheck, UserCheck } from "lucide-react";
import { GlassPanel, PageHeader, SectionLabel, StatusPill } from "@/components/BlueprintPrimitives";
import { api } from "@/lib/api";
import type { AgentDeskHandoffDetail, AgentDeskProactiveResponse, AgentDeskQueueItem, AgentDeskQueueResponse } from "@/lib/types";

const statusTone: Record<AgentDeskQueueItem["status"], "amber" | "indigo" | "green"> = {
  waiting: "amber",
  assigned: "indigo",
  resolved: "green",
};

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function issueLabel(value: string) {
  return value.replaceAll("_", " ");
}

export default function AgentDeskPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const [resolving, setResolving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const { data, error, isLoading, mutate: refreshQueue } = useSWR<AgentDeskQueueResponse>(
    "agent-desk-queue",
    api.agentDesk.queue,
    { refreshInterval: 5000 }
  );
  const { data: proactiveData } = useSWR<AgentDeskProactiveResponse>(
    "agent-desk-proactive",
    api.agentDesk.proactive,
    { refreshInterval: 5000 }
  );
  const queue = data?.queue ?? [];
  const proactiveContacts = proactiveData?.contacts ?? [];
  // Only fall back to queue[0] when nothing has been picked yet -- if the operator's
  // selection drops out of a poll (e.g. resolved elsewhere), show the empty state
  // rather than silently switching them to a different customer's transcript.
  const selected = selectedId === null ? queue[0] : queue.find((item) => item.handoff_id === selectedId);
  const { data: detail, mutate: refreshDetail } = useSWR<AgentDeskHandoffDetail>(
    selected?.handoff_id ? ["agent-desk-detail", selected.handoff_id] : null,
    () => api.agentDesk.detail(selected!.handoff_id),
    { refreshInterval: 5000 }
  );
  const waiting = queue.filter((item) => item.status === "waiting").length;
  const assigned = queue.filter((item) => item.status === "assigned").length;
  const resolved = queue.filter((item) => item.status === "resolved").length;

  async function sendReply() {
    const normalized = reply.trim();
    if (!selected || !normalized || sending) return;
    setSending(true);
    setActionError(null);
    try {
      await api.agentDesk.reply(selected.handoff_id, normalized, "ResolveFlow Specialist");
      setReply("");
      await Promise.all([refreshQueue(), refreshDetail()]);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to send the reply. Please try again.");
    } finally {
      setSending(false);
    }
  }

  async function resolveHandoff() {
    if (!selected || selected.status === "resolved" || resolving) return;
    setResolving(true);
    setActionError(null);
    try {
      await api.agentDesk.resolve(
        selected.handoff_id,
        "Human specialist completed takeover and resolved the remaining issue.",
        "ResolveFlow Specialist"
      );
      await Promise.all([refreshQueue(), refreshDetail()]);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to resolve the handoff. Please try again.");
    } finally {
      setResolving(false);
    }
  }

  return (
    <div className="p-6 max-w-7xl">
      <PageHeader
        eyebrow="Human support"
        title="Agent Desk"
        subtitle="Live handoff queue for specialist takeover. The page polls FastAPI, shows customer context, and keeps the recommended first sentence visible for a smooth handoff."
        action={
          <div className="rounded-lg px-3 py-2 text-xs font-mono" style={{ background: "rgba(20,184,166,0.08)", border: "1px solid rgba(20,184,166,0.25)", color: "#5eead4" }}>
            Polling · 5s
          </div>
        }
      />

      <div className="mb-5 grid gap-3 md:grid-cols-3">
        <DeskMetric label="Waiting" value={waiting} icon={<Clock3 size={16} />} />
        <DeskMetric label="Assigned" value={assigned} icon={<UserCheck size={16} />} />
        <DeskMetric label="Resolved" value={resolved} icon={<ShieldCheck size={16} />} />
      </div>

      {error && (
        <GlassPanel className="mb-5 p-4 text-sm" >
          <p style={{ color: "#fb7185" }}>Could not load the human handoff queue from FastAPI.</p>
        </GlassPanel>
      )}

      <div className="grid gap-5 xl:grid-cols-[0.9fr_1.1fr]">
        <GlassPanel className="p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <SectionLabel>Live Queue</SectionLabel>
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              {isLoading && !data ? "Loading..." : `${queue.length} handoffs`}
            </span>
          </div>

          <div className="space-y-3">
            {queue.length === 0 && (
              <div className="rounded-lg p-4 text-sm" style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                No handoffs are waiting right now.
              </div>
            )}

            {queue.map((item) => {
              const active = selected?.handoff_id === item.handoff_id;
              return (
              <button
                key={item.handoff_id}
                onClick={() => setSelectedId(item.handoff_id)}
                className="w-full rounded-lg p-4 text-left transition-all"
                style={{
                  background: active ? "rgba(20,184,166,0.09)" : "var(--surface-3)",
                  border: active ? "1px solid rgba(20,184,166,0.35)" : "1px solid var(--border)",
                }}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{item.customer_name}</p>
                    <p className="mt-1 text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>{item.case_id} · {item.customer_id}</p>
                  </div>
                  <StatusPill tone={statusTone[item.status]}>{item.status.toUpperCase()}</StatusPill>
                </div>
                <p className="mt-3 text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{item.handoff_reason}</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {item.intents.map((intent) => (
                    <span key={intent} className="rounded-md px-2 py-1 text-[11px]" style={{ color: "#5eead4", background: "rgba(20,184,166,0.09)", border: "1px solid rgba(20,184,166,0.24)" }}>
                      {issueLabel(intent)}
                    </span>
                  ))}
                </div>
                <p className="mt-3 text-[11px]" style={{ color: "var(--text-muted)" }}>
                  {formatTime(item.created_at)} · Health {item.health_score ?? "--"} · UJCS {item.ujcs ?? "--"}
                </p>
              </button>
            )})}
          </div>

          <div className="mt-6">
            <div className="mb-3 flex items-center justify-between gap-3">
              <SectionLabel>Proactive Outreach</SectionLabel>
              <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                {proactiveContacts.length} contacts
              </span>
            </div>
            <div className="space-y-3">
              {proactiveContacts.slice(0, 6).map((contact) => {
                const credit = contact.credit ?? {};
                const creditId = typeof credit.credit_id === "string" ? credit.credit_id : "policy checked";
                const amount = typeof credit.amount === "number" ? `₹${credit.amount}` : contact.status;
                return (
                  <div
                    key={contact.session_id}
                    className="rounded-lg p-3"
                    style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{contact.customer_name}</p>
                        <p className="mt-1 text-[11px] font-mono" style={{ color: "var(--text-muted)" }}>{contact.customer_id} · {contact.location}</p>
                      </div>
                      <StatusPill tone={contact.status === "credited" ? "green" : "amber"}>{contact.status}</StatusPill>
                    </div>
                    <p className="mt-2 text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{contact.message}</p>
                    <p className="mt-2 text-[11px] font-mono" style={{ color: "#5eead4" }}>{creditId} · {amount}</p>
                  </div>
                );
              })}
              {proactiveContacts.length === 0 && (
                <p className="rounded-lg p-3 text-sm" style={{ color: "var(--text-secondary)", background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                  No proactive outage contacts yet.
                </p>
              )}
            </div>
          </div>
        </GlassPanel>

        <GlassPanel className="p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <SectionLabel>Takeover Context</SectionLabel>
            {selected && <StatusPill tone={statusTone[selected.status]}>{selected.handoff_id}</StatusPill>}
          </div>

          {!selected ? (
            <div className="rounded-lg p-4 text-sm" style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
              Selectable context appears when the queue has a handoff.
            </div>
          ) : (
            <div className="space-y-4">
              <div className="rounded-lg p-4" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-3">
                  <span className="flex h-10 w-10 items-center justify-center rounded-full" style={{ background: "rgba(20,184,166,0.12)", color: "#5eead4", border: "1px solid rgba(20,184,166,0.25)" }}>
                    <Headphones size={18} />
                  </span>
                  <div>
                    <p className="font-semibold" style={{ color: "var(--text-primary)" }}>{selected.customer_name}</p>
                    <p className="text-xs" style={{ color: "var(--text-muted)" }}>{selected.plan_id ?? "plan unknown"} · risk {selected.risk_level ?? "unknown"}</p>
                  </div>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-3">
                  <SmallStat label="Churn" value={`${Math.round(selected.churn_score * 100)}%`} />
                  <SmallStat label="Messages" value={selected.message_count} />
                  <SmallStat label="Policy" value={selected.policy_status ?? "pending"} />
                </div>
              </div>

              <div className="rounded-lg p-4" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <div className="mb-2 flex items-center gap-2">
                  <MessageSquareText size={16} style={{ color: "#5eead4" }} />
                  <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Recommended opening line</p>
                </div>
                <p className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                  {detail?.opening_line?.opening_line ?? selected.recommended_opening_line}
                </p>
              </div>

              <div className="rounded-lg p-4" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>AI co-pilot</p>
                    <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>Suggested replies grounded in tool evidence.</p>
                  </div>
                  <StatusPill tone="indigo">{detail?.copilot_suggestions?.length ?? 0} suggestions</StatusPill>
                </div>
                <div className="space-y-3">
                  {(detail?.copilot_suggestions ?? []).map((suggestion) => (
                    <button
                      key={suggestion.id}
                      onClick={() => setReply(suggestion.reply)}
                      className="w-full rounded-lg p-3 text-left transition-all hover:opacity-90"
                      style={{ background: "rgba(255,255,255,0.04)", border: "1px solid var(--border)" }}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{suggestion.title}</p>
                        <span className="font-mono text-[11px]" style={{ color: "#5eead4" }}>
                          {Math.round(suggestion.confidence * 100)}%
                        </span>
                      </div>
                      <p className="mt-2 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>{suggestion.reply}</p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {suggestion.evidence.map((evidence, index) => (
                          <span
                            key={`${suggestion.id}-${evidence.label}-${index}`}
                            className="rounded-md px-2 py-1 text-[11px]"
                            style={{ color: "#99f6e4", background: "rgba(20,184,166,0.08)", border: "1px solid rgba(20,184,166,0.22)" }}
                          >
                            {evidence.label}: {evidence.detail}
                          </span>
                        ))}
                      </div>
                    </button>
                  ))}
                  {!detail?.copilot_suggestions?.length && (
                    <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                      Suggestions appear after the handoff detail loads.
                    </p>
                  )}
                </div>
              </div>

              <div className="rounded-lg p-4" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Context card</p>
                <div className="mt-3 grid gap-2 md:grid-cols-2">
                  <SmallStat label="Session" value={detail?.session_id ?? selected.session_id ?? "unknown"} />
                  <SmallStat label="Case" value={selected.case_id} />
                  <SmallStat label="Remaining" value={contextList(detail?.context_card?.issues_remaining).join(", ") || "review"} />
                  <SmallStat label="Status" value={selected.status} />
                </div>
              </div>

              <div className="rounded-lg p-4" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Full transcript</p>
                <div className="mt-3 max-h-72 space-y-2 overflow-y-auto pr-1">
                  {(detail?.transcript?.length ? detail.transcript : []).map((turn, index) => (
                    <div key={`${turn.role}-${index}`} className="rounded-lg p-3" style={{ background: "rgba(255,255,255,0.04)", border: "1px solid var(--border)" }}>
                      <p className="text-[10px] font-semibold uppercase tracking-widest" style={{ color: "#5eead4" }}>{String(turn.role ?? "turn")}</p>
                      <p className="mt-1 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                        {(() => {
                          const raw = turn.content ?? turn.message ?? "";
                          return typeof raw === "string" ? raw : JSON.stringify(raw);
                        })()}
                      </p>
                    </div>
                  ))}
                  {!detail?.transcript?.length && (
                    <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
                      {selected.last_customer_message ?? "No transcript is available in the conversation record."}
                    </p>
                  )}
                </div>
              </div>

              <div className="rounded-lg p-4" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Reply as specialist</p>
                  <button
                    onClick={() => void resolveHandoff()}
                    disabled={selected.status === "resolved" || resolving}
                    className="rounded-lg px-3 py-2 text-xs font-semibold disabled:opacity-50"
                    style={{ background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.32)", color: "#5eead4" }}
                  >
                    {selected.status === "resolved" ? "Resolved" : resolving ? "Resolving" : "Resolve handoff"}
                  </button>
                </div>
                {actionError && (
                  <p className="mt-2 text-xs" style={{ color: "#fb7185" }}>{actionError}</p>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  <input
                    value={reply}
                    onChange={(event) => setReply(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
                      event.preventDefault();
                      void sendReply();
                    }}
                    placeholder="Write a concise human reply..."
                    className="flex-1 min-w-[140px] rounded-lg px-3 py-2 text-sm outline-none"
                    style={{ background: "rgba(255,255,255,0.04)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  />
                  <button
                    onClick={() => void sendReply()}
                    disabled={!reply.trim() || sending}
                    className="inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"
                    style={{ background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.32)", color: "#5eead4" }}
                  >
                    <Send size={14} />
                    {sending ? "Sending" : "Send"}
                  </button>
                </div>
              </div>
            </div>
          )}
        </GlassPanel>
      </div>
    </div>
  );
}

function DeskMetric({ label, value, icon }: { label: string; value: number; icon: ReactNode }) {
  return (
    <GlassPanel className="p-4">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>{label}</p>
        <span style={{ color: "#5eead4" }}>{icon}</span>
      </div>
      <p className="mt-3 text-2xl font-bold font-mono" style={{ color: "#5eead4" }}>{value}</p>
    </GlassPanel>
  );
}

function contextList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item)).filter(Boolean);
}

function SmallStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg p-3" style={{ background: "rgba(255,255,255,0.04)", border: "1px solid var(--border)" }}>
      <p className="text-[10px] uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>{label}</p>
      <p className="mt-1 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{value}</p>
    </div>
  );
}
