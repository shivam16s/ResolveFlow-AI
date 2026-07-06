"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  Bot,
  Brain,
  CheckCircle2,
  Circle,
  Database,
  GitBranch,
  Loader2,
  Mic,
  MessageSquare,
  ReceiptText,
  Send,
  ShieldCheck,
  User,
  Volume2,
  WifiOff,
  Wrench,
  X,
} from "lucide-react";
import { OutageWidget, InvoiceWidget, CreditWidget, RetentionWidget } from "../../components/GenerativeUI";

type CustomerProfile = {
  customer_id: string;
  name: string;
  plan: string;
  risk: "HIGH" | "MEDIUM" | "LOW";
  hint: string;
  tone: "red" | "amber" | "green";
  preferredLanguage: string;
};

type HandoffInfo = {
  should_handoff?: boolean;
  reason?: string;
  severity?: string;
  customer_message?: string;
  context_card?: Record<string, unknown>;
};

type VerifiedClaim = { claim: string; tool: string; receipt_id?: string | null };

type TrustInfo = { score?: number; action?: string; issues?: string[]; threshold?: number };

type ChatMessage = {
  role: "customer" | "agent" | "human_agent";
  content: string;
  timestamp: string;
  agentName?: string;
  toolResults?: Record<string, unknown>[];
  handoff?: HandoffInfo | null;
  verifiedClaims?: VerifiedClaim[];
  trust?: TrustInfo | null;
  language?: string;
};

type SpeechRecognitionResultLike = {
  readonly length: number;
  item(index: number): { transcript: string };
  [index: number]: { transcript: string };
};

type SpeechRecognitionEventLike = {
  readonly results: {
    readonly length: number;
    item(index: number): SpeechRecognitionResultLike;
    [index: number]: SpeechRecognitionResultLike;
  };
};

type SpeechRecognitionLike = {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
  start: () => void;
};

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

type PipelineStepId = "intent" | "memory" | "policy" | "tools" | "dag" | "response";
type PipelineStatus = "idle" | "running" | "done";

type PipelineStep = {
  id: PipelineStepId;
  label: string;
  status: PipelineStatus;
  result: Record<string, unknown>;
  startedAt?: number;
  durationMs?: number;
};

const DEMO_CUSTOMERS: CustomerProfile[] = [
  {
    customer_id: "CUST-1001",
    name: "Rahul Sharma",
    plan: "Fiber Pro 200Mbps",
    risk: "HIGH",
    hint: "Duplicate charge + outage history",
    tone: "red",
    preferredLanguage: "en-IN",
  },
  {
    customer_id: "CUST-1002",
    name: "Ananya Iyer",
    plan: "Fiber Basic 50Mbps",
    risk: "MEDIUM",
    hint: "Plan change + refund pending",
    tone: "amber",
    preferredLanguage: "en-IN",
  },
  {
    customer_id: "CUST-1009",
    name: "Karthik Subramanian",
    plan: "Fiber Plus 100Mbps",
    risk: "LOW",
    hint: "Router diagnostic history",
    tone: "green",
    preferredLanguage: "en-IN",
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

const STREAM_WATCHDOG_MS = 30000;
const TAB_SESSION_STORAGE_KEY = "resolveflow.chat_session_id";

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

function createSessionId() {
  const randomPart =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `tab-${randomPart}`;
}

function getOrCreateTabSessionId() {
  if (typeof window === "undefined") return createSessionId();
  const stored = window.sessionStorage.getItem(TAB_SESSION_STORAGE_KEY);
  if (stored) return stored;
  const created = createSessionId();
  window.sessionStorage.setItem(TAB_SESSION_STORAGE_KEY, created);
  return created;
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
  const [isListening, setIsListening] = useState(false);
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);
  const [steps, setSteps] = useState<PipelineStep[]>(resetSteps);
  const [status, setStatus] = useState<"READY" | "THINKING" | "RESOLVED">("READY");
  const [resetting, setResetting] = useState(false);
  const [health, setHealth] = useState(72);
  const [relationship, setRelationship] = useState({ start: 29, end: 29 });
  const streamRef = useRef<EventSource | null>(null);
  const sessionIdRef = useRef("");
  const humanReplyKeysRef = useRef<Set<string>>(new Set());
  const streamWatchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const emptyConversation = messages.length === 1 && messages[0].role === "agent";
  const completedSteps = steps.filter((step) => step.status === "done").length;

  // Without this, a newly sent message (or the agent's reply) can render below
  // the visible viewport with no indication anything happened -- every real
  // chat UI auto-scrolls to the newest message.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  function clearStreamWatchdog() {
    if (streamWatchdogRef.current === null) return;
    clearTimeout(streamWatchdogRef.current);
    streamWatchdogRef.current = null;
  }

  function closeStream(stream: EventSource) {
    clearStreamWatchdog();
    stream.close();
    if (streamRef.current === stream) streamRef.current = null;
  }

  function markStreamStopped(stream: EventSource, content: string, stepError: string) {
    setStatus("READY");
    setSteps((items) =>
      items.map((step) =>
        step.status === "running"
          ? { ...step, status: "idle", result: { error: stepError } }
          : step
      )
    );
    setMessages((items) => [
      ...items,
      {
        role: "agent",
        content,
        timestamp: new Date().toISOString(),
      },
    ]);
    closeStream(stream);
  }

  function armStreamWatchdog(stream: EventSource) {
    clearStreamWatchdog();
    streamWatchdogRef.current = setTimeout(() => {
      if (streamRef.current !== stream) return;
      markStreamStopped(
        stream,
        "The live chat stream stopped responding, so I closed it before the UI could get stuck. Please try sending the message again.",
        "Stream timed out"
      );
    }, STREAM_WATCHDOG_MS);
  }

  const speakReply = useCallback((text: string) => {
    if (!ttsEnabled || typeof window === "undefined" || !("speechSynthesis" in window)) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = selected.preferredLanguage;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
  }, [selected.preferredLanguage, ttsEnabled]);

  useEffect(() => {
    sessionIdRef.current = getOrCreateTabSessionId();
    return () => {
      clearStreamWatchdog();
      streamRef.current?.close();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function pollHumanReplies() {
      const activeSessionId = sessionIdRef.current || getOrCreateTabSessionId();
      sessionIdRef.current = activeSessionId;
      try {
        const params = new URLSearchParams({
          customer_id: selected.customer_id,
          session_id: activeSessionId,
        });
        const response = await fetch(`/api/chat/session/messages?${params.toString()}`);
        if (!response.ok || cancelled) return;
        const payload = await response.json() as { messages?: Array<Record<string, unknown>> };
        const externalMessages = (payload.messages ?? []).filter(
          (item) => item.role === "human_agent" || item.proactive === true
        );
        const additions: ChatMessage[] = [];
        for (const item of externalMessages) {
          const content = typeof item.content === "string" ? item.content : "";
          const timestamp = typeof item.timestamp === "string" ? item.timestamp : new Date().toISOString();
          const key = `${timestamp}:${content}`;
          if (!content || humanReplyKeysRef.current.has(key)) continue;
          humanReplyKeysRef.current.add(key);
          const isHuman = item.role === "human_agent";
          additions.push({
            role: isHuman ? "human_agent" : "agent",
            content,
            timestamp,
            agentName: isHuman && typeof item.agent_name === "string" ? item.agent_name : undefined,
          });
        }
        if (additions.length) {
          setMessages((items) => [...items, ...additions]);
          speakReply(additions[additions.length - 1].content);
        }
      } catch {
        return;
      }
    }
    const interval = window.setInterval(() => {
      void pollHumanReplies();
    }, 5000);
    void pollHumanReplies();
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [selected.customer_id, speakReply]);

  function chooseCustomer(customer: CustomerProfile) {
    if (streamRef.current) closeStream(streamRef.current);
    setSelected(customer);
    setMessages(initialMessages(customer));
    humanReplyKeysRef.current.clear();
    setInput("");
    setSteps(resetSteps());
    setStatus("READY");
    setHealth(customer.risk === "HIGH" ? 46 : customer.risk === "MEDIUM" ? 63 : 78);
    setRelationship({ start: customer.risk === "HIGH" ? 29 : customer.risk === "MEDIUM" ? 52 : 74, end: customer.risk === "HIGH" ? 29 : customer.risk === "MEDIUM" ? 52 : 74 });
  }

  function startVoiceInput() {
    if (typeof window === "undefined" || status === "THINKING" || isListening) return;
    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Recognition) {
      setVoiceNotice("Voice input is not supported in this browser. You can keep typing normally.");
      return;
    }
    setVoiceNotice(null);
    const recognition = new Recognition();
    recognition.lang = "en-IN";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event) => {
      const lastResult = event.results[event.results.length - 1];
      const transcript = lastResult?.[0]?.transcript?.trim();
      if (transcript) setInput(transcript);
    };
    recognition.onend = () => setIsListening(false);
    recognition.onerror = () => setIsListening(false);
    setIsListening(true);
    recognition.start();
  }

  function toggleTts() {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setVoiceNotice("Spoken replies are not supported in this browser.");
      setTtsEnabled(false);
      return;
    }
    setVoiceNotice(null);
    setTtsEnabled((enabled) => !enabled);
  }

  async function resetDemoData() {
    if (status === "THINKING" || resetting) return;
    setResetting(true);
    try {
      const response = await fetch("/api/demo/reset", { method: "POST" });
      if (!response.ok) throw new Error(`Reset failed with ${response.status}`);
      const freshSessionId = createSessionId();
      window.sessionStorage.setItem(TAB_SESSION_STORAGE_KEY, freshSessionId);
      sessionIdRef.current = freshSessionId;
      setMessages(initialMessages(selected));
      setInput("");
      setSteps(resetSteps());
      setStatus("READY");
      setHealth(selected.risk === "HIGH" ? 46 : selected.risk === "MEDIUM" ? 63 : 78);
      setRelationship({
        start: selected.risk === "HIGH" ? 29 : selected.risk === "MEDIUM" ? 52 : 74,
        end: selected.risk === "HIGH" ? 29 : selected.risk === "MEDIUM" ? 52 : 74,
      });
    } catch (error) {
      console.error("Demo reset failed", error);
      setMessages((items) => [
        ...items,
        {
          role: "agent",
          content: "I could not reset the demo data. Check that FastAPI is running, then try again.",
          timestamp: new Date().toISOString(),
        },
      ]);
    } finally {
      setResetting(false);
    }
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

    const activeSessionId = sessionIdRef.current || getOrCreateTabSessionId();
    sessionIdRef.current = activeSessionId;
    const params = new URLSearchParams({
      customer_id: selected.customer_id,
      session_id: activeSessionId,
      message: normalized,
    });
    const stream = new EventSource(`/api/chat/message/stream?${params.toString()}`);
    streamRef.current = stream;
    armStreamWatchdog(stream);

    stream.onmessage = (event) => {
      armStreamWatchdog(stream);
      if (!event.data.trim()) return;

      let data: {
        step: PipelineStepId;
        status: PipelineStatus;
        result: Record<string, unknown>;
      };
      try {
        data = JSON.parse(event.data) as {
          step: PipelineStepId;
          status: PipelineStatus;
          result: Record<string, unknown>;
        };
      } catch (error) {
        console.error("Malformed chat stream frame", { frame: event.data, error });
        markStreamStopped(
          stream,
          "The live chat stream returned a malformed update, so I stopped the run before it could get stuck. Please try sending the message again.",
          "Malformed stream frame"
        );
        return;
      }

      setSteps((items) =>
        items.map((step) =>
          step.id === data.step
            ? {
                ...step,
                status: data.status,
                result: data.result ?? {},
                startedAt: data.status === "running" ? Date.now() : step.startedAt,
                durationMs: data.status === "done" && step.startedAt ? Date.now() - step.startedAt : step.durationMs,
              }
            : step
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
        const handoff = (data.result.handoff as HandoffInfo | null) ?? null;
        const verifiedClaims = Array.isArray(data.result.verified_claims)
          ? (data.result.verified_claims as VerifiedClaim[])
          : [];
        const trust = (data.result.trust as TrustInfo | null) ?? null;
        const language = typeof data.result.language === "string" ? data.result.language : undefined;
        setMessages((items) => [
          ...items,
          { role: "agent", content: text, timestamp: new Date().toISOString(), toolResults: currentToolResults, handoff, verifiedClaims, trust, language },
        ]);
        speakReply(text);
        setStatus("RESOLVED");
        closeStream(stream);
      }
    };

    stream.onerror = () => {
      markStreamStopped(
        stream,
        "The live chat stream could not complete. Check that FastAPI is running on port 8000.",
        "Stream connection failed"
      );
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
        <div className="flex items-center gap-2">
          <button
            onClick={resetDemoData}
            disabled={status === "THINKING" || resetting}
            className="px-3 py-2 rounded-lg text-xs font-semibold transition-opacity disabled:opacity-50"
            style={{ background: "rgba(255,255,255,0.06)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
          >
            {resetting ? "Resetting..." : "Reset demo data"}
          </button>
          <div className="px-3 py-2 rounded-lg text-xs font-mono" style={{ background: "rgba(20,184,166,0.08)", border: "1px solid rgba(20,184,166,0.25)", color: "#5eead4" }}>
            {status} · {completedSteps}/6
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[280px_minmax(420px,1fr)_460px] xl:gap-5 xl:h-[calc(100vh-180px)] xl:items-stretch xl:min-h-0">
        <aside className="glass p-4 h-fit xl:h-full xl:min-h-0 xl:overflow-y-auto">
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

        <section className="glass h-[620px] flex flex-col overflow-hidden xl:h-full xl:min-h-0">
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
              const human = message.role === "human_agent";
              return (
                <div key={`${message.timestamp}-${index}`} className={`flex ${customer ? "justify-end" : "justify-start"}`}>
                  <div className={`flex gap-2 max-w-[94%] sm:max-w-[82%] ${customer ? "flex-row-reverse" : ""}`}>
                    <span className="w-8 h-8 rounded-full flex items-center justify-center shrink-0" style={customer ? { background: "rgba(20,184,166,0.14)", color: "#5eead4" } : { background: "rgba(99,102,241,0.18)", color: "#a5b4fc" }}>
                      {customer ? <User size={14} /> : <Bot size={14} />}
                    </span>
                    <div className="rounded-xl px-3 py-2 text-sm leading-relaxed" style={customer ? { background: "rgba(20,184,166,0.15)", border: "1px solid rgba(20,184,166,0.26)", color: "var(--text-primary)" } : human ? { background: "rgba(168,85,247,0.12)", border: "1px solid rgba(168,85,247,0.35)", color: "var(--text-primary)" } : { background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)" }}>
                      {human && (
                        <p className="mb-1 text-[10px] font-semibold uppercase tracking-widest" style={{ color: "#c4b5fd" }}>
                          {message.agentName ?? "Human specialist"}
                        </p>
                      )}
                      {message.role === "agent" && message.language && message.language !== "English" && (
                        <div
                          className="mb-1.5 inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold"
                          style={{ background: "rgba(94,234,212,0.10)", border: "1px solid rgba(94,234,212,0.35)", color: "#5eead4" }}
                          title={`Replied in ${message.language}, the customer's preferred language`}
                        >
                          Replied in {message.language}
                        </div>
                      )}
                      {message.content}
                      {message.toolResults && message.toolResults.map((tool: Record<string, unknown>, idx: number) => {
                        if (tool.tool_name === "check_outage_status") return <OutageWidget key={idx} result={tool.result as Record<string, unknown>} />;
                        if (tool.tool_name === "get_invoice_history") return <InvoiceWidget key={idx} result={tool.result as Record<string, unknown>} />;
                        if (tool.tool_name === "apply_credit_guard") return <CreditWidget key={idx} result={tool.result as Record<string, unknown>} />;
                        if (tool.tool_name === "build_retention_offer") return <RetentionWidget key={idx} result={tool.result as Record<string, unknown>} />;
                        return null;
                      })}
                      {message.role === "agent" && message.verifiedClaims && message.verifiedClaims.length > 0 && (
                        <VerifiedEvidence claims={message.verifiedClaims} trust={message.trust} />
                      )}
                      {message.handoff && <HandoffBanner handoff={message.handoff} />}
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
            <div ref={messagesEndRef} />
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
            <div className="flex flex-wrap gap-2">
              <input
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
                  event.preventDefault();
                  sendMessage();
                }}
                disabled={status === "THINKING"}
                placeholder="Type a customer message..."
                className="flex-1 min-w-[140px] rounded-lg px-3 py-2 text-sm outline-none"
                style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
              <button
                onClick={startVoiceInput}
                disabled={status === "THINKING" || isListening}
                title="Use voice input"
                className="px-3 py-2 rounded-lg flex items-center gap-2 text-sm font-semibold disabled:opacity-50"
                style={{ background: isListening ? "rgba(245,158,11,0.14)" : "rgba(255,255,255,0.04)", border: "1px solid var(--border)", color: isListening ? "#fbbf24" : "#5eead4" }}
              >
                <Mic size={14} />
                {isListening ? "Listening" : "Mic"}
              </button>
              <button
                onClick={toggleTts}
                title="Toggle spoken replies"
                className="px-3 py-2 rounded-lg flex items-center gap-2 text-sm font-semibold"
                style={{ background: ttsEnabled ? "rgba(20,184,166,0.12)" : "rgba(255,255,255,0.04)", border: "1px solid var(--border)", color: ttsEnabled ? "#5eead4" : "var(--text-secondary)" }}
              >
                <Volume2 size={14} />
                Voice
              </button>
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
            {voiceNotice && (
              <p className="mt-2 rounded-lg px-3 py-2 text-xs" style={{ background: "rgba(245,158,11,0.1)", border: "1px solid rgba(245,158,11,0.24)", color: "#fbbf24" }}>
                {voiceNotice}
              </p>
            )}
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
  const [expanded, setExpanded] = useState(true);
  return (
    <aside className="glass p-4 h-fit xl:h-full xl:min-h-0 xl:overflow-y-auto">
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

      <div className="mb-4 rounded-lg p-3" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
        <div className="mb-3 flex items-center justify-between gap-3">
          <p className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>Pipeline</p>
          <button
            onClick={() => setExpanded((value) => !value)}
            className="rounded-md px-2 py-1 text-[11px] font-semibold"
            style={{ background: "rgba(255,255,255,0.04)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
          >
            {expanded ? "Collapse" : "Expand"}
          </button>
        </div>
        <PipelineRail steps={steps} />
      </div>

      {expanded && (
        <div className="space-y-3">
          {steps.map((step) => (
            <PipelineStepCard key={step.id} step={step} />
          ))}
        </div>
      )}
    </aside>
  );
}

function PipelineRail({ steps }: { steps: PipelineStep[] }) {
  return (
    <div className="grid grid-cols-6 gap-2">
      {steps.map((step) => {
        const done = step.status === "done";
        const running = step.status === "running";
        return (
          <div key={step.id} className="min-w-0">
            <div
              className="h-2 rounded-full transition-all duration-500"
              style={{
                background: done ? "#10b981" : running ? "#5eead4" : "rgba(255,255,255,0.08)",
                boxShadow: running ? "0 0 14px rgba(94,234,212,0.45)" : "none",
              }}
            />
            <p className="mt-2 truncate text-[10px] font-semibold" style={{ color: done || running ? "#5eead4" : "var(--text-muted)" }}>
              {step.id}
            </p>
            <p className="font-mono text-[10px]" style={{ color: "var(--text-muted)" }}>
              {step.durationMs ? `${step.durationMs}ms` : running ? "..." : "--"}
            </p>
          </div>
        );
      })}
    </div>
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
        {step.durationMs && (
          <span className="ml-auto font-mono text-[11px]" style={{ color: "var(--text-muted)" }}>
            {step.durationMs}ms
          </span>
        )}
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
  const trust = (step.result.trust ?? {}) as { score?: number; action?: string };
  const trustLine = typeof trust.score === "number" ? `Trust ${trust.score.toFixed(2)} · ${String(trust.action ?? "proceed")}` : "";
  return <MiniList items={[trustLine, `Empathy mode: ${String(step.result.empathy_mode ?? "STANDARD")}`, `Emotion: ${String(step.result.emotion ?? "neutral")}`, `Relationship ${String(step.result.relationship_start ?? "")} -> ${String(step.result.relationship_end ?? "")}`]} />;
}

function VerifiedEvidence({ claims, trust }: { claims: VerifiedClaim[]; trust?: TrustInfo | null }) {
  const [open, setOpen] = useState(false);
  const score = typeof trust?.score === "number" ? trust.score : null;
  const action = trust?.action ?? "";
  const trustColor = score == null ? "#5eead4" : score >= 0.8 ? "#10b981" : score >= 0.6 ? "#f59e0b" : "#ef4444";
  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 px-2 py-1 rounded-md text-[11px] font-semibold transition-all"
          style={{ background: "rgba(16,185,129,0.10)", border: "1px solid rgba(16,185,129,0.35)", color: "#10b981" }}
        >
          <ShieldCheck size={13} />
          Verified · {claims.length} evidence {claims.length === 1 ? "receipt" : "receipts"}
        </button>
        {score != null && (
          <span
            className="px-2 py-1 rounded-md text-[11px] font-mono font-semibold"
            style={{ background: `${trustColor}1a`, border: `1px solid ${trustColor}55`, color: trustColor }}
            title={(trust?.issues && trust.issues.length ? trust.issues.join("; ") : "no trust issues") + (trust?.threshold ? ` (threshold ${trust.threshold})` : "")}
          >
            Trust {score.toFixed(2)}{action ? ` · ${action}` : ""}
          </span>
        )}
      </div>
      {open && (
        <div className="mt-2 space-y-1.5">
          {claims.map((c, i) => (
            <div key={i} className="flex items-start gap-2 text-[11px] p-2 rounded-md" style={{ background: "var(--surface-1)", border: "1px solid var(--border)" }}>
              <ShieldCheck size={12} className="mt-0.5 shrink-0" style={{ color: "#10b981" }} />
              <div className="min-w-0">
                <p style={{ color: "var(--text-primary)" }}>{c.claim}</p>
                <p className="font-mono mt-0.5" style={{ color: "var(--text-muted)" }}>
                  {c.tool} · {c.receipt_id ?? "no-receipt"}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function HandoffBanner({ handoff }: { handoff: HandoffInfo }) {
  if (!handoff || !handoff.should_handoff) return null;
  const card = handoff.context_card ?? {};
  return (
    <div className="mt-3 p-3 rounded-xl border" style={{ background: "rgba(168, 85, 247, 0.07)", borderColor: "rgba(168, 85, 247, 0.35)" }}>
      <div className="flex items-center gap-2 mb-1">
        <ArrowUpRight size={15} style={{ color: "#c084fc" }} />
        <h4 className="font-bold text-xs uppercase tracking-wider" style={{ color: "#c084fc" }}>Escalating to human specialist</h4>
      </div>
      {handoff.customer_message && (
        <p className="text-sm" style={{ color: "var(--text-primary)" }}>{handoff.customer_message}</p>
      )}
      <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
        {handoff.reason && (
          <span className="px-2 py-0.5 rounded-md" style={{ background: "var(--surface-1)", border: "1px solid var(--border)", color: "var(--text-muted)" }}>
            Reason: {handoff.reason}
          </span>
        )}
        {typeof card.health_score !== "undefined" && (
          <span className="px-2 py-0.5 rounded-md" style={{ background: "var(--surface-1)", border: "1px solid var(--border)", color: "var(--text-muted)" }}>
            Health {String(card.health_score)}
          </span>
        )}
        {Array.isArray(card.issues) && card.issues.length > 0 && (
          <span className="px-2 py-0.5 rounded-md" style={{ background: "var(--surface-1)", border: "1px solid var(--border)", color: "var(--text-muted)" }}>
            Context card: {(card.issues as string[]).join(", ")}
          </span>
        )}
      </div>
    </div>
  );
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
