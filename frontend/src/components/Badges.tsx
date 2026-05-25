"use client";

import { cn } from "@/lib/utils";
import { healthColor, healthBg, statusColor, stateColor } from "@/lib/utils";

interface BadgeProps {
  children: React.ReactNode;
  className?: string;
  variant?: "health" | "status" | "state" | "default";
  score?: number;
  status?: string;
  state?: string;
}

export function Badge({
  children,
  className,
  variant = "default",
  score,
  status,
  state,
}: BadgeProps) {
  let styles = "bg-zinc-500/15 text-zinc-400 border-zinc-500/25";
  if (variant === "health" && score !== undefined) styles = healthBg(score);
  if (variant === "status" && status) styles = statusColor(status);
  if (variant === "state" && state) styles = stateColor(state);

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-medium uppercase tracking-wide",
        styles,
        className,
      )}
    >
      {children}
    </span>
  );
}

interface HealthRingProps {
  score: number;
  size?: number;
}

export function HealthRing({ score, size = 40 }: HealthRingProps) {
  const r = (size - 4) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * r;
  const dash = (score / 100) * circumference;
  const color = healthColor(score);

  return (
    <svg width={size} height={size}>
      <g transform={`rotate(-90, ${cx}, ${cy})`}>
        <circle
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke="rgba(255,255,255,0.06)"
          strokeWidth="3"
        />
        <circle
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth="3"
          strokeDasharray={`${dash} ${circumference}`}
          strokeLinecap="round"
        />
      </g>
      <text
        x={cx}
        y={cy}
        dominantBaseline="middle"
        textAnchor="middle"
        style={{ fill: color, fontSize: size * 0.28, fontWeight: 700 }}
      >
        {score}
      </text>
    </svg>
  );
}

interface RelArcProps {
  start: number;
  end: number;
}

export function RelArc({ start, end }: RelArcProps) {
  const delta = end - start;
  const improved = delta >= 0;

  return (
    <div className="flex items-center gap-2">
      <span
        className="text-sm font-mono font-bold"
        style={{ color: improved ? "#10b981" : "#ef4444" }}
      >
        {start}
      </span>
      <div className="flex flex-col items-center">
        <div
          className="w-10 h-px"
          style={{ background: improved ? "#10b981" : "#ef4444" }}
        />
        <span
          className="text-[10px] mt-0.5"
          style={{ color: improved ? "#10b981" : "#ef4444" }}
        >
          {delta > 0 ? `+${delta.toFixed(0)}` : delta.toFixed(0)}
        </span>
      </div>
      <span
        className="text-sm font-mono font-bold"
        style={{ color: improved ? "#10b981" : "#ef4444" }}
      >
        {end}
      </span>
    </div>
  );
}
