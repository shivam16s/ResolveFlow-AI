"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  Brain,
  CheckCircle2,
  Circle,
  Database,
  GitBranch,
  Loader2,
  MessageSquare,
  ReceiptText,
  Send,
  ShieldCheck,
  User,
  WifiOff,
  Wrench,
  X,
} from "lucide-react";
import { OutageWidget, InvoiceWidget, CreditWidget } from "../../components/GenerativeUI";

type CustomerProfile = {
  customer_id: string;
  name: string;
  plan: string;
  risk: "HIGH" | "MEDIUM" | "LOW";
  hint: string;
  tone: "red" | "amber" | "green";
};

type ChatMessage = {
  role: "customer" | "agent";
  content: string;
  timestamp: string;
  toolResults?: Record<string, unknown>[];
};

type PipelineStepId = "intent" | "memory" | "policy" | "tools" | "dag" | "response";
type PipelineStatus = "idle" | "running" | "done";

type PipelineStep = {
  id: PipelineStepId;
  label: string;
  status: PipelineStatus;
  result: Record<string, unknown>;
};

const DEMO_CUSTOMERS: CustomerProfile[] = [
  {
    customer_id: "CUST-1001",
    name: "Rahul Sharma",
    plan: "Fiber Pro 200Mbps",
    risk: "HIGH",
    hint: "Duplicate charge + outage history",
    tone: "red",
  },
  {
    customer_id: "CUST-1002",
    name: "Ananya Iyer",
    plan: "Fiber Basic 50Mbps",
    risk: "MEDIUM",
    hint: "Plan change + refund pending",
    tone: "amber",
  },
  {
    customer_id: "CUST-1009",
    name: "Karthik Subramanian",
    plan: "Fiber Plus 100Mbps",
    risk: "LOW",
    hint: "Router diagnostic history",
    tone: "green",
  },
];

const ISSUE_CHIPS = [
  {
    label: "Charged twice this month",
    message: "I was charged twice this month and want a refund",
    icon: ReceiptText,
  },
  {
    label: "Internet not working",
    message: "My internet has been down since yesterday, still not working",
    icon: WifiOff,
  },
  {
    label: "Want to cancel",
    message: "I want to cancel my subscription",
    icon: X,
  },
  {
    label: "Charged twice + internet down",
    message: "I was charged twice this month and my internet is still not working. I want to cancel.",
    icon: AlertTriangle,
  },
  {
    label: "Angry repeat request",
    message: "This is ridiculous. I am angry because I already asked for this refund. Do not make me repeat everything again.",
    icon: AlertTriangle,
  },
];

const PIPELINE_TEMPLATE: PipelineStep[] = [
  { id: "intent", label: "Detecting intent", status: "idle", result: {} },
  { id: "memory", label: "Retrieving memory", status: "idle", result: {} },
  { id: "policy", label: "Fetching policy", status: "idle", result: {} },
  { id: "tools", label: "Calling tools", status: "idle", result: {} },
  { id: "dag", label: "Validating policy DAG", status: "idle", result: {} },
  { id: "response", label: "Generating response", status: "idle", result: {} },
];

const stepIcons = {
  intent: Brain,
  memory: Database,
  policy: ShieldCheck,
  tools: Wrench,
  dag: GitBranch,
  response: MessageSquare,
};

function colorForRisk(risk: CustomerProfile["risk"]) {
  if (risk === "HIGH") return "#ef4444";
  if (risk === "MEDIUM") return "#f59e0b";
  return "#10b981";
}

function initialMessages(customer: CustomerProfile): ChatMessage[] {
  return [
    {
      role: "agent",
      content: `Loaded ${customer.name}'s account context. Pick a quick issue or type freely.`,
      timestamp: new Date().toISOString(),
    },
  ];
}

function resetSteps(): PipelineStep[] {
  return PIPELINE_TEMPLATE.map((step) => ({ ...step, status: "idle", result: {} }));
}

type LiveAgentConsoleProps = {
  title?: string;
  subtitle?: string;
};

export function LiveAgentConsole({
  title = "Live Agent Test Console",
  subtitle = "Isolated demo page for removable customer selection, chat, and reasoning playback.",
}: LiveAgentConsoleProps) {
  const [selected, setSelected] = useState(DEMO_CUSTOMERS[0]);
  const [messages, setMessages] = useState<ChatMessage[]>(() => initialMessages(DEMO_CUSTOMERS[0]));
  const [input, setInput] = useState("");
  const [steps, setSteps] = useState<PipelineStep[]>(resetSteps);
  const [status, setStatus] = useState<"READY" | "THINKING" | "RESOLVED">("READY");
  const [health, setHealth] = useState(72);
  const [relationship, setRelationship] = useState({ start: 29, end: 29 });
  const streamRef = useRef<EventSource | null>(null);

  const emptyConversation = messages.length === 1 && messages[0].role === "agent";
  const completedSteps = steps.filter((step) => step.status === "done").length;

  useEffect(() => {
    return () => streamRef.current?.close();
  }, []);

  function chooseCustomer(customer: CustomerProfile) {
    streamRef.current?.close();
    setSelected(customer);
    setMessages(initialMessages(customer));
    setInput("");
    setSteps(resetSteps());
    setStatus("READY");
    setHealth(customer.risk === "HIGH" ? 46 : customer.risk === "MEDIUM" ? 63 : 78);
    setRelationship({ start: customer.risk === "HIGH" ? 29 : customer.risk === "MEDIUM" ? 52 : 74, end: customer.risk === "HIGH" ? 29 : customer.risk === "MEDIUM" ? 52 : 74 });
  }

  function sendMessage(text = input) {
    const normalized = text.trim();
    if (!normalized || status === "THINKING") return;

    let currentToolResults: Record<string, unknown>[] = [];

    streamRef.current?.close();
    setInput("");
    setStatus("THINKING");
    setSteps(resetSteps());
    setMessages((items) => [
      ...items,
      { role: "customer", content: normalized, timestamp: new Date().toISOString() },
    ]);

    const params = new URLSearchParams({ customer_id: selected.customer_id, message: normalized });
    const stream = new EventSource(`/api/chat/message/stream?${params.toString()}`);
    streamRef.current = stream;

    stream.onmessage = (event) => {
      const data = JSON.parse(event.data) as {
        step: PipelineStepId;
        status: PipelineStatus;
        result: Record<string, unknown>;
      };

      setSteps((items) =>
        items.map((step) =>
          step.id === data.step ? { ...step, status: data.status, result: data.result ?? {} } : step
        )
      );

      if (data.step === "tools" && data.status === "done" && data.result.tools) {
        currentToolResults = data.result.tools as Record<string, unknown>[];
      }

      if (data.step === "response" && data.status === "done") {
        const text = typeof data.result.text === "string" ? data.result.text : "Done. I checked the account and prepared the next action.";
        setHealth(typeof data.result.health_score === "number" ? data.result.health_score : health);
        setRelationship({
          start: typeof data.result.relationship_start === "number" ? data.result.relationship_start : relationship.start,
          end: typeof data.result.relationship_end === "number" ? data.result.relationship_end : relationship.end,
        });
        setMessages((items) => [
          ...items,
          { role: "agent", content: text, timestamp: new Date().toISOString(), toolResults: currentToolResults },
        ]);
        setStatus("RESOLVED");
        stream.close();
      }
    };

    stream.onerror = () => {
      setStatus("READY");
      setMessages((items) => [
        ...items,
        {
          role: "agent",
          content: "The live chat stream could not complete. Check that FastAPI is running on port 8000.",
          timestamp: new Date().toISOString(),
        },
      ]);
      stream.close();
    };
  }

  const runningStep = useMemo(() => steps.find((step) => step.status === "running"), [steps]);

  return (
    <div className="p-4 max-w-[1800px] lg:p-6">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold gradient-text">{title}</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
            {subtitle}
          </p>
        </div>
        <div className="px-3 py-2 rounded-lg text-xs font-mono" style={{ background: "rgba(20,184,166,0.08)", border: "1px solid rgba(20,184,166,0.25)", color: "#5eead4" }}>
          {status} · {completedSteps}/6
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[280px_minmax(420px,1fr)_460px] xl:gap-5">
        <aside className="glass p-4 h-fit">
          <p className="text-xs font-semibold uppercase tracking-widest mb-3" style={{ color: "var(--text-muted)" }}>Customer Selector</p>
          <div className="space-y-3">
            {DEMO_CUSTOMERS.map((customer) => {
              const active = customer.customer_id === selected.customer_id;
              return (
                <button
                  key={customer.customer_id}
                  onClick={() => chooseCustomer(customer)}
                  className="w-full text-left p-3 rounded-lg transition-all"
                  style={{
                    background: active ? "rgba(99,102,241,0.16)" : "var(--surface-3)",
                    border: active ? "1px solid rgba(129,140,248,0.45)" : "1px solid var(--border)",
                  }}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{customer.name}</p>
                      <p className="text-[11px] font-mono mt-1" style={{ color: "var(--text-muted)" }}>{customer.customer_id}</p>
                    </div>
                    <span className="text-[10px] font-bold px-2 py-1 rounded-md" style={{ color: colorForRisk(customer.risk), background: `${colorForRisk(customer.risk)}1f`, border: `1px solid ${colorForRisk(customer.risk)}55` }}>
                      {customer.risk}
                    </span>
                  </div>
                  <p className="text-xs mt-3" style={{ color: "var(--text-secondary)" }}>{customer.plan}</p>
                  <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>{customer.hint}</p>
                </button>
              );
            })}
          </div>
        </aside>

        <section className="glass h-[620px] flex flex-col overflow-hidden xl:h-[720px]">
          <div className="px-5 py-4 border-b flex items-center gap-3" style={{ borderColor: "var(--border)" }}>
            <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ background: "rgba(20,184,166,0.12)", color: "#5eead4", border: "1px solid rgba(20,184,166,0.25)" }}>
              <User size={18} />
            </div>
            <div>
              <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{selected.name}</p>
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>{selected.plan} · {selected.hint}</p>
            </div>
          </div>

          <div className="flex-1 p-5 space-y-4 overflow-y-auto">
            {messages.map((message, index) => {
              const customer = message.role === "customer";
              return (
                <div key={`${message.timestamp}-${index}`} className={`flex ${customer ? "justify-end" : "justify-start"}`}>
                  <div className={`flex gap-2 max-w-[94%] sm:max-w-[82%] ${customer ? "flex-row-reverse" : ""}`}>
                    <span className="w-8 h-8 rounded-full flex items-center justify-center shrink-0" style={customer ? { background: "rgba(20,184,166,0.14)", color: "#5eead4" } : { background: "rgba(99,102,241,0.18)", color: "#a5b4fc" }}>
                      {customer ? <User size={14} /> : <Bot size={14} />}
                    </span>
                    <div className="rounded-xl px-3 py-2 text-sm leading-relaxed" style={customer ? { background: "rgba(20,184,166,0.15)", border: "1px solid rgba(20,184,166,0.26)", color: "var(--text-primary)" } : { background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                      {message.content}
                      {message.toolResults && message.toolResults.map((tool: Record<string, unknown>, idx: number) => {
                        if (tool.tool_name === "check_outage_status") return <OutageWidget key={idx} result={tool.result as Record<string, unknown>} />;
                        if (tool.tool_name === "get_invoice_history") return <InvoiceWidget key={idx} result={tool.result as Record<string, unknown>} />;
                        if (tool.tool_name === "apply_credit") return <CreditWidget key={idx} result={tool.result as Record<string, unknown>} />;
                        return null;
                      })}
                    </div>
                  </div>
                </div>
              );
            })}

            {status === "THINKING" && (
              <div className="flex items-center gap-2 text-sm" style={{ color: "var(--text-secondary)" }}>
                <Loader2 size={16} className="animate-spin" />
                {runningStep ? runningStep.label : "Working through the request"}
              </div>
            )}
          </div>

          <div className="border-t p-4" style={{ borderColor: "var(--border)" }}>
            {emptyConversation && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
                {ISSUE_CHIPS.map((chip) => {
                  const Icon = chip.icon;
                  return (
                    <button
                      key={chip.label}
                      onClick={() => sendMessage(chip.message)}
                      className="text-left px-3 py-2 rounded-lg flex items-center gap-2 transition-all hover:translate-y-[-1px]"
                      style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
                    >
                      <Icon size={15} style={{ color: "#5eead4" }} />
                      <span className="text-xs font-medium">{chip.label}</span>
                    </button>
                  );
                })}
              </div>
            )}
            <div className="flex gap-2">
              <input
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") sendMessage();
                }}
                disabled={status === "THINKING"}
                placeholder="Type a customer message..."
                className="flex-1 rounded-lg px-3 py-2 text-sm outline-none"
                style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
              <button
                onClick={() => sendMessage()}
                disabled={status === "THINKING"}
                className="px-4 py-2 rounded-lg flex items-center gap-2 text-sm font-semibold"
                style={{ background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.32)", color: "#5eead4" }}
              >
                <Send size={14} />
                Send
              </button>
            </div>
          </div>
        </section>

        <ReasoningPanel steps={steps} status={status} health={health} relationship={relationship} />
      </div>
    </div>
  );
}

export default function TestPage() {
  return <LiveAgentConsole />;
}

function ReasoningPanel({
  steps,
  status,
  health,
  relationship,
}: {
  steps: PipelineStep[];
  status: string;
  health: number;
  relationship: { start: number; end: number };
}) {
  return (
    <aside className="glass p-4 h-fit">
      <div className="flex items-center justify-between mb-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>Live Reasoning</p>
          <p className="text-sm mt-1" style={{ color: "var(--text-primary)" }}>{status}</p>
        </div>
        <div className="text-right">
          <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>Health</p>
          <p className="text-xl font-mono font-bold" style={{ color: health >= 70 ? "#10b981" : health >= 45 ? "#f59e0b" : "#ef4444" }}>{health}</p>
        </div>
      </div>

      <div className="mb-4 p-3 rounded-lg" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
        <div className="flex items-center justify-between text-xs mb-2">
          <span style={{ color: "var(--text-muted)" }}>Relationship</span>
          <span className="font-mono" style={{ color: "#5eead4" }}>{relationship.start} {"->"} {relationship.end}</span>
        </div>
        <div className="h-2 rounded-full overflow-hidden" style={{ background: "rgba(255,255,255,0.06)" }}>
          <div className="h-full rounded-full transition-all duration-700" style={{ width: `${relationship.end}%`, background: "linear-gradient(90deg, #f59e0b, #10b981)" }} />
        </div>
      </div>

      <div className="space-y-3">
        {steps.map((step) => (
          <PipelineStepCard key={step.id} step={step} />
        ))}
      </div>
    </aside>
  );
}

function PipelineStepCard({ step }: { step: PipelineStep }) {
  const Icon = stepIcons[step.id];
  const done = step.status === "done";
  const running = step.status === "running";

  return (
    <div className="rounded-lg p-3 transition-all" style={{ background: done || running ? "rgba(20,184,166,0.07)" : "var(--surface-3)", border: done ? "1px solid rgba(16,185,129,0.28)" : running ? "1px solid rgba(20,184,166,0.36)" : "1px solid var(--border)" }}>
      <div className="flex items-center gap-2">
        <span className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: running ? "rgba(20,184,166,0.16)" : "rgba(99,102,241,0.12)", color: running ? "#5eead4" : "#a5b4fc" }}>
          {running ? <Loader2 size={14} className="animate-spin" /> : done ? <CheckCircle2 size={14} /> : <Icon size={14} />}
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{step.label}</p>
          <p className="text-[11px]" style={{ color: done ? "#5eead4" : "var(--text-muted)" }}>{running ? "Running..." : done ? "Done" : "Waiting"}</p>
        </div>
      </div>
      {done && <StepResult step={step} />}
    </div>
  );
}

function StepResult({ step }: { step: PipelineStep }) {
  if (step.id === "intent") {
    const intents = arrayOfStrings(step.result.intents);
    const emotion = typeof step.result.emotion === "string" ? step.result.emotion : "neutral";
    const confidence = Number(step.result.confidence ?? 0);
    return <MiniList items={[`emotion: ${emotion}`, `confidence: ${confidence.toFixed(2)}`, ...intents.map((intent) => intent.replace(/_/g, " "))]} />;
  }
  if (step.id === "memory") {
    const stable = arrayOfStrings(step.result.stable);
    if (stable.length > 0 || Array.isArray(step.result.episodic)) {
      const episodic = Array.isArray(step.result.episodic) ? step.result.episodic.length : 0;
      return <MiniList items={[...stable.slice(0, 2), `${episodic} prior session(s) found`]} />;
    }
    return (
      <MiniList
        items={[
          String(step.result.name ?? step.result.customer_name ?? "customer loaded"),
          String(step.result.plan_name ?? step.result.plan ?? "account context"),
          String(step.result.location ?? step.result.risk_level ?? ""),
        ]}
      />
    );
  }
  if (step.id === "policy") {
    const policies = Array.isArray(step.result.policies) ? step.result.policies as Array<Record<string, unknown>> : [];
    return <MiniList items={policies.map((policy) => `${policy.policy_name} · ${Number(policy.confidence ?? 0).toFixed(2)}`)} />;
  }
  if (step.id === "tools") {
    const tools = Array.isArray(step.result.tools) ? step.result.tools as Array<Record<string, unknown>> : [];
    return <MiniList items={tools.map((tool) => `${tool.tool_name} · ${tool.summary ?? summarizeToolResult(tool.result)}`)} />;
  }
  if (step.id === "dag") {
    return <MiniList items={[String(step.result.dag_name ?? "policy DAG"), `UJCS ${Number(step.result.ujcs ?? 0).toFixed(2)} · ${String(step.result.policy_status ?? "pending").toUpperCase()}`, arrayOfStrings(step.result.path).join(" -> ")].filter(Boolean)} />;
  }
  return <MiniList items={[`Health updated`, `Empathy mode: ${String(step.result.empathy_mode ?? "STANDARD")}`, `Emotion: ${String(step.result.emotion ?? "neutral")}`, `Relationship ${String(step.result.relationship_start ?? "")} -> ${String(step.result.relationship_end ?? "")}`]} />;
}

function MiniList({ items }: { items: string[] }) {
  return (
    <div className="mt-3 space-y-1.5">
      {items.filter(Boolean).slice(0, 5).map((item, index) => (
        <div key={`${item}-${index}`} className="flex items-start gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
          <Circle size={6} className="mt-1.5 shrink-0" style={{ color: "#5eead4", fill: "#5eead4" }} />
          <span className="break-words">{item}</span>
        </div>
      ))}
    </div>
  );
}

function arrayOfStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item));
}

function summarizeToolResult(value: unknown): string {
  if (!value || typeof value !== "object") return "completed";
  const result = value as Record<string, unknown>;
  if (Array.isArray(result.invoices)) return `${result.invoices.length} invoices loaded`;
  if (result.duplicate_confirmed) return `duplicate found INR ${String(result.duplicate_amount ?? "")}`.trim();
  if (result.verified) return `verified outage ${String(result.duration_hours ?? "")} hrs`.trim();
  if (result.recommendation) return String(result.recommendation);
  if (result.mode === "already_taken") return "already taken";
  if (result.mode === "eligible") return "eligible";
  return "completed";
}
