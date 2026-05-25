"use client";

import { useState, useMemo } from "react";
import useSWR from "swr";
import { motion, AnimatePresence } from "framer-motion";
import { Search, Brain, FileText, Network, CheckCircle2, XCircle, AlertCircle, RefreshCw } from "lucide-react";
import { PageHeader, GlassPanel, SectionLabel } from "@/components/BlueprintPrimitives";
import { api } from "@/lib/api";
import type { MemorySearchResult, PolicyRetrievalResult, MemoryGraphData } from "@/lib/types";
import { truncate } from "@/lib/utils";

type Tab = "memory" | "policy" | "graph";

export default function KnowledgeExplorerPage() {
  const [activeTab, setActiveTab] = useState<Tab>("memory");

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <PageHeader
        eyebrow="Trust Layer"
        title="Knowledge Explorer"
        subtitle="Explore the RAG infrastructure underlying the agent. Search episodic memories, query the NetworkX PPR graph, and trace Policy CRAG evaluation paths in real-time."
      />

      <div className="flex items-center gap-2 mb-6 p-1 rounded-xl w-fit" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
        <TabButton id="memory" active={activeTab} icon={<Brain size={16} />} label="Memory Search" onClick={setActiveTab} />
        <TabButton id="policy" active={activeTab} icon={<FileText size={16} />} label="Policy Retrieval" onClick={setActiveTab} />
        <TabButton id="graph" active={activeTab} icon={<Network size={16} />} label="Memory Graph" onClick={setActiveTab} />
      </div>

      <AnimatePresence mode="wait">
        {activeTab === "memory" && <MemorySearchTab key="memory" />}
        {activeTab === "policy" && <PolicyRetrievalTab key="policy" />}
        {activeTab === "graph" && <MemoryGraphTab key="graph" />}
      </AnimatePresence>
    </div>
  );
}

function TabButton({ id, active, icon, label, onClick }: { id: Tab; active: Tab; icon: React.ReactNode; label: string; onClick: (id: Tab) => void }) {
  const isActive = id === active;
  return (
    <button
      onClick={() => onClick(id)}
      className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all relative overflow-hidden"
      style={{ color: isActive ? "#5eead4" : "var(--text-muted)" }}
    >
      {isActive && (
        <motion.div
          layoutId="rag-tab-indicator"
          className="absolute inset-0 z-0"
          style={{ background: "rgba(20,184,166,0.12)", border: "1px solid rgba(20,184,166,0.25)", borderRadius: "0.5rem" }}
        />
      )}
      <span className="relative z-10 flex items-center gap-2">
        {icon}
        {label}
      </span>
    </button>
  );
}

// ── Tab 1: Memory Search ──────────────────────────────────────────────────

function MemorySearchTab() {
  const [query, setQuery] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [isSearching, setIsSearching] = useState(false);
  const [results, setResults] = useState<MemorySearchResult[]>([]);

  const { data: customersData } = useSWR("rag-customers", () => api.rag.customers());
  const customers = customersData?.customers ?? [];

  async function handleSearch() {
    if (!query.trim() || !customerId) return;
    setIsSearching(true);
    try {
      const res = await api.rag.memorySearch(customerId, query, 5);
      setResults(res.results);
    } catch (err) {
      console.error(err);
    } finally {
      setIsSearching(false);
    }
  }

  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} className="space-y-6">
      <GlassPanel className="p-5 flex flex-col md:flex-row gap-4">
        <div className="flex-1">
          <label className="block text-xs font-semibold mb-2" style={{ color: "var(--text-muted)" }}>CUSTOMER</label>
          <select
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
            className="w-full px-3 py-2 text-sm rounded-lg outline-none transition-all focus:ring-1"
            style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)", "--tw-ring-color": "var(--accent)" } as React.CSSProperties}
          >
            <option value="">Select a customer...</option>
            {customers.map(c => (
              <option key={c.customer_id} value={c.customer_id}>{c.name} ({c.customer_id})</option>
            ))}
          </select>
        </div>
        <div className="flex-[2]">
          <label className="block text-xs font-semibold mb-2" style={{ color: "var(--text-muted)" }}>QUERY (HYBRID RRF)</label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                placeholder="Search vector embeddings + NetworkX PPR graph..."
                className="w-full pl-9 pr-3 py-2 text-sm rounded-lg outline-none transition-all focus:ring-1"
                style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)", "--tw-ring-color": "var(--accent)" } as React.CSSProperties}
              />
            </div>
            <button
              onClick={handleSearch}
              disabled={isSearching || !query || !customerId}
              className="px-4 py-2 text-sm font-semibold rounded-lg transition-all disabled:opacity-50 flex items-center gap-2"
              style={{ background: "var(--accent)", color: "#000" }}
            >
              {isSearching ? <RefreshCw size={14} className="animate-spin" /> : "Search"}
            </button>
          </div>
        </div>
      </GlassPanel>

      <div className="grid gap-4">
        {results.map((result, i) => (
          <GlassPanel key={result.memory_id} className="p-5 flex flex-col gap-3">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-medium leading-relaxed" style={{ color: "var(--text-primary)" }}>{result.document}</p>
                <div className="flex flex-wrap gap-2 mt-3">
                  {result.sources.map(src => (
                    <span key={src} className="px-2 py-0.5 text-[10px] uppercase font-bold rounded" style={{ background: src === "vector" ? "rgba(99,102,241,0.15)" : "rgba(245,158,11,0.15)", color: src === "vector" ? "#818cf8" : "#fbbf24", border: `1px solid ${src === "vector" ? "rgba(99,102,241,0.3)" : "rgba(245,158,11,0.3)"}` }}>
                      {src} Rank: {src === "vector" ? result.vector_rank : result.graph_rank}
                    </span>
                  ))}
                  <span className="px-2 py-0.5 text-[10px] rounded font-mono" style={{ background: "var(--surface-3)", color: "var(--text-muted)", border: "1px solid var(--border)" }}>
                    Type: {result.metadata?.memory_type as string || "unknown"}
                  </span>
                </div>
              </div>
              <div className="flex flex-col items-end gap-1 text-right min-w-24">
                <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>Fused Score</span>
                <span className="text-xl font-mono font-light gradient-text">{result.fused_score.toFixed(4)}</span>
                <div className="w-full h-1 mt-1 rounded-full bg-black/40 overflow-hidden">
                  <div className="h-full bg-teal-400" style={{ width: `${Math.min(100, result.fused_score * 1000)}%` }} />
                </div>
              </div>
            </div>
          </GlassPanel>
        ))}
      </div>
    </motion.div>
  );
}

// ── Tab 2: Policy Retrieval ───────────────────────────────────────────────

const POLICIES = [
  "cancellation_policy",
  "duplicate_charge_policy",
  "escalation_policy",
  "payment_failure_policy",
  "plan_change_policy",
  "refund_policy",
  "service_credit_policy",
  "technician_visit_policy",
];

function PolicyRetrievalTab() {
  const [query, setQuery] = useState("");
  const [policyName, setPolicyName] = useState(POLICIES[0]);
  const [isRetrieving, setIsRetrieving] = useState(false);
  const [results, setResults] = useState<PolicyRetrievalResult[]>([]);

  async function handleRetrieve() {
    if (!query.trim()) return;
    setIsRetrieving(true);
    try {
      const res = await api.rag.policyRetrieve(query, policyName, 3);
      setResults(res.results);
    } catch (err) {
      console.error(err);
    } finally {
      setIsRetrieving(false);
    }
  }

  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} className="space-y-6">
      <GlassPanel className="p-5 flex flex-col md:flex-row gap-4">
        <div className="flex-1">
          <label className="block text-xs font-semibold mb-2" style={{ color: "var(--text-muted)" }}>POLICY SCOPE</label>
          <select
            value={policyName}
            onChange={(e) => setPolicyName(e.target.value)}
            className="w-full px-3 py-2 text-sm rounded-lg outline-none transition-all focus:ring-1"
            style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)", "--tw-ring-color": "var(--accent)" } as React.CSSProperties}
          >
            {POLICIES.map(p => (
              <option key={p} value={p}>{p.replace("_policy", "").replace(/_/g, " ")}</option>
            ))}
          </select>
        </div>
        <div className="flex-[2]">
          <label className="block text-xs font-semibold mb-2" style={{ color: "var(--text-muted)" }}>QUERY (SELF-RAG + CRAG)</label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleRetrieve()}
                placeholder="Search policy instructions..."
                className="w-full pl-9 pr-3 py-2 text-sm rounded-lg outline-none transition-all focus:ring-1"
                style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)", "--tw-ring-color": "var(--accent)" } as React.CSSProperties}
              />
            </div>
            <button
              onClick={handleRetrieve}
              disabled={isRetrieving || !query}
              className="px-4 py-2 text-sm font-semibold rounded-lg transition-all disabled:opacity-50 flex items-center gap-2"
              style={{ background: "var(--accent)", color: "#000" }}
            >
              {isRetrieving ? <RefreshCw size={14} className="animate-spin" /> : "Evaluate"}
            </button>
          </div>
        </div>
      </GlassPanel>

      <div className="grid gap-4">
        {results.map((result, i) => (
          <GlassPanel key={i} className="p-5 flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                {result.crag_path === "CORRECT" && <CheckCircle2 size={18} style={{ color: "#34d399" }} />}
                {result.crag_path === "INCORRECT" && <XCircle size={18} style={{ color: "#f87171" }} />}
                {result.crag_path === "AMBIGUOUS" && <AlertCircle size={18} style={{ color: "#fbbf24" }} />}
                <span className="text-sm font-bold tracking-widest uppercase" style={{ color: result.crag_path === "CORRECT" ? "#34d399" : result.crag_path === "INCORRECT" ? "#f87171" : "#fbbf24" }}>
                  {result.crag_path}
                </span>
              </div>
              <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>Confidence: {result.confidence}</span>
            </div>
            
            {result.rewritten_query && (
              <div className="p-3 rounded-md mt-2 text-xs font-mono" style={{ background: "rgba(248,113,113,0.1)", border: "1px dashed rgba(248,113,113,0.3)", color: "#fca5a5" }}>
                <strong>CRAG Rewritten Query:</strong> {result.rewritten_query}
              </div>
            )}
            
            <p className="text-sm leading-relaxed mt-2 p-4 rounded-md" style={{ background: "var(--surface-1)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>
              {result.chunk}
            </p>
          </GlassPanel>
        ))}
      </div>
    </motion.div>
  );
}

// ── Tab 3: Memory Graph ───────────────────────────────────────────────────

function MemoryGraphTab() {
  const [customerId, setCustomerId] = useState("");
  const { data: customersData } = useSWR("rag-customers", () => api.rag.customers());
  const customers = customersData?.customers ?? [];

  const { data: graphData, isLoading } = useSWR<MemoryGraphData>(
    customerId ? `rag-graph-${customerId}` : null,
    () => api.rag.memoryGraph(customerId)
  );

  const nodes = graphData?.nodes ?? [];
  const edges = graphData?.edges ?? [];

  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} className="space-y-6">
      <GlassPanel className="p-5 flex items-center justify-between">
        <div className="flex-1 max-w-sm">
          <label className="block text-xs font-semibold mb-2" style={{ color: "var(--text-muted)" }}>CUSTOMER GRAPH</label>
          <select
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
            className="w-full px-3 py-2 text-sm rounded-lg outline-none transition-all focus:ring-1"
            style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-primary)", "--tw-ring-color": "var(--accent)" } as React.CSSProperties}
          >
            <option value="">Select a customer...</option>
            {customers.map(c => (
              <option key={c.customer_id} value={c.customer_id}>{c.name} ({c.customer_id})</option>
            ))}
          </select>
        </div>
        <div className="text-right">
          <p className="text-2xl font-light gradient-text">{nodes.length}</p>
          <p className="text-[10px] uppercase font-bold tracking-widest mt-1" style={{ color: "var(--text-muted)" }}>Nodes Extracted</p>
        </div>
        <div className="text-right">
          <p className="text-2xl font-light gradient-text">{edges.length}</p>
          <p className="text-[10px] uppercase font-bold tracking-widest mt-1" style={{ color: "var(--text-muted)" }}>Edges Formed</p>
        </div>
      </GlassPanel>

      {isLoading && (
        <div className="flex items-center justify-center p-12">
          <RefreshCw className="animate-spin" style={{ color: "var(--accent)" }} />
        </div>
      )}

      {customerId && !isLoading && nodes.length === 0 && (
        <div className="p-12 text-center text-sm" style={{ color: "var(--text-muted)" }}>
          No graph data found. Make sure this customer's sessions have been indexed via MemoryManager.
        </div>
      )}

      {nodes.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <GlassPanel className="p-0 overflow-hidden flex flex-col h-[500px]">
            <div className="p-4 border-b" style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}>
              <SectionLabel>OpenIE Entities (Nodes)</SectionLabel>
            </div>
            <div className="overflow-y-auto p-4 space-y-2 flex-1">
              {nodes.map(node => (
                <div key={node.node_id} className="p-3 rounded-lg flex items-center justify-between" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center gap-3">
                    <div className="w-2 h-2 rounded-full" style={{ background: getNodeColor(node.node_type) }} />
                    <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{node.label}</span>
                  </div>
                  <span className="text-xs font-mono px-2 py-1 rounded" style={{ background: "var(--surface-1)", color: "var(--text-secondary)" }}>{node.node_type}</span>
                </div>
              ))}
            </div>
          </GlassPanel>
          <GlassPanel className="p-0 overflow-hidden flex flex-col h-[500px]">
            <div className="p-4 border-b" style={{ borderColor: "var(--border)", background: "var(--surface-2)" }}>
              <SectionLabel>Extracted Relations (Edges)</SectionLabel>
            </div>
            <div className="overflow-y-auto p-4 space-y-2 flex-1">
              {edges.map((edge, i) => (
                <div key={i} className="p-3 rounded-lg flex flex-col gap-2" style={{ background: "var(--surface-3)", border: "1px solid var(--border)" }}>
                  <div className="flex items-center justify-between text-xs font-mono">
                    <span style={{ color: "var(--text-primary)" }}>{truncate(edge.source, 20)}</span>
                    <span className="px-2 py-0.5 rounded text-[10px] uppercase font-bold" style={{ background: "rgba(94,234,212,0.1)", color: "#5eead4" }}>
                      {edge.relation}
                    </span>
                    <span style={{ color: "var(--text-primary)" }}>{truncate(edge.target, 20)}</span>
                  </div>
                </div>
              ))}
            </div>
          </GlassPanel>
        </div>
      )}
    </motion.div>
  );
}

function getNodeColor(type: string) {
  switch (type.toLowerCase()) {
    case 'customer': return '#38bdf8'; // light blue
    case 'outage': return '#fbbf24'; // amber
    case 'payment': return '#34d399'; // emerald
    case 'invoice': return '#a78bfa'; // violet
    default: return '#94a3b8'; // slate
  }
}
