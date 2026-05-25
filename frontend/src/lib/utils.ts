import { clsx, type ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

export function healthColor(score: number): string {
  if (score >= 70) return "#10b981";
  if (score >= 40) return "#f59e0b";
  if (score >= 30) return "#f97316";
  return "#ef4444";
}

export function healthLabel(score: number): string {
  if (score >= 70) return "HEALTHY";
  if (score >= 40) return "MODERATE";
  if (score >= 30) return "AT-RISK";
  return "CRITICAL";
}

export function healthBg(score: number): string {
  if (score >= 70) return "bg-emerald-500/15 text-emerald-400 border-emerald-500/25";
  if (score >= 40) return "bg-amber-500/15 text-amber-400 border-amber-500/25";
  if (score >= 30) return "bg-orange-500/15 text-orange-400 border-orange-500/25";
  return "bg-rose-500/15 text-rose-400 border-rose-500/25";
}

export function statusColor(status: string): string {
  switch (status) {
    case "resolved":    return "bg-emerald-500/15 text-emerald-400 border-emerald-500/25";
    case "escalated":   return "bg-rose-500/15 text-rose-400 border-rose-500/25";
    case "in_progress": return "bg-indigo-500/15 text-indigo-400 border-indigo-500/25";
    default:            return "bg-zinc-500/15 text-zinc-400 border-zinc-500/25";
  }
}

export function stateColor(state: string): string {
  switch (state) {
    case "WAITING":   return "text-amber-400 bg-amber-500/15 border-amber-500/25";
    case "VERIFYING": return "text-indigo-400 bg-indigo-500/15 border-indigo-500/25";
    case "RESOLVED":  return "text-emerald-400 bg-emerald-500/15 border-emerald-500/25";
    case "FAILED":    return "text-rose-400 bg-rose-500/15 border-rose-500/25";
    case "ESCALATED": return "text-purple-400 bg-purple-500/15 border-purple-500/25";
    default:          return "text-zinc-400 bg-zinc-500/15 border-zinc-500/25";
  }
}

export function formatInr(amount: number): string {
  return `₹${amount.toLocaleString("en-IN")}`;
}

export function formatPct(value: number): string {
  return `${value.toFixed(1)}%`;
}

export function relDelta(start: number | null, end: number | null): string {
  if (start == null || end == null) return "—";
  const delta = end - start;
  return delta > 0 ? `+${delta.toFixed(0)}` : `${delta.toFixed(0)}`;
}

export function relDeltaColor(start: number | null, end: number | null): string {
  if (start == null || end == null) return "text-zinc-400";
  return (end - start) >= 0 ? "text-emerald-400" : "text-rose-400";
}

export function truncate(str: string, n: number): string {
  return str.length > n ? str.slice(0, n) + "…" : str;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}
