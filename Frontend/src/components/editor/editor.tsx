"use client";

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { getWorkflow } from "@/lib/api";
import { useEditorStore } from "@/store/editor-store";
import { ChatPanel } from "./chat-panel";
import { NodePalette } from "./node-palette";
import { WorkflowCanvas } from "./workflow-canvas";
import { PropertyPanel } from "./property-panel";
import { Toolbar } from "./toolbar";
import { ResultDrawer } from "./result-drawer";

interface EditorProps {
  workflowId?: string; // when undefined or "new" → blank editor
}

export function Editor({ workflowId }: EditorProps) {
  const reset = useEditorStore((s) => s.reset);
  const loadFromWorkflow = useEditorStore((s) => s.loadFromWorkflow);
  const isExisting = workflowId && workflowId !== "new";

  const { data, isLoading, error } = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => getWorkflow(workflowId as string),
    enabled: Boolean(isExisting),
  });

  useEffect(() => {
    if (!isExisting) {
      reset();
    } else if (data) {
      loadFromWorkflow(data);
    }
  }, [isExisting, data, reset, loadFromWorkflow]);

  if (isExisting && isLoading) {
    return (
      <div className="h-screen flex items-center justify-center text-gray-500">
        Loading workflow…
      </div>
    );
  }

  if (isExisting && error) {
    return (
      <div className="h-screen flex items-center justify-center">
        <pre className="text-red-600 text-sm whitespace-pre-wrap">
          {error instanceof Error ? error.message : String(error)}
        </pre>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col">
      <Toolbar />
      <div className="flex-1 flex overflow-hidden relative">
        <NodePalette />
        <ChatPanel />
        <WorkflowCanvas />
        <PropertyPanel />
        <ResultDrawer />
      </div>
    </div>
  );
}
