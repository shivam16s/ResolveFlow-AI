import type { ReactNode } from "react";

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  action,
}: {
  eyebrow?: string;
  title: string;
  subtitle: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        {eyebrow && (
          <p className="mb-2 text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>
            {eyebrow}
          </p>
        )}
        <h1 className="text-2xl font-bold gradient-text">{title}</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          {subtitle}
        </p>
      </div>
      {action}
    </div>
  );
}

export function GlassPanel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={`glass ${className}`}>{children}</section>;
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="mb-3 text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>
      {children}
    </p>
  );
}

export function StatusPill({
  children,
  tone = "teal",
}: {
  children: ReactNode;
  tone?: "teal" | "indigo" | "amber" | "rose" | "green";
}) {
  const colors = {
    teal: ["#5eead4", "rgba(20,184,166,0.12)", "rgba(20,184,166,0.3)"],
    indigo: ["#a5b4fc", "rgba(99,102,241,0.14)", "rgba(99,102,241,0.32)"],
    amber: ["#fbbf24", "rgba(245,158,11,0.13)", "rgba(245,158,11,0.32)"],
    rose: ["#fb7185", "rgba(244,63,94,0.12)", "rgba(244,63,94,0.3)"],
    green: ["#34d399", "rgba(16,185,129,0.12)", "rgba(16,185,129,0.3)"],
  }[tone];
  return (
    <span
      className="inline-flex items-center rounded-md px-2 py-1 text-[11px] font-semibold"
      style={{ color: colors[0], background: colors[1], border: `1px solid ${colors[2]}` }}
    >
      {children}
    </span>
  );
}

export function MiniMetric({
  label,
  value,
  sub,
}: {
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className="glass p-4">
      <p className="text-[11px] font-semibold uppercase tracking-widest" style={{ color: "var(--text-muted)" }}>
        {label}
      </p>
      <p className="mt-3 text-2xl font-bold font-mono" style={{ color: "#5eead4" }}>
        {value}
      </p>
      {sub && <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>{sub}</p>}
    </div>
  );
}

export function ProgressRow({
  label,
  value,
  tone = "teal",
}: {
  label: string;
  value: number;
  tone?: "teal" | "amber" | "rose" | "indigo";
}) {
  const color = tone === "amber" ? "#f59e0b" : tone === "rose" ? "#ef4444" : tone === "indigo" ? "#818cf8" : "#14b8a6";
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span style={{ color: "var(--text-secondary)" }}>{label}</span>
        <span className="font-mono" style={{ color }}>{value}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full" style={{ background: "rgba(255,255,255,0.06)" }}>
        <div className="h-full rounded-full" style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: color }} />
      </div>
    </div>
  );
}
