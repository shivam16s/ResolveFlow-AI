import React from "react";
import { WifiOff, FileText, CheckCircle2, IndianRupee } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

export function OutageWidget({ result }: { result: Record<string, unknown> }) {
  if (!result || !result.outage_active) return null;
  return (
    <div className="mt-3 p-4 rounded-xl shadow-lg border relative overflow-hidden" style={{ background: "rgba(239, 68, 68, 0.05)", borderColor: "rgba(239, 68, 68, 0.3)" }}>
      <div className="absolute top-0 right-0 p-2">
        <span className="relative flex h-3 w-3">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
          <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500"></span>
        </span>
      </div>
      <div className="flex items-start gap-3 relative z-10">
        <div className="p-2 rounded-full" style={{ background: "rgba(239, 68, 68, 0.15)", color: "#ef4444" }}>
          <WifiOff size={20} />
        </div>
        <div>
          <h4 className="font-bold text-sm text-red-500 uppercase tracking-wider mb-1">Network Outage Detected</h4>
          <p className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{String(result.cause)}</p>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div className="p-2 rounded-md" style={{ background: "var(--surface-1)", border: "1px solid var(--border)" }}>
              <p style={{ color: "var(--text-muted)" }}>Est. Resolution</p>
              <p className="font-semibold" style={{ color: "var(--text-primary)" }}>{String(result.estimated_resolution)}</p>
            </div>
            <div className="p-2 rounded-md" style={{ background: "var(--surface-1)", border: "1px solid var(--border)" }}>
              <p style={{ color: "var(--text-muted)" }}>Impact Area</p>
              <p className="font-semibold" style={{ color: "var(--text-primary)" }}>{result.location ? String(result.location) : "Your Region"}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function InvoiceWidget({ result }: { result: Record<string, unknown> }) {
  if (!result || !Array.isArray(result.invoices) || !result.invoices.length) return null;
  const data = result.invoices.map((inv: Record<string, unknown>) => ({
    name: String(inv.due_date).substring(5, 10),
    amount: Number(inv.amount),
    status: String(inv.status),
  })).reverse();

  return (
    <div className="mt-3 p-4 rounded-xl shadow-lg border" style={{ background: "var(--surface-2)", borderColor: "var(--border)" }}>
      <div className="flex items-center gap-2 mb-4">
        <FileText size={16} style={{ color: "var(--text-muted)" }} />
        <h4 className="font-bold text-sm" style={{ color: "var(--text-primary)" }}>Billing History</h4>
      </div>
      <div className="h-32 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 0, right: 0, left: -25, bottom: 0 }}>
            <XAxis dataKey="name" tick={{ fontSize: 10, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fontSize: 10, fill: "var(--text-muted)" }} axisLine={false} tickLine={false} />
            <Tooltip 
              cursor={{ fill: "rgba(255,255,255,0.05)" }}
              contentStyle={{ background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: "8px", fontSize: "12px", color: "var(--text-primary)" }}
            />
            <Bar dataKey="amount" fill="#6366f1" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export function CreditWidget({ result }: { result: Record<string, unknown> }) {
  if (!result || !result.credit_applied) return null;
  return (
    <div className="mt-3 p-3 rounded-xl shadow-lg border flex items-center justify-between" style={{ background: "rgba(16, 185, 129, 0.05)", borderColor: "rgba(16, 185, 129, 0.2)" }}>
      <div className="flex items-center gap-3">
        <div className="p-1.5 rounded-full" style={{ background: "rgba(16, 185, 129, 0.2)", color: "#10b981" }}>
          <CheckCircle2 size={18} />
        </div>
        <div>
          <h4 className="font-bold text-sm text-emerald-500">Credit Guard Activated</h4>
          <p className="text-xs" style={{ color: "var(--text-secondary)" }}>{result.reason ? String(result.reason) : "Automatic refund processed"}</p>
        </div>
      </div>
      <div className="text-right">
        <span className="font-bold text-lg text-emerald-400 flex items-center">
          <IndianRupee size={16} className="mr-0.5" />{Number(result.amount)}
        </span>
      </div>
    </div>
  );
}
