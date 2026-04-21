"use client";

import { useQuery } from "@tanstack/react-query";
import {
  getExecution,
  TERMINAL_STATUSES,
  type ExecutionResponse,
  type ExecutionStatus,
  type NodeResult,
} from "@/lib/api";
import { useEditorStore } from "@/store/editor-store";

const STATUS_STYLES: Record<ExecutionStatus, string> = {
  queued: "bg-gray-200 text-gray-700",
  running: "bg-blue-100 text-blue-700 animate-pulse",
  success: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
};

function StatusPill({ status }: { status: ExecutionStatus }) {
  return (
    <span
      className={`text-xs font-medium rounded px-2 py-0.5 ${STATUS_STYLES[status]}`}
    >
      {status}
    </span>
  );
}

function durationMs(ex: ExecutionResponse): number | null {
  if (!ex.started_at) return null;
  const end = ex.finished_at ? new Date(ex.finished_at) : new Date();
  return end.getTime() - new Date(ex.started_at).getTime();
}

function formatNodeResult(result: NodeResult | undefined): string {
  if (!result) return "—";
  if (Object.keys(result).length === 0) return "(no output)";
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

// Mirrors custom-node.tsx: Execution_Engine writes node_results[id] only on
// success, so per-node status must be derived from entry presence + overall.
function deriveNodeStatus(
  exec: ExecutionResponse,
  nodeId: string,
): ExecutionStatus {
  if (exec.node_results?.[nodeId]) return "success";
  if (exec.status === "failed") return "failed";
  return exec.status;
}

export function ResultDrawer() {
  const activeExecutionId = useEditorStore((s) => s.activeExecutionId);
  const setActiveExecutionId = useEditorStore((s) => s.setActiveExecutionId);
  const nodes = useEditorStore((s) => s.nodes);

  const { data, error, isLoading } = useQuery({
    queryKey: ["execution", activeExecutionId],
    queryFn: () => getExecution(activeExecutionId as string),
    enabled: Boolean(activeExecutionId),
    // Poll every 1s until terminal; React Query passes the latest data into the
    // refetchInterval callback, so we stop on success/failed automatically.
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status && TERMINAL_STATUSES.has(status) ? false : 1000;
    },
  });

  if (!activeExecutionId) return null;

  return (
    <aside
      role="complementary"
      aria-label="Execution result"
      className="absolute right-0 top-0 h-full w-96 border-l bg-white shadow-lg flex flex-col z-10"
    >
      <header className="border-b px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">Execution</h2>
          {data && <StatusPill status={data.status} />}
        </div>
        <button
          type="button"
          onClick={() => setActiveExecutionId(null)}
          className="text-xs text-gray-500 hover:text-gray-800"
          aria-label="Close result drawer"
        >
          ✕
        </button>
      </header>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {isLoading && <p className="text-xs text-gray-500">Loading…</p>}

        {error && (
          <pre className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2 whitespace-pre-wrap">
            {error instanceof Error ? error.message : String(error)}
          </pre>
        )}

        {data && (
          <>
            <dl className="text-xs text-gray-600 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="text-gray-400">id</dt>
              <dd className="font-mono break-all">{data.id}</dd>
              <dt className="text-gray-400">mode</dt>
              <dd>{data.execution_mode}</dd>
              {durationMs(data) !== null && (
                <>
                  <dt className="text-gray-400">elapsed</dt>
                  <dd>{Math.round((durationMs(data) ?? 0) / 100) / 10}s</dd>
                </>
              )}
            </dl>

            {data.error && (
              <pre className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2 whitespace-pre-wrap">
                {JSON.stringify(data.error, null, 2)}
              </pre>
            )}

            <section>
              <h3 className="text-xs font-semibold text-gray-700 mb-1">
                Per-node results
              </h3>
              <ul className="space-y-2">
                {nodes.map((n) => {
                  const result = data.node_results?.[n.id];
                  const nodeStatus = deriveNodeStatus(data, n.id);
                  return (
                    <li key={n.id} className="border rounded">
                      <div className="flex items-center justify-between px-2 py-1 bg-gray-50">
                        <span className="text-xs font-mono">{n.id}</span>
                        <StatusPill status={nodeStatus} />
                      </div>
                      <pre className="text-[11px] p-2 max-h-48 overflow-auto whitespace-pre-wrap break-all">
                        {formatNodeResult(result)}
                      </pre>
                    </li>
                  );
                })}
              </ul>
            </section>
          </>
        )}
      </div>
    </aside>
  );
}
