"use client";

import { useCallback, useRef } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  ReactFlowProvider,
  useReactFlow,
  type ReactFlowInstance,
} from "reactflow";
import "reactflow/dist/style.css";
import { useShallow } from "zustand/react/shallow";
import { useEditorStore } from "@/store/editor-store";
import { nodeTypes } from "./custom-node";
import { NODE_DRAG_MIME, type DraggedNode } from "./node-palette";

function buildDefaults(schema: Record<string, unknown>): Record<string, unknown> {
  // Pull `default` out of each property; ignore the rest. The PropertyPanel
  // drives full editing once a node is selected.
  const props = (schema?.properties ?? {}) as Record<string, { default?: unknown }>;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(props)) {
    if (v?.default !== undefined) out[k] = v.default;
  }
  return out;
}

function CanvasInner() {
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const rfInstanceRef = useRef<ReactFlowInstance | null>(null);
  const { screenToFlowPosition } = useReactFlow();

  const { nodes, edges, onNodesChange, onEdgesChange, onConnect, addNode, selectNode } =
    useEditorStore(
      useShallow((s) => ({
        nodes: s.nodes,
        edges: s.edges,
        onNodesChange: s.onNodesChange,
        onEdgesChange: s.onEdgesChange,
        onConnect: s.onConnect,
        addNode: s.addNode,
        selectNode: s.selectNode,
      })),
    );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData(NODE_DRAG_MIME);
      if (!raw) return;
      const dragged = JSON.parse(raw) as DraggedNode;
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      addNode(
        dragged.type,
        dragged.display_name,
        position,
        buildDefaults(dragged.config_schema),
      );
    },
    [addNode, screenToFlowPosition],
  );

  return (
    <div ref={reactFlowWrapper} className="flex-1 h-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onInit={(inst) => (rfInstanceRef.current = inst)}
        onNodeClick={(_e, n) => selectNode(n.id)}
        onPaneClick={() => selectNode(null)}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls />
        <MiniMap pannable zoomable className="!bg-gray-100" />
      </ReactFlow>
    </div>
  );
}

export function WorkflowCanvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}
