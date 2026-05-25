"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

interface KpiCardProps {
  title: string;
  value: string | number;
  sub?: string;
  color?: string;
  icon?: React.ReactNode;
  trend?: "up" | "down" | "neutral";
  trendValue?: string;
  delay?: number;
}

export function KpiCard({
  title,
  value,
  sub,
  icon,
  trend,
  trendValue,
  delay = 0,
}: KpiCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay }}
      className="glass glass-hover p-5 flex flex-col gap-3"
    >
      <div className="flex items-start justify-between">
        <p
          className="text-xs font-medium uppercase tracking-wider"
          style={{ color: "var(--text-muted)" }}
        >
          {title}
        </p>
        {icon && (
          <div
            className="w-7 h-7 rounded-lg flex items-center justify-center"
            style={{
              background: "var(--accent-dim)",
              border: "1px solid var(--border-strong)",
            }}
          >
            {icon}
          </div>
        )}
      </div>

      <div>
        <p
          className="text-2xl font-bold tracking-tight"
          style={{ color: "var(--text-primary)" }}
        >
          {value}
        </p>
        {sub && (
          <p
            className="text-xs mt-1"
            style={{ color: "var(--text-secondary)" }}
          >
            {sub}
          </p>
        )}
      </div>

      {trendValue && (
        <div
          className={cn(
            "flex items-center gap-1 text-xs font-medium",
            trend === "up"
              ? "text-emerald-400"
              : trend === "down"
                ? "text-rose-400"
                : "text-zinc-400",
          )}
        >
          <span>{trend === "up" ? "↑" : trend === "down" ? "↓" : "→"}</span>
          <span>{trendValue}</span>
        </div>
      )}
    </motion.div>
  );
}
