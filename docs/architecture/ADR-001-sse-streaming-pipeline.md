# ADR-001: Server-Sent Events (SSE) for Streaming Chat

**Status:** Accepted
**Date:** 2026-05-20
**Deciders:** Project team

---

## Context

The chat endpoint must stream intermediate pipeline events (intent classification, memory retrieval, tool results, the final response) to the browser in real time so the operator can see the reasoning panel populate as the agent works.

Three options were considered: HTTP polling, WebSockets, and Server-Sent Events.

---

## Decision

Use **SSE (`StreamingResponse` with `text/event-stream`)** for the chat inference endpoint (`POST /api/chat/{customer_id}`).

---

## Options Considered

### Option A: HTTP Long Polling
| Dimension | Assessment |
|---|---|
| Complexity | Low (familiar pattern) |
| Latency | High (round-trip per event) |
| Infrastructure | Standard HTTP |
| Browser support | Universal |

**Pros:** No special server infrastructure; easy to test with curl.
**Cons:** Adds one round-trip of latency per event; server must buffer events between polls; complex retry/ordering logic.

### Option B: WebSockets
| Dimension | Assessment |
|---|---|
| Complexity | High |
| Latency | Lowest (full-duplex) |
| Infrastructure | Requires WS upgrade support |
| Browser support | Universal (but proxies can interfere) |

**Pros:** Full-duplex; lowest latency; supports bidirectional events.
**Cons:** Full-duplex is unnecessary (server→client only for this feature); requires WS-aware reverse proxies; connection lifecycle management (reconnect, heartbeat) adds complexity; harder to debug (can't use browser Network tab easily).

### Option C: Server-Sent Events (SSE) ← **Chosen**
| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Latency | Low (server-push, no round-trips) |
| Infrastructure | Plain HTTP/1.1 |
| Browser support | Universal (built-in `EventSource` API) |

**Pros:**
- Unidirectional push matches the data flow exactly (server produces, client consumes)
- Standard HTTP — no proxy configuration, no upgrade handshake
- Browser `EventSource` handles automatic reconnect
- Trivially debuggable in the Network tab
- FastAPI `StreamingResponse` supports it natively
- Each event is a typed JSON payload (`data: {...}\n\n`)

**Cons:**
- Unidirectional only — if the client needs to cancel mid-stream it must send a separate DELETE request (acceptable)
- HTTP/1.1 connection limit per origin (not a concern for a demo with one operator)

---

## Trade-off Analysis

The data flow is strictly server→client: the agent pipeline produces events and the browser renders them. WebSockets' full-duplex capability provides no benefit here and adds operational complexity. SSE is the correct primitive for unidirectional streaming over HTTP.

---

## Consequences

- The frontend uses the browser `EventSource` API to consume the stream and renders each event type (`intent`, `memory`, `tools`, `response`) into the appropriate panel.
- The backend emits typed JSON events in a structured format: `data: {"event": "...", "status": "...", "payload": {...}}\n\n`.
- Cancellation mid-stream is handled by the client closing the `EventSource` connection; the generator's `asyncio.CancelledError` tears down cleanly.
- All blocking I/O inside the async generator is wrapped with `asyncio.to_thread` to prevent event-loop stalls.

## Action Items
- [x] Implement `StreamingResponse` in `chat_routes.py`
- [x] Wire `EventSource` consumer in `frontend/src/app/test/page.tsx`
- [x] Ensure all `llm.generate` calls inside the generator use `asyncio.to_thread`
