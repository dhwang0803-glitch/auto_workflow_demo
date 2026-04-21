// AI Composer client — mirrors API_Server/app/models/ai_composer.py.
//
// Two transports share the same `ComposeResult`:
// - composeJSON  — POST /api/v1/ai/compose, single JSON response (PR C)
// - composeStream — POST /api/v1/ai/compose?stream=true, SSE frames (PR D)
//
// SSE wire format (matches app/routers/ai_composer.py):
//   event: session         data: {"session_id": "..."}
//   event: rationale_delta data: {"token": "..."}
//   event: result          data: {"session_id": "...", "result": {...}}
//   event: error           data: {"code": "...", "message": "..."}
import { apiFetch, type EdgeSpec, type NodeSpec } from "./api";

export type ComposeIntent = "clarify" | "draft" | "refine";

export interface ComposeProposedDag {
  nodes: NodeSpec[];
  edges: EdgeSpec[];
}

export interface ComposeDiffNodeChange {
  id: string;
  config: Record<string, unknown>;
}

export interface ComposeDiff {
  added_nodes: NodeSpec[];
  removed_node_ids: string[];
  modified_nodes: ComposeDiffNodeChange[];
}

export interface ComposeResult {
  intent: ComposeIntent;
  clarify_questions: string[] | null;
  proposed_dag: ComposeProposedDag | null;
  diff: ComposeDiff | null;
  rationale: string;
}

export interface ComposeResponse {
  session_id: string;
  result: ComposeResult;
}

export interface ComposeRequest {
  session_id?: string;
  message: string;
  current_dag?: ComposeProposedDag | null;
}

export const composeJSON = (req: ComposeRequest) =>
  apiFetch<ComposeResponse>("/api/v1/ai/compose", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });

// ─── SSE streaming ──────────────────────────────────────────────────────────

export interface ComposeStreamHandlers {
  onSession?: (sessionId: string) => void;
  onRationaleDelta?: (token: string) => void;
  onResult?: (result: ComposeResult, sessionId: string) => void;
  onError?: (code: string, message: string) => void;
}

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
const TOKEN = process.env.NEXT_PUBLIC_DEV_TOKEN;

// Streams /api/v1/ai/compose?stream=true and dispatches frames to handlers.
// Returns when the stream closes naturally; throws on transport errors or
// when the supplied AbortSignal fires (DOMException 'AbortError', which
// callers should treat as cancellation, not failure).
//
// We use fetch+ReadableStream rather than EventSource so we can attach the
// Bearer token in a request header. EventSource has no header API.
export async function composeStream(
  req: ComposeRequest,
  handlers: ComposeStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (TOKEN) headers.Authorization = `Bearer ${TOKEN}`;

  const resp = await fetch(`${BASE}/api/v1/ai/compose?stream=true`, {
    method: "POST",
    headers,
    body: JSON.stringify(req),
    signal,
  });

  if (!resp.ok || !resp.body) {
    // Pre-stream failures (auth, validation) come back as normal HTTP
    // errors. The body is plain JSON or text.
    const text = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status}: ${text || resp.statusText}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by "\n\n". A chunk may contain multiple
      // complete frames + a partial trailing one — keep the tail in buffer.
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        dispatchFrame(frame, handlers);
      }
    }
    // Flush a trailing frame that arrived without "\n\n" (e.g., proxy
    // closed mid-write). Tolerate by treating the leftover as one frame.
    if (buffer.trim()) dispatchFrame(buffer, handlers);
  } finally {
    reader.releaseLock();
  }
}

function dispatchFrame(frame: string, h: ComposeStreamHandlers): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    // Ignore comment lines (": keep-alive") and unknown fields.
  }
  if (dataLines.length === 0) return;
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch {
    return; // Malformed payload — drop the frame, keep the stream alive.
  }
  switch (event) {
    case "session":
      h.onSession?.(String(data.session_id));
      break;
    case "rationale_delta":
      h.onRationaleDelta?.(String(data.token ?? ""));
      break;
    case "result":
      h.onResult?.(
        data.result as ComposeResult,
        String(data.session_id),
      );
      break;
    case "error":
      h.onError?.(String(data.code), String(data.message));
      break;
  }
}
