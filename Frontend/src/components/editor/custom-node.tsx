"use client";

import { Handle, Position, type NodeProps } from "reactflow";
import type { EditorNodeData } from "@/store/editor-store";

export function CustomNode({ data, selected }: NodeProps<EditorNodeData>) {
  return (
    <div
      className={`px-3 py-2 rounded-md border bg-white shadow-sm min-w-[180px] ${
        selected ? "border-blue-500 ring-2 ring-blue-200" : "border-gray-300"
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-gray-400" />
      <div className="text-sm font-medium">{data.displayName}</div>
      <code className="text-[10px] text-gray-500">{data.nodeType}</code>
      <Handle type="source" position={Position.Right} className="!bg-gray-400" />
    </div>
  );
}

export const nodeTypes = { custom: CustomNode };
