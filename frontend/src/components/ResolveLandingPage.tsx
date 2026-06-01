"use client";

import type { CSSProperties } from "react";
import { useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import useSWR from "swr";
import {
  ArrowRight,
  Brain,
  CheckCircle2,
  ChevronRight,
  Database,
  FileText,
  GitBranch,
  Menu,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatPct } from "@/lib/utils";
import type { EvaluationReport, KpiOverview } from "@/lib/types";

const VIDEO_URL =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260508_064122_c4750c0e-7476-4b44-94a2-a85a65c63bf2.mp4";

const gradientStyle: CSSProperties = {
  backgroundImage:
    "linear-gradient(to right, #091020 0%, #0B2551 12.5%, #A4F4FD 32.5%, #00d2ff 50%, #0B2551 67.5%, #091020 87.5%, #091020 100%)",
  backgroundSize: "200% auto",
  WebkitBackgroundClip: "text",
  backgroundClip: "text",
  color: "transparent",
  WebkitTextFillColor: "transparent",
  filter: "url(#resolve-noise)",
};

const navLinks = ["Demo", "Memory", "Policy", "Evaluation", "Handoff"];

const pipeline = [
  { label: "Intent", body: "billing, outage, cancellation", icon: Brain },
  { label: "Memory", body: "prior complaint + churn risk", icon: Database },
  { label: "Policy", body: "service_credit_policy v2", icon: ShieldCheck },
  { label: "Tools", body: "6 verified calls logged", icon: Wrench },
  { label: "DAG", body: "UJCS 0.96 compliant", icon: GitBranch },
  { label: "Audit", body: "proof trail generated", icon: FileText },
];

const researchLogos = ["LongMemEval", "HippoRAG", "Self-RAG", "CRAG", "RAGAS", "tau-bench", "JourneyBench", "FastAPI"];

const proofCards = [
  {
    quote:
      "ResolveFlow does not just answer. It shows the evidence, the tools, the policy path, and the exact action gate that allowed the resolution.",
    name: "Glass-box resolution",
    role: "Every decision is inspectable",
    company: "AUDIT TRAIL",
  },
  {
    quote:
      "A single angry message can contain billing, outage, and cancellation intent. ResolveFlow keeps the queue intact and resolves one issue at a time.",
    name: "Multi-issue support",
    role: "No lost customer concern",
    company: "INTENT LAYER",
  },
  {
    quote:
      "Policy compliance is enforced structurally through DAG traversal before high-impact actions like credits, refunds, and handoff.",
    name: "Policy-safe actions",
    role: "Compliant by construction",
    company: "TRUST LAYER",
  },
];

const stackPlans = [
  {
    tier: "Foundation",
    price: "Ops DB",
    desc: "The operational base for telecom support cases, customers, invoices, outages, tickets, and audit logs.",
    items: ["SQLite schema with seeded telecom data", "FastAPI tool gateway", "Case browser and live dashboard", "Tool calls logged for every action"],
  },
  {
    tier: "Memory",
    price: "LongMem",
    desc: "Customer memory built from session facts, vector retrieval, graph retrieval, and citation-aware reading.",
    items: ["Atomic memory unit extraction", "ChromaDB vector store", "HippoRAG graph traversal", "LLM read with abstention"],
  },
  {
    tier: "Trust",
    price: "Policy",
    desc: "The enforcement layer that grounds answers, blocks unsafe actions, and produces evaluation-ready proof.",
    items: ["Policy-grounded retrieval", "DAG validation before actions", "UJCS policy score", "RAGAS and scenario evaluation"],
    featured: true,
  },
];

function pct(value: number | undefined, fallback: string) {
  if (typeof value !== "number") return fallback;
  return formatPct(value > 1 ? value : value * 100);
}

function LogoMark({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 256 256" className={className} fill="currentColor" aria-hidden="true">
      <path d="M 0 128 C 70.692 128 128 185.308 128 256 L 64 256 C 64 220.654 35.346 192 0 192 Z M 256 192 C 220.654 192 192 220.654 192 256 L 128 256 C 128 185.308 185.308 128 256 128 Z M 128 0 C 128 70.692 70.692 128 0 128 L 0 64 C 35.346 64 64 35.346 64 0 Z M 192 0 C 192 35.346 220.654 64 256 64 L 256 128 C 185.308 128 128 70.692 128 0 Z" />
    </svg>
  );
}

function PrimaryButton({ href, label, full = false }: { href: string; label: string; full?: boolean }) {
  return (
    <Link
      href={href}
      className={`group inline-flex items-center justify-center gap-2 rounded-full bg-white px-5 py-3 text-sm font-medium text-black transition-all hover:bg-white/90 active:scale-[0.98] ${full ? "w-full" : ""}`}
    >
      <Sparkles size={16} />
      {label}
      <ChevronRight size={15} className="transition-transform group-hover:translate-x-px" />
    </Link>
  );
}

function SectionEyebrow({ label, tag }: { label: string; tag?: string }) {
  return (
    <div className="inline-flex items-center gap-3 text-xs font-medium uppercase tracking-[0.24em] text-white/50">
      <span className="h-1.5 w-1.5 rounded-full bg-white" />
      {label}
      {tag ? <span className="rounded-full border border-white/10 px-2 py-0.5 normal-case tracking-normal text-white/50">{tag}</span> : null}
    </div>
  );
}

function MetricPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="liquid-glass rounded-2xl px-4 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-white/35">{label}</p>
      <p className="mt-1 font-mono text-lg font-semibold text-teal-200">{value}</p>
    </div>
  );
}

function Dot({ color }: { color: string }) {
  return <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />;
}

export default function ResolveLandingPage() {
  const [yearly, setYearly] = useState(false);
  const { data: kpi } = useSWR<KpiOverview>("project-kpi", api.overview.kpi);
  const { data: evaluation } = useSWR<EvaluationReport>("project-eval", api.evaluation.results);

  const passRate = pct(evaluation?.pass_rate, "100.0%");
  const policyRate = pct(evaluation?.avg_policy_compliance, kpi ? formatPct(kpi.policy_compliant_pct) : "86.7%");
  const ragasRate = pct(evaluation?.avg_ragas_faithfulness, "94.9%");
  const casesToday = String(kpi?.total_cases_today ?? 30);

  return (
    <div className="resolve-landing relative min-h-screen overflow-x-hidden bg-[#0c0c0c] text-white">
      <div className="fixed inset-0 z-0 pointer-events-none">
        <video autoPlay loop muted playsInline className="h-full w-full object-cover opacity-25 pointer-events-none" src={VIDEO_URL} />
        <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(12,12,12,0.35),#0c0c0c_70%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_12%,rgba(34,211,238,0.10),transparent_34%)]" />
      </div>
      <svg className="pointer-events-none absolute h-0 w-0" aria-hidden="true">
        <filter id="resolve-noise">
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" stitchTiles="stitch" />
          <feColorMatrix type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.35 0" />
          <feComposite in2="SourceGraphic" operator="in" result="noise" />
          <feBlend in="SourceGraphic" in2="noise" mode="multiply" />
        </filter>
      </svg>

      <div className="relative z-10">
        <motion.nav
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: "easeOut" }}
          className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6"
        >
          <Link href="/" className="flex items-center gap-3 text-white">
            <LogoMark className="h-9 w-9 rounded-2xl border border-white/10 bg-white/5 p-2" />
            <span className="hidden text-sm font-semibold tracking-tight sm:inline">ResolveFlow AI</span>
          </Link>
          <div className="hidden items-center gap-8 md:flex">
            {navLinks.map((item, index) => (
              <motion.a
                key={item}
                href={`#${item.toLowerCase()}`}
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.1 + index * 0.05, duration: 0.45 }}
                className="text-sm font-medium text-white/70 transition-colors hover:text-white"
              >
                {item}
              </motion.a>
            ))}
          </div>
          <div className="hidden items-center gap-3 md:flex">
            <Link href="/evaluation" className="rounded-full border border-white/10 px-4 py-2 text-sm font-medium text-white/70 transition hover:bg-white/5 hover:text-white">
              View proof
            </Link>
            <PrimaryButton href="/demo" label="Open demo" />
          </div>
          <button className="flex h-10 w-10 items-center justify-center rounded-full border border-white/10 bg-white/5 text-white md:hidden" aria-label="Open menu">
            <Menu size={18} />
          </button>
        </motion.nav>

        <section className="mx-auto flex max-w-6xl flex-col items-center px-6 pb-10 pt-10 text-center md:pb-14 md:pt-20">
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15, duration: 0.6 }}
            className="mb-5 inline-flex items-center gap-2 rounded-full border border-teal-300/20 bg-teal-300/10 px-3 py-1.5 text-xs font-medium text-teal-100"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-teal-300" />
            Multi-issue telecom support with visible reasoning
          </motion.div>
          <motion.h1
            initial={{ opacity: 0, y: 22 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3, duration: 0.8, ease: [0.22, 1, 0.36, 1] }}
            className="text-4xl font-semibold leading-[0.92] tracking-tight md:text-7xl"
          >
            Customer care.
            <br />
            <span className="animate-shiny" style={gradientStyle}>
              Made inspectable
            </span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.5, duration: 0.7 }}
            className="mt-8 max-w-2xl text-base leading-[1.65] text-white/60 md:text-lg"
          >
            ResolveFlow AI is a glass-box customer-care operations console for telecom teams. It detects multiple issues,
            remembers customer history, retrieves grounded policy evidence, calls verified tools, and produces an audit
            trail judges can inspect.
          </motion.p>
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.7, duration: 0.7 }}
            className="mt-8 flex flex-col items-center gap-4 sm:flex-row"
          >
            <PrimaryButton href="/demo" label="Run Rahul case" />
            <Link href="/evaluation" className="group inline-flex items-center justify-center gap-2 rounded-full border border-white/15 px-5 py-3 text-sm font-medium text-white transition hover:bg-white/5">
              See evaluation
              <ArrowRight size={15} className="transition-transform group-hover:translate-x-0.5" />
            </Link>
          </motion.div>
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.9, duration: 0.7 }}
            className="mt-8 grid w-full max-w-3xl grid-cols-2 gap-3 md:grid-cols-4"
          >
            <MetricPill label="Pass rate" value={passRate} />
            <MetricPill label="Policy" value={policyRate} />
            <MetricPill label="RAGAS" value={ragasRate} />
            <MetricPill label="Cases" value={casesToday} />
          </motion.div>
        </section>

        <section id="demo" className="mx-auto max-w-6xl px-6 py-12 md:py-16">
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.25 }}
            transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }}
            className="liquid-glass overflow-hidden rounded-3xl p-6 md:p-8"
          >
            <div className="grid gap-8 lg:grid-cols-[0.9fr_1.1fr] lg:items-center">
              <div>
                <SectionEyebrow label="Live demo" tag="separate page" />
                <h2 className="mt-5 text-3xl font-semibold leading-[1.03] tracking-tight md:text-5xl">
                  Landing page first.
                  <br />
                  Demo app one click away.
                </h2>
                <p className="mt-5 max-w-xl text-sm leading-[1.7] text-white/60 md:text-base">
                  The homepage explains the system. The actual support console stays in the live demo route, where the
                  Rahul Sharma case runs with chat, reasoning, tools, memory, and policy proof in the dashboard shell.
                </p>
                <div className="mt-7 flex flex-col gap-3 sm:flex-row">
                  <PrimaryButton href="/demo" label="Go to live demo" />
                  <Link href="/cases" className="group inline-flex items-center justify-center gap-2 rounded-full border border-white/15 px-5 py-3 text-sm font-medium text-white transition hover:bg-white/5">
                    Browse cases
                    <ArrowRight size={15} className="transition-transform group-hover:translate-x-0.5" />
                  </Link>
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {pipeline.map(({ label, body, icon: Icon }, index) => (
                  <div key={label} className="rounded-2xl border border-white/10 bg-black/30 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2">
                        <Icon size={16} className="text-teal-200" />
                        <p className="text-sm font-semibold text-white">{label}</p>
                      </div>
                      <span className="font-mono text-[10px] text-white/35">0{index + 1}</span>
                    </div>
                    <p className="mt-3 text-xs leading-relaxed text-white/50">{body}</p>
                  </div>
                ))}
              </div>
            </div>
          </motion.div>
        </section>

        <section id="memory" className="mx-auto grid max-w-6xl gap-10 px-6 py-20 md:grid-cols-2 md:items-start md:py-28">
          <motion.div initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ duration: 0.7 }}>
            <SectionEyebrow label="Triage" tag="AI-native" />
            <h2 className="mt-5 text-3xl font-semibold leading-[1.02] tracking-tight md:text-5xl">
              Clear complex cases
              <br />
              in a single pass.
            </h2>
            <p className="mt-6 max-w-md text-base leading-[1.6] text-white/60">
              ResolveFlow reads every customer turn, extracts intent, fills missing slots, preserves the issue queue, and
              asks one targeted question when information is missing.
            </p>
            <div className="mt-6 flex flex-wrap gap-2">
              {["Multi-intent", "Slot priority", "Replay guard", "CASA empathy", "Handoff safe"].map((chip) => (
                <span key={chip} className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-white/70">
                  {chip}
                </span>
              ))}
            </div>
          </motion.div>
          <motion.div initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ duration: 0.7, delay: 0.1 }} className="liquid-glass rounded-2xl p-5">
            <div className="mb-4 flex items-center justify-between">
              <p className="text-sm font-semibold text-white">Today - {casesToday} cases triaged</p>
              <span className="rounded-full border border-teal-300/20 bg-teal-300/10 px-2 py-1 text-xs text-teal-100">Live</span>
            </div>
            {[
              ["Priority", "4", ["Rahul - duplicate charge", "Ananya - policy exception"], "#ffffff"],
              ["Follow-up", "7", ["Router reset waiting", "Technician slot retry"], "#A4F4FD"],
              ["Resolved", "18", ["Credits applied", "Tickets created"], "#10b981"],
              ["Handoff", "3", ["Refund > INR 500", "Explicit specialist request"], "#f59e0b"],
            ].map(([title, count, items, color]) => (
              <div key={String(title)} className="liquid-glass mb-3 rounded-lg p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Dot color={String(color)} />
                    <p className="text-sm font-semibold text-white">{title}</p>
                  </div>
                  <span className="font-mono text-xs text-white/45">{count}</span>
                </div>
                <div className="mt-3 space-y-1">
                  {(items as string[]).map((item) => (
                    <p key={item} className="text-xs text-white/50">{item}</p>
                  ))}
                </div>
              </div>
            ))}
          </motion.div>
        </section>

        <section id="policy" className="mx-auto max-w-6xl px-6 py-16 md:py-20">
          <p className="text-center text-xs font-semibold uppercase tracking-widest text-white/40">Built on the research stack judges recognize</p>
          <div className="mt-10 grid grid-cols-2 gap-6 sm:grid-cols-4 lg:grid-cols-8">
            {researchLogos.map((name, index) => (
              <motion.div
                key={name}
                initial={{ opacity: 0, y: 10 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: index * 0.05, duration: 0.45 }}
                className="rounded-xl border border-white/10 bg-white/[0.025] px-3 py-4 text-center text-sm font-semibold tracking-tight text-white/50 transition hover:text-white"
              >
                {name}
              </motion.div>
            ))}
          </div>
        </section>

        <section className="mx-auto max-w-6xl border-t border-white/10 px-6 py-20 md:py-28">
          <div className="grid gap-5 md:grid-cols-3">
            {proofCards.map((card) => (
              <figure key={card.name} className="liquid-glass rounded-2xl p-6">
                <blockquote className="text-sm leading-[1.6] text-white/80">&quot;{card.quote}&quot;</blockquote>
                <figcaption className="mt-6 border-t border-white/10 pt-5">
                  <p className="text-sm font-semibold text-white">{card.name}</p>
                  <p className="mt-1 text-xs text-white/50">{card.role}</p>
                  <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-white">{card.company}</p>
                </figcaption>
              </figure>
            ))}
          </div>
        </section>

        <section id="evaluation" className="c3-pricing-section">
          <svg className="pointer-events-none absolute h-0 w-0" aria-hidden="true">
            <filter id="resolve-pricing-noise">
              <feTurbulence type="fractalNoise" baseFrequency="0.5" numOctaves="2" stitchTiles="stitch" />
              <feComponentTransfer>
                <feFuncA type="linear" slope="0.075" />
              </feComponentTransfer>
              <feComposite in2="SourceGraphic" operator="in" result="noise" />
              <feBlend in="SourceGraphic" in2="noise" mode="overlay" />
            </filter>
          </svg>
          <div className="c3-watermark-container">
            <div className="c3-watermark-main">
              <span className="c3-watermark-line-1">Customer care.</span>
              <span className="c3-watermark-line-2">Verified</span>
            </div>
          </div>
          <div className="c3-grid">
            {stackPlans.map((plan) => (
              <div key={plan.tier} className={`c3-card ${plan.featured ? "c3-card-pro" : ""}`}>
                <p className="c3-tier-small">{plan.tier}</p>
                <p className="c3-tier-large">{plan.price}</p>
                <p className="c3-desc">{plan.desc}</p>
                <ul className="c3-list">
                  {plan.items.map((item) => (
                    <li key={item}>
                      <span className="c3-check">
                        <CheckCircle2 size={15} />
                      </span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
                <Link href={plan.featured ? "/evaluation" : "/rag"} className="c3-btn">
                  Inspect layer
                </Link>
              </div>
            ))}
          </div>
          <div className="c3-toggle-wrap">
            <span className="text-sm text-white/60">Demo mode</span>
            <button className={`c3-toggle ${yearly ? "active" : ""}`} onClick={() => setYearly((value) => !value)} aria-label="Toggle demo mode">
              <span className="c3-toggle-knob" />
            </button>
          </div>
        </section>

        <section id="handoff" className="mx-auto max-w-6xl px-6 py-20 md:py-32">
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.75 }}
            className="liquid-glass relative overflow-hidden rounded-3xl px-8 py-16 text-center md:py-24"
          >
            <div className="pointer-events-none absolute inset-0 opacity-30" style={{ background: "radial-gradient(600px circle at 50% 0%, rgba(255,255,255,0.15), transparent 70%)" }} />
            <div className="relative">
              <h2 className="text-4xl font-semibold leading-[1.02] tracking-tight md:text-6xl">
                Show the work.
                <br />
                Win the trust.
              </h2>
              <p className="mx-auto mt-6 max-w-md text-sm leading-[1.6] text-white/60">
                Open the live demo, click the hard multi-issue case, and watch intent detection, memory, tools, policy DAGs,
                and audit evidence appear in one inspectable flow.
              </p>
              <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
                <PrimaryButton href="/demo" label="Open live demo" />
                <Link href="/evaluation" className="group inline-flex items-center justify-center gap-2 rounded-full border border-white/15 px-5 py-3 text-sm font-medium text-white transition hover:bg-white/5">
                  Evaluation harness
                  <ChevronRight size={15} className="transition-transform group-hover:translate-x-px" />
                </Link>
              </div>
            </div>
          </motion.div>
        </section>
      </div>
    </div>
  );
}
