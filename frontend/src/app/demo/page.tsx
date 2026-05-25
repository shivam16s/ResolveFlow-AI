"use client";

import { LiveAgentConsole } from "../test/page";

export default function DemoPage() {
  return (
    <LiveAgentConsole
      title="Live Demo Chat"
      subtitle="Customer selector, live chat, tool calls, memory retrieval, policy DAG validation, and health updates from FastAPI."
    />
  );
}
