"use client";

import { usePathname } from "next/navigation";
import { Activity, Settings, ShieldAlert } from "lucide-react";
import useSWR from "swr";
import { api } from "@/lib/api";

const crumbs: Record<string, string> = {
  "/project": "Project Overview",
  "/workspace": "Team Dashboard",
  "/setup": "Project Setup",
  "/overview": "Overview",
  "/cases": "Cases",
  "/demo": "Conversation UI",
  "/actions": "Action Console",
  "/audit": "Audit + Handoff",
  "/evaluation": "Evaluator",
  "/admin": "Test Harness",
  "/submission": "Submission",
  "/test": "Test Console",
  "/rag": "Knowledge Explorer",
  "/tools": "Tools Explorer",
};

export function TopBar() {
  const pathname = usePathname();
  const { data: healthData, error } = useSWR("health-check", () => api.health.check(), { refreshInterval: 30000 });

  const isHealthy = healthData?.status === "healthy" && !error;
  const isError = error || (healthData && healthData.status !== "healthy");

  const page = Object.entries(crumbs).find(
    ([k]) => pathname === k || pathname.startsWith(k + "/"),
  );
  const title = page ? page[1] : "Dashboard";
  const sub = pathname.startsWith("/cases/")
    ? `/ ${pathname.split("/cases/")[1]}`
    : "";

  return (
    <header
      className="fixed top-0 left-64 right-0 h-14 flex items-center px-6 z-20"
      style={{
        background: "rgba(8,8,14,0.85)",
        backdropFilter: "blur(12px)",
        borderBottom: "1px solid var(--border)",
      }}
    >
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm">
        <span style={{ color: "var(--text-muted)" }}>ResolveFlow</span>
        <span style={{ color: "var(--text-muted)" }}>/</span>
        <span style={{ color: "var(--text-primary)" }} className="font-medium">
          {title}
        </span>
        {sub && (
          <>
            <span style={{ color: "var(--text-muted)" }}>/</span>
            <span
              className="font-mono text-xs"
              style={{ color: "var(--accent-hover)" }}
            >
              {sub}
            </span>
          </>
        )}
      </div>

      <div className="flex-1" />

      {/* Right controls */}
      <div className="flex items-center gap-3">
        <div
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full"
          style={{
            background: isHealthy ? "rgba(16,185,129,0.08)" : "rgba(244,63,94,0.08)",
            border: "1px solid var(--border-strong)",
            color: isHealthy ? "#34d399" : "#fb7185",
          }}
        >
          {isHealthy ? (
            <>
              <Activity size={11} />
              <span className="font-medium">System healthy</span>
            </>
          ) : isError ? (
            <>
              <ShieldAlert size={11} />
              <span className="font-medium">System degraded</span>
            </>
          ) : (
            <>
              <span className="font-medium animate-pulse">Checking health...</span>
            </>
          )}
        </div>
        <button
          className="w-8 h-8 flex items-center justify-center rounded-lg transition-colors hover:bg-white/5"
          style={{ color: "var(--text-muted)" }}
        >
          <Settings size={15} />
        </button>
      </div>
    </header>
  );
}
