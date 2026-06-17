"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  FolderOpen,
  FlaskConical,
  BrainCircuit,
  MessageSquareText,
  ChevronRight,
  Handshake,
  Home,
} from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/project", label: "Project Overview", icon: Home },
  { href: "/demo", label: "Conversation Cockpit", icon: MessageSquareText },
  { href: "/cases", label: "Cases", icon: FolderOpen },
  { href: "/audit", label: "Audit Trail", icon: Handshake },
  { href: "/rag", label: "Knowledge Base", icon: BrainCircuit },
  { href: "/evaluation", label: "Evaluation Lab", icon: FlaskConical },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside
      className="fixed left-0 top-0 h-full w-64 flex-col z-30 hidden lg:flex"
      style={{
        background: "rgba(12,12,12,0.92)",
        backdropFilter: "blur(18px)",
        borderRight: "1px solid var(--border)",
      }}
    >
      {/* Logo */}
      <div
        className="flex items-center gap-2.5 px-5 py-5 border-b"
        style={{ borderColor: "var(--border)" }}
      >
        <div
          className="w-7 h-7 rounded-lg flex items-center justify-center"
          style={{
            background: "rgba(255,255,255,0.05)",
            border: "1px solid var(--border)",
          }}
        >
          <BrainCircuit size={15} style={{ color: "var(--accent-hover)" }} />
        </div>
        <div>
          <p
            className="text-sm font-semibold leading-none"
            style={{ color: "var(--text-primary)" }}
          >
            ResolveFlow
          </p>
          <p
            className="text-[10px] mt-0.5"
            style={{ color: "var(--text-muted)" }}
          >
            AI Operations
          </p>
        </div>
      </div>

      {/* Live indicator */}
      <div
        className="mx-4 mt-4 mb-2 flex items-center gap-2 px-3 py-2 rounded-lg"
        style={{
          background: "rgba(16,185,129,0.08)",
          border: "1px solid rgba(16,185,129,0.18)",
        }}
      >
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-dot" />
        <span className="text-[11px] text-emerald-400 font-medium">
          Live · ConnectCare
        </span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 pt-4 overflow-y-auto">
        <div className="space-y-1">
          {navItems.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(href + "/");
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-150 group",
                  active ? "text-teal-100" : "hover:bg-white/4",
                )}
                style={
                  active
                    ? { background: "var(--accent-dim)", color: "#a4f4fd", border: "1px solid rgba(164,244,253,0.18)" }
                    : { color: "var(--text-secondary)" }
                }
              >
                <Icon
                  size={15}
                  className={
                    active
                      ? "text-teal-200"
                      : "group-hover:text-teal-200 transition-colors"
                  }
                />
                <span className="flex-1">{label}</span>
                {active && <ChevronRight size={12} className="opacity-50" />}
              </Link>
            );
          })}
        </div>
      </nav>

      {/* Bottom version tag */}
      <div className="px-5 pb-5">
        <div
          className="rounded-lg px-3 py-2"
          style={{
            background: "var(--surface-3)",
            border: "1px solid var(--border)",
          }}
        >
          <p
            className="text-[10px] font-mono"
            style={{ color: "var(--text-muted)" }}
          >
            v1.1 · glass-box frontend
          </p>
          <p
            className="text-[10px] mt-0.5"
            style={{ color: "var(--text-muted)" }}
          >
            FlowZint · τ-bench · RAGAS
          </p>
        </div>
      </div>
    </aside>
  );
}
