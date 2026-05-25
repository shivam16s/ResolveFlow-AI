"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { Search, ChevronUp, ChevronDown, ExternalLink, Filter } from "lucide-react";
import { Badge, HealthRing } from "@/components/Badges";
import { api } from "@/lib/api";
import { formatDate, truncate } from "@/lib/utils";
import type { CaseListResponse } from "@/lib/types";

type SortKey = "created_at" | "health_score" | "status" | "turns";
type SortDir = "asc" | "desc";

function EmptyState({ label }: { label: string }) {
  return (
    <div className="glass py-16 text-center text-sm" style={{ color: "var(--text-muted)" }}>
      {label}
    </div>
  );
}

function SortButton({
  col,
  label,
  sortKey,
  sortDir,
  onSort,
}: {
  col: SortKey;
  label: string;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (col: SortKey) => void;
}) {
  return (
    <button
      onClick={() => onSort(col)}
      className="flex items-center gap-1 text-xs uppercase font-semibold transition-colors hover:text-teal-300"
      style={{ color: sortKey === col ? "#5eead4" : "var(--text-muted)" }}
    >
      {label}
      {sortKey === col ? (sortDir === "asc" ? <ChevronUp size={10} /> : <ChevronDown size={10} />) : <ChevronDown size={10} className="opacity-30" />}
    </button>
  );
}

export default function CasesPage() {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("created_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [filter, setFilter] = useState<string>("all");
  const [page, setPage] = useState(1);

  const { data, error, isLoading } = useSWR<CaseListResponse>(["cases", page], () => api.cases.list(page, 20));

  const filtered = useMemo(() => {
    const rows = data?.cases ?? [];
    return rows
      .filter((item) => filter === "all" || item.status === filter)
      .filter((item) => {
        const needle = search.trim().toLowerCase();
        if (!needle) return true;
        return (
          item.case_id.toLowerCase().includes(needle) ||
          item.customer_name.toLowerCase().includes(needle) ||
          item.customer_id.toLowerCase().includes(needle) ||
          item.issues.some((issue) => issue.toLowerCase().includes(needle))
        );
      })
      .sort((a, b) => {
        const left: string | number = a[sortKey] ?? "";
        const right: string | number = b[sortKey] ?? "";
        if (typeof left === "string" && typeof right === "string") {
          return sortDir === "asc" ? left.localeCompare(right) : right.localeCompare(left);
        }
        return sortDir === "asc" ? Number(left) - Number(right) : Number(right) - Number(left);
      });
  }, [data?.cases, filter, search, sortDir, sortKey]);

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / (data?.limit ?? 20)));
  function handleSort(col: SortKey) {
    if (sortKey === col) setSortDir((current) => current === "asc" ? "desc" : "asc");
    else {
      setSortKey(col);
      setSortDir("desc");
    }
  }

  function openCase(item: CaseListResponse["cases"][number]) {
    router.push(`/cases/${encodeURIComponent(item.route_id ?? item.case_id)}`);
  }

  return (
    <div className="p-6 max-w-7xl">
      <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
        <h1 className="text-xl font-bold gradient-text">Case Browser</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
          {data?.total ?? 0} sessions from FastAPI
        </p>
      </motion.div>

      <div className="flex flex-wrap items-center gap-3 mb-5">
        <div className="relative flex-1 min-w-64 max-w-sm">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--text-muted)" }} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search cases, customers, issues"
            className="w-full pl-9 pr-3 py-2 text-sm rounded-lg outline-none transition-all focus:ring-1"
            style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)", "--tw-ring-color": "var(--accent)" } as React.CSSProperties}
          />
        </div>
        <div className="flex items-center gap-1.5 p-1 rounded-lg" style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}>
          <Filter size={12} style={{ color: "var(--text-muted)", marginLeft: 6 }} />
          {["all", "resolved", "escalated", "in_progress", "open"].map((status) => (
            <button
              key={status}
              onClick={() => setFilter(status)}
              className="px-3 py-1.5 text-xs rounded-md font-medium transition-all capitalize"
              style={filter === status
                ? { background: "rgba(20,184,166,0.14)", color: "#5eead4", border: "1px solid rgba(20,184,166,0.35)" }
                : { color: "var(--text-muted)" }}
            >
              {status.replace("_", " ")}
            </button>
          ))}
        </div>
      </div>

      {error && !data && <EmptyState label="Could not load cases from FastAPI." />}
      {isLoading && !data && <EmptyState label="Loading cases..." />}
      {!isLoading && !error && filtered.length === 0 && <EmptyState label="No cases match the current filters." />}

      {filtered.length > 0 && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="glass overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b" style={{ borderColor: "var(--border)" }}>
                  <th className="px-4 py-3 text-left text-xs uppercase font-semibold" style={{ color: "var(--text-muted)" }}>Case</th>
                  <th className="px-4 py-3 text-left text-xs uppercase font-semibold" style={{ color: "var(--text-muted)" }}>Customer</th>
                  <th className="px-4 py-3 text-left text-xs uppercase font-semibold" style={{ color: "var(--text-muted)" }}>Issues</th>
                  <th className="px-4 py-3 text-left"><SortButton col="status" label="Status" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} /></th>
                  <th className="px-4 py-3 text-left"><SortButton col="health_score" label="Health" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} /></th>
                  <th className="px-4 py-3 text-left"><SortButton col="turns" label="Turns" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} /></th>
                  <th className="px-4 py-3 text-left"><SortButton col="created_at" label="Time" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} /></th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody>
                {filtered.map((item, index) => (
                  <motion.tr
                    key={item.case_id}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: index * 0.03 }}
                    onClick={() => openCase(item)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        openCase(item);
                      }
                    }}
                    tabIndex={0}
                    role="link"
                    aria-label={`Open case ${item.case_id}`}
                    className="border-b transition-colors hover:bg-white/3 cursor-pointer focus:outline-none focus:bg-white/4"
                    style={{ borderColor: "var(--border)" }}
                  >
                    <td className="px-4 py-3.5">
                      <span className="font-mono text-xs font-semibold" style={{ color: "#5eead4" }}>{item.case_id}</span>
                    </td>
                    <td className="px-4 py-3.5">
                      <p className="font-medium text-sm" style={{ color: "var(--text-primary)" }}>{item.customer_name}</p>
                      <p className="text-[11px] font-mono mt-0.5" style={{ color: "var(--text-muted)" }}>{item.customer_id}</p>
                    </td>
                    <td className="px-4 py-3.5">
                      <div className="flex flex-wrap gap-1">
                        {item.issues.map((issue) => (
                          <span key={issue} className="px-1.5 py-0.5 text-[10px] rounded font-medium" style={{ background: "rgba(20,184,166,0.12)", color: "#5eead4", border: "1px solid rgba(20,184,166,0.25)" }}>
                            {truncate(issue, 18)}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3.5">
                      <Badge variant="status" status={item.status}>{item.status.replace("_", " ")}</Badge>
                    </td>
                    <td className="px-4 py-3.5"><HealthRing score={Math.round(item.health_score)} size={34} /></td>
                    <td className="px-4 py-3.5 font-mono" style={{ color: "var(--text-secondary)" }}>{item.turns}</td>
                    <td className="px-4 py-3.5 text-xs" style={{ color: "var(--text-muted)" }}>{formatDate(item.created_at)}</td>
                    <td className="px-4 py-3.5">
                      <span className="flex items-center gap-1 text-xs font-medium transition-colors group-hover:text-teal-300" style={{ color: "var(--text-muted)" }}>
                        <ExternalLink size={12} />
                      </span>
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          </div>
        </motion.div>
      )}

      {data && data.total > data.limit && (
        <div className="flex items-center justify-end gap-2 mt-4">
          <button disabled={page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))} className="px-3 py-1.5 text-xs rounded-md disabled:opacity-40" style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>Previous</button>
          <span className="text-xs font-mono" style={{ color: "var(--text-muted)" }}>Page {page} / {totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))} className="px-3 py-1.5 text-xs rounded-md disabled:opacity-40" style={{ background: "var(--surface-2)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>Next</button>
        </div>
      )}
    </div>
  );
}
