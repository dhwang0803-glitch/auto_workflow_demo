"use client";

import { Handle, Position, type NodeProps } from "reactflow";
import { useQuery } from "@tanstack/react-query";
import {
  getExecution,
  TERMINAL_STATUSES,
  type ExecutionStatus,
} from "@/lib/api";
import { useEditorStore, type EditorNodeData } from "@/store/editor-store";

const DOT_COLOR: Record<ExecutionStatus, string> = {
  queued: "bg-gray-400",
  running: "bg-blue-500 animate-pulse",
  success: "bg-emerald-500",
  failed: "bg-red-500",
};

function NodeStatusDot({ nodeId }: { nodeId: string }) {
  const activeExecutionId = useEditorStore((s) => s.activeExecutionId);
  // Subscribe to the same query the ResultDrawer uses so React Query
  // deduplicates the fetch; status updates ripple to every node on the canvas.
  const { data } = useQuery({
    queryKey: ["execution", activeExecutionId],
    queryFn: () => getExecution(activeExecutionId as string),
    enabled: Boolean(activeExecutionId),
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status && TERMINAL_STATUSES.has(status) ? false : 1000;
    },
  });

  const status = data?.node_results?.[nodeId]?.status;
  if (!status) return null;
  return (
    <span
      aria-label={`node status: ${status}`}
      className={`inline-block h-2 w-2 rounded-full ${DOT_COLOR[status]}`}
    />
  );
}

export function CustomNode({ id, data, selected }: NodeProps<EditorNodeData>) {
  return (
    <div
      className={`px-3 py-2 rounded-md border bg-white shadow-sm min-w-[180px] ${
        selected ? "border-blue-500 ring-2 ring-blue-200" : "border-gray-300"
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-gray-400" />
      <div className="flex items-center justify-between gap-2">
        <div className="text-sm font-medium">{data.displayName}</div>
        <NodeStatusDot nodeId={id} />
      </div>
      <code className="text-[10px] text-gray-500">{data.nodeType}</code>
      <Handle type="source" position={Position.Right} className="!bg-gray-400" />
    </div>
  );
}

export const nodeTypes = { custom: CustomNode };
