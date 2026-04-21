// AI Composer client — mirrors API_Server/app/models/ai_composer.py.
//
// PR C covers the JSON-once (`stream=false`) path. PR D adds the SSE
// (`stream=true`) client on top, reusing the same types. The Zustand slice
// consumes `ComposeResult` without caring which transport produced it.
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
