"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Wrench, Play, Code2, AlertTriangle, CheckCircle2 } from "lucide-react";
import { PageHeader, GlassPanel, SectionLabel } from "@/components/BlueprintPrimitives";
import { api } from "@/lib/api";
import type { ToolResponse } from "@/lib/types";

const TOOLS_CONFIG = [
  { id: "lookupCustomer", name: "lookup_customer", defaultPayload: '{\n  "customer_id": "CUST-1001"\n}', type: "GET" },
  { id: "getInvoiceHistory", name: "get_invoice_history", defaultPayload: '{\n  "customer_id": "CUST-1001",\n  "months": 3\n}', type: "GET" },
  { id: "checkDuplicateCharge", name: "check_duplicate_charge", defaultPayload: '{\n  "customer_id": "CUST-1001",\n  "lookback_days": 30\n}', type: "GET" },
  { id: "checkOutageStatus", name: "check_outage_status", defaultPayload: '{\n  "location": "Chennai Zone-04",\n  "customer_id": "CUST-1001"\n}', type: "GET" },
  { id: "runRouterDiagnostic", name: "run_router_diagnostic", defaultPayload: '{\n  "customer_id": "CUST-1001"\n}', type: "GET" },
  { id: "retrievePolicy", name: "retrieve_policy", defaultPayload: '{\n  "policy_name": "service_credit_policy",\n  "query": "duplicate charge",\n  "top_k": 3\n}', type: "GET" },
  { id: "applyCredit", name: "apply_credit", defaultPayload: '{\n  "customer_id": "CUST-1001",\n  "amount": 599,\n  "reason": "Duplicate charge verified",\n  "policy_context": {\n    "duplicate_charge_verified": true\n  },\n  "policy_name": "service_credit_policy"\n}', type: "POST" },
  { id: "createTicket", name: "create_ticket", defaultPayload: '{\n  "customer_id": "CUST-1001",\n  "issue_type": "outage",\n  "priority": "high",\n  "status": "open"\n}', type: "POST" },
  { id: "scheduleTechnician", name: "schedule_technician", defaultPayload: '{\n  "customer_id": "CUST-1001",\n  "time_slot": "2026-05-22T10:00:00Z",\n  "policy_context": {\n    "outage_verified": true\n  },\n  "policy_name": "technician_visit_policy"\n}', type: "POST" },
  { id: "changePlan", name: "change_plan", defaultPayload: '{\n  "customer_id": "CUST-1001",\n  "new_plan_id": "PLAN-BASIC",\n  "policy_context": {\n    "retention_offer_rejected": true\n  },\n  "policy_name": "plan_change_policy"\n}', type: "POST" },
  { id: "generateHandoffSummary", name: "generate_handoff_summary", defaultPayload: '{\n  "conversation_id": "demo-rahul-1029",\n  "handoff_reason": "Verification failed"\n}', type: "POST" },
  { id: "generateContextCard", name: "generate_context_card", defaultPayload: '{\n  "conversation_id": "demo-rahul-1029"\n}', type: "POST" },
  { id: "generateOpeningLine", name: "generate_opening_line", defaultPayload: '{\n  "conversation_id": "demo-rahul-1029",\n  "handoff_reason": "Escalated to human supervisor"\n}', type: "POST" },
  { id: "generateAuditLog", name: "generate_audit_log", defaultPayload: '{\n  "case_id": "#1029",\n  "customer_id": "CUST-1001",\n  "session_id": "demo-rahul-1029",\n  "tools_called": [],\n  "evidence_used": [],\n  "action_taken": [],\n  "policy_dag_path": [],\n  "policy_status": "compliant"\n}', type: "POST" },
];

export default function ToolsExplorerPage() {
  const [activeToolId, setActiveToolId] = useState(TOOLS_CONFIG[0].id);
  const [payloadText, setPayloadText] = useState(TOOLS_CONFIG[0].defaultPayload);
  const [response, setResponse] = useState<any>(null);
  const [isExecuting, setIsExecuting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeTool = TOOLS_CONFIG.find((t) => t.id === activeToolId)!;

  function handleToolSelect(id: string) {
    const tool = TOOLS_CONFIG.find((t) => t.id === id)!;
    setActiveToolId(id);
    setPayloadText(tool.defaultPayload);
    setResponse(null);
    setError(null);
  }

  async function executeTool() {
    setIsExecuting(true);
    setError(null);
    setResponse(null);

    try {
      let parsedPayload: any = {};
      try {
        parsedPayload = JSON.parse(payloadText);
      } catch (err) {
        throw new Error("Invalid JSON Payload format.");
      }

      let res: ToolResponse;
      switch (activeToolId) {
        case "lookupCustomer": res = await api.tools.lookupCustomer(parsedPayload.customer_id); break;
        case "getInvoiceHistory": res = await api.tools.getInvoiceHistory(parsedPayload.customer_id, parsedPayload.months); break;
        case "checkDuplicateCharge": res = await api.tools.checkDuplicateCharge(parsedPayload.customer_id, parsedPayload.lookback_days); break;
        case "checkOutageStatus": res = await api.tools.checkOutageStatus(parsedPayload.location, parsedPayload.customer_id); break;
        case "runRouterDiagnostic": res = await api.tools.runRouterDiagnostic(parsedPayload.customer_id); break;
        case "retrievePolicy": res = await api.tools.retrievePolicy(parsedPayload.policy_name, parsedPayload.query, parsedPayload.top_k); break;
        case "applyCredit": res = await api.tools.applyCredit(parsedPayload); break;
        case "createTicket": res = await api.tools.createTicket(parsedPayload); break;
        case "scheduleTechnician": res = await api.tools.scheduleTechnician(parsedPayload); break;
        case "changePlan": res = await api.tools.changePlan(parsedPayload); break;
        case "generateHandoffSummary": res = await api.tools.generateHandoffSummary(parsedPayload); break;
        case "generateContextCard": res = await api.tools.generateContextCard(parsedPayload); break;
        case "generateOpeningLine": res = await api.tools.generateOpeningLine(parsedPayload); break;
        case "generateAuditLog": res = await api.tools.generateAuditLog(parsedPayload); break;
        default: throw new Error("Tool not mapped");
      }
      setResponse(res);
    } catch (err: any) {
      setError(err.message || String(err));
    } finally {
      setIsExecuting(false);
    }
  }

  return (
    <div className="p-6 max-w-7xl mx-auto h-full flex flex-col">
      <PageHeader
        eyebrow="Admin Interface"
        title="Tools Explorer"
        subtitle="Test individual agent tools directly against the backend API."
      />

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6 mt-6 flex-1 min-h-[600px]">
        {/* Sidebar Tools List */}
        <GlassPanel className="p-4 overflow-y-auto">
          <SectionLabel>Available Tools</SectionLabel>
          <div className="space-y-2 mt-4">
            {TOOLS_CONFIG.map((tool) => {
              const active = tool.id === activeToolId;
              return (
                <button
                  key={tool.id}
                  onClick={() => handleToolSelect(tool.id)}
                  className="w-full text-left p-3 rounded-lg flex items-center justify-between transition-colors"
                  style={{
                    background: active ? "rgba(99,102,241,0.16)" : "var(--surface-2)",
                    border: active ? "1px solid rgba(129,140,248,0.45)" : "1px solid var(--border)",
                  }}
                >
                  <span className="text-xs font-mono font-semibold" style={{ color: "var(--text-primary)" }}>{tool.name}</span>
                  <span className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded" style={{ background: tool.type === "GET" ? "rgba(16,185,129,0.15)" : "rgba(245,158,11,0.15)", color: tool.type === "GET" ? "#34d399" : "#fbbf24" }}>
                    {tool.type}
                  </span>
                </button>
              );
            })}
          </div>
        </GlassPanel>

        {/* Execution Area */}
        <div className="flex flex-col gap-4">
          <GlassPanel className="p-5 flex-1 flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <Wrench size={18} style={{ color: "#818cf8" }} />
                <h3 className="text-lg font-semibold font-mono" style={{ color: "var(--text-primary)" }}>{activeTool.name}</h3>
              </div>
              <button
                onClick={executeTool}
                disabled={isExecuting}
                className="px-4 py-2 text-sm font-semibold rounded-lg flex items-center gap-2 transition-all hover:scale-105 active:scale-95 disabled:opacity-50 disabled:hover:scale-100"
                style={{ background: "var(--accent)", color: "#000" }}
              >
                {isExecuting ? "Executing..." : "Execute"} <Play size={14} />
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 flex-1">
              <div className="flex flex-col">
                <label className="text-xs font-semibold mb-2 uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>JSON Payload</label>
                <textarea
                  value={payloadText}
                  onChange={(e) => setPayloadText(e.target.value)}
                  className="flex-1 w-full p-4 text-sm font-mono rounded-lg outline-none resize-none"
                  spellCheck={false}
                  style={{ background: "var(--surface-3)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
                />
              </div>
              
              <div className="flex flex-col">
                <label className="text-xs font-semibold mb-2 uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>Response</label>
                <div className="flex-1 w-full p-4 text-sm font-mono rounded-lg overflow-y-auto" style={{ background: "var(--bg)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                  {error && (
                    <div className="flex items-center gap-2 text-red-400 mb-2 p-3 rounded bg-red-400/10 border border-red-400/20">
                      <AlertTriangle size={16} />
                      <span className="text-xs whitespace-pre-wrap">{error}</span>
                    </div>
                  )}
                  {response && (
                    <motion.div initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }}>
                      <div className="flex items-center gap-2 mb-4">
                        <CheckCircle2 size={16} style={{ color: "#34d399" }} />
                        <span className="text-xs font-bold text-emerald-400">Success</span>
                      </div>
                      <pre className="text-xs overflow-x-auto whitespace-pre-wrap" style={{ color: "var(--text-primary)" }}>
                        {JSON.stringify(response, null, 2)}
                      </pre>
                    </motion.div>
                  )}
                  {!response && !error && !isExecuting && (
                    <div className="h-full flex flex-col items-center justify-center opacity-30 gap-2">
                      <Code2 size={24} />
                      <span>Waiting for execution</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </GlassPanel>
        </div>
      </div>
    </div>
  );
}
