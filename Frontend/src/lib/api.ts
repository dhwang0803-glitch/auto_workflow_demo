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
  return r.json() as Promise<T>;
}

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
