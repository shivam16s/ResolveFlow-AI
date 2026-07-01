import React from "react";
import { WifiOff, FileText, IndianRupee, ShieldCheck, Gift } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

export function OutageWidget({ result }: { result: Record<string, unknown> }) {
  // check_outage_status returns: verified, outage_cleared, duration_hours,
  // affected_area/location, start_time, end_time, customer_affected.
  if (!result || !result.verified) return null;
  const cleared = Boolean(result.outage_cleared);
  const duration = Number(result.duration_hours ?? 0);
  const area = result.affected_area || result.location;
  const statusLabel = cleared ? "Outage Resolved" : "Active Network Outage";
  const accent = cleared ? "#10b981" : "#ef4444";
  return (
    <div className="mt-3 p-4 rounded-xl shadow-lg border relative overflow-hidden" style={{ background: `${accent}0d`, borderColor: `${accent}4d` }}>
      {!cleared && (
        <div className="absolute top-0 right-0 p-2">
          <span className="relative flex h-3 w-3">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500"></span>
          </span>
        </div>
      )}
      <div className="flex items-start gap-3 relative z-10">
        <div className="p-2 rounded-full" style={{ background: `${accent}26`, color: accent }}>
          <WifiOff size={20} />
        </div>
        <div>
          <h4 className="font-bold text-sm uppercase tracking-wider mb-1" style={{ color: accent }}>{statusLabel}</h4>
          <p className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
            {duration ? `Verified outage lasting ${duration} hours` : "Verified outage on your line"}
            {result.customer_affected ? " — your account was affected." : "."}
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div className="p-2 rounded-md" style={{ background: "var(--surface-1)", border: "1px solid var(--border)" }}>
              <p style={{ color: "var(--text-muted)" }}>Status</p>
              <p className="font-semibold" style={{ color: "var(--text-primary)" }}>{cleared ? "Restored" : "Investigating"}</p>
            </div>
            <div className="p-2 rounded-md" style={{ background: "var(--surface-1)", border: "1px solid var(--border)" }}>
              <p style={{ color: "var(--text-muted)" }}>Impact Area</p>
              <p className="font-semibold" style={{ color: "var(--text-primary)" }}>{area ? String(area) : "Your Region"}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function InvoiceWidget({ result }: { result: Record<string, unknown> }) {
  if (!result || !Array.isArray(result.invoices) || !result.invoices.length) return null;
  // get_invoice_history items expose `date` (YYYY-MM-DD), not `due_date`.
  const data = result.invoices.map((inv: Record<string, unknown>) => ({
    name: String(inv.date ?? "").substring(5, 10),
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
  // apply_credit_guard returns an action-replay decision:
  // { already_taken, reason, matched_action: { summary, amount } | null }.
  if (!result || typeof result.already_taken === "undefined") return null;
  const alreadyTaken = Boolean(result.already_taken);
  const matched = (result.matched_action as Record<string, unknown> | null) ?? null;
  const summary = matched && matched.summary ? String(matched.summary) : null;
  const amount = matched && matched.amount != null ? Number(matched.amount) : null;
  const accent = alreadyTaken ? "#10b981" : "#6366f1";
  const title = alreadyTaken ? "Credit Already Applied" : "Credit Guard — Eligible";
  const subtitle = alreadyTaken
    ? (summary ?? "A matching credit was already applied; not re-running it.")
    : "Verified eligible — queued at the policy gate, not yet applied.";
  return (
    <div className="mt-3 p-3 rounded-xl shadow-lg border flex items-center justify-between" style={{ background: `${accent}0d`, borderColor: `${accent}33` }}>
      <div className="flex items-center gap-3">
        <div className="p-1.5 rounded-full" style={{ background: `${accent}33`, color: accent }}>
          <ShieldCheck size={18} />
        </div>
        <div>
          <h4 className="font-bold text-sm" style={{ color: accent }}>{title}</h4>
          <p className="text-xs" style={{ color: "var(--text-secondary)" }}>{subtitle}</p>
        </div>
      </div>
      {amount != null && (
        <div className="text-right">
          <span className="font-bold text-lg flex items-center" style={{ color: accent }}>
            <IndianRupee size={16} className="mr-0.5" />{amount}
          </span>
        </div>
      )}
    </div>
  );
}

export function RetentionWidget({ result }: { result: Record<string, unknown> }) {
  // build_retention_offer: { offer_available, headline, discount_pct,
  // discount_months, estimated_total_savings, waive_cancellation_fee }.
  if (!result || !result.offer_available) return null;
  const savings = Number(result.estimated_total_savings ?? 0);
  return (
    <div className="mt-3 p-4 rounded-xl shadow-lg border" style={{ background: "rgba(245, 158, 11, 0.06)", borderColor: "rgba(245, 158, 11, 0.32)" }}>
      <div className="flex items-center gap-2 mb-2">
        <Gift size={16} style={{ color: "#f59e0b" }} />
        <h4 className="font-bold text-sm" style={{ color: "#f59e0b" }}>Retention Offer</h4>
      </div>
      <p className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{String(result.headline ?? "Exclusive loyalty offer")}</p>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
        <div className="p-2 rounded-md" style={{ background: "var(--surface-1)", border: "1px solid var(--border)" }}>
          <p style={{ color: "var(--text-muted)" }}>You save (est.)</p>
          <p className="font-semibold flex items-center" style={{ color: "#10b981" }}><IndianRupee size={12} className="mr-0.5" />{savings}</p>
        </div>
        <div className="p-2 rounded-md" style={{ background: "var(--surface-1)", border: "1px solid var(--border)" }}>
          <p style={{ color: "var(--text-muted)" }}>Cancellation fee</p>
          <p className="font-semibold" style={{ color: "var(--text-primary)" }}>{result.waive_cancellation_fee ? "Waived" : "Applies"}</p>
        </div>
      </div>
    </div>
  );
}
