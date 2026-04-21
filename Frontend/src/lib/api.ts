// Thin fetch wrapper. Same-origin in dev (next.config rewrites /api/* → FastAPI);
// prod consumers should set NEXT_PUBLIC_API_BASE_URL once we wire a real ingress.
const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
const TOKEN = process.env.NEXT_PUBLIC_DEV_TOKEN;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (TOKEN && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${TOKEN}`);
  }
  const r = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!r.ok) throw new ApiError(r.status, await r.text());
  if (r.status === 204) return undefined as T;
  return r.json() as Promise<T>;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

// ─── Node catalog ───────────────────────────────────────────────────────────

export interface NodeCatalogEntry {
  type: string;
  display_name: string;
  category: string;
  description: string;
  config_schema: Record<string, unknown>;
}

export interface NodeCatalogResponse {
  nodes: NodeCatalogEntry[];
  total: number;
  categories: string[];
}

export const fetchNodeCatalog = () =>
  apiFetch<NodeCatalogResponse>("/api/v1/nodes/catalog");

// ─── Workflow CRUD ──────────────────────────────────────────────────────────
// Mirrors API_Server/app/models/workflow.py. The API stores `graph` as
// {nodes:[{id,type,config}], edges:[{source,target}]} — node positions are
// not first-class and are persisted under `settings.layout` as {id: {x,y}}.

export interface NodeSpec {
  id: string;
  type: string;
  config: Record<string, unknown>;
}

export interface EdgeSpec {
  source: string;
  target: string;
}

export interface WorkflowGraph {
  nodes: NodeSpec[];
  edges: EdgeSpec[];
}

export interface WorkflowPayload {
  name: string;
  settings: Record<string, unknown>;
  graph: WorkflowGraph;
}

export interface WorkflowResponse {
  id: string;
  name: string;
  settings: Record<string, unknown>;
  graph: WorkflowGraph;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface WorkflowListResponse {
  items: WorkflowSummary[];
  total: number;
  limit: number;
  plan_tier: string;
  approaching_limit: boolean;
}

export const listWorkflows = () =>
  apiFetch<WorkflowListResponse>("/api/v1/workflows");

export const getWorkflow = (id: string) =>
  apiFetch<WorkflowResponse>(`/api/v1/workflows/${id}`);

export const createWorkflow = (body: WorkflowPayload) =>
  apiFetch<WorkflowResponse>("/api/v1/workflows", jsonInit("POST", body));

export const updateWorkflow = (id: string, body: WorkflowPayload) =>
  apiFetch<WorkflowResponse>(`/api/v1/workflows/${id}`, jsonInit("PUT", body));

export const deleteWorkflow = (id: string) =>
  apiFetch<void>(`/api/v1/workflows/${id}`, { method: "DELETE" });

// ─── Execution ──────────────────────────────────────────────────────────────
// Backend status values: "queued" | "running" | "success" | "failed".
// node_results is a free-form {nodeId: {status, output?, error?, ...}} dict —
// shape varies per node, so consumers must read fields defensively.

export type ExecutionStatus = "queued" | "running" | "success" | "failed";

export interface NodeResult {
  status?: ExecutionStatus;
  output?: unknown;
  error?: unknown;
  [k: string]: unknown;
}

export interface ExecutionResponse {
  id: string;
  workflow_id: string;
  status: ExecutionStatus;
  execution_mode: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
  error: Record<string, unknown> | null;
  node_results: Record<string, NodeResult> | null;
}

export const TERMINAL_STATUSES: ReadonlySet<ExecutionStatus> = new Set<ExecutionStatus>(
  ["success", "failed"],
);

export const executeWorkflow = (id: string) =>
  apiFetch<ExecutionResponse>(
    `/api/v1/workflows/${id}/execute`,
    { method: "POST" },
  );

export const getExecution = (id: string) =>
  apiFetch<ExecutionResponse>(`/api/v1/executions/${id}`);
