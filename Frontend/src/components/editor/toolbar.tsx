"use client";

import { useMutation } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  createWorkflow,
  updateWorkflow,
  type WorkflowResponse,
} from "@/lib/api";
import { useEditorStore, useEditorTemporal } from "@/store/editor-store";
import { applyAutoLayout } from "@/lib/auto-layout";

export function Toolbar() {
  const router = useRouter();
  const {
    name,
    setName,
    dirty,
    lastSavedId,
    lastError,
    nodes,
    edges,
    replaceNodes,
    toPayload,
    markSaved,
    setError,
  } = useEditorStore();

  const temporal = useEditorTemporal();
  const undo = () => temporal.getState().undo();
  const redo = () => temporal.getState().redo();

  const saveMutation = useMutation({
    mutationFn: async (): Promise<WorkflowResponse> => {
      const payload = toPayload();
      if (lastSavedId) return updateWorkflow(lastSavedId, payload);
      return createWorkflow(payload);
    },
    onSuccess: (wf) => {
      markSaved(wf.id);
      setError(null);
      if (!lastSavedId) router.replace(`/workflows/${wf.id}`);
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  const onAutoLayout = () => {
    const laid = applyAutoLayout(nodes, edges);
    replaceNodes(laid);
  };

  const canSave = nodes.length > 0 && !saveMutation.isPending;

  return (
    <div className="flex items-center gap-2 border-b bg-white px-3 py-2">
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="text-sm font-medium border rounded px-2 py-1 w-64"
      />
      <button
        type="button"
        onClick={undo}
        className="text-xs border rounded px-2 py-1 hover:bg-gray-50"
        title="Undo (zundo)"
      >
        ↶ Undo
      </button>
      <button
        type="button"
        onClick={redo}
        className="text-xs border rounded px-2 py-1 hover:bg-gray-50"
        title="Redo"
      >
        ↷ Redo
      </button>
      <button
        type="button"
        onClick={onAutoLayout}
        className="text-xs border rounded px-2 py-1 hover:bg-gray-50"
      >
        Auto-Layout
      </button>
      <div className="flex-1" />
      <span className="text-xs text-gray-500">
        {dirty ? "Unsaved changes" : lastSavedId ? "Saved" : "Empty"}
      </span>
      <button
        type="button"
        disabled={!canSave}
        onClick={() => saveMutation.mutate()}
        className="text-sm bg-blue-600 text-white rounded px-3 py-1 disabled:bg-gray-300"
      >
        {saveMutation.isPending ? "Saving…" : "Save"}
      </button>
      {lastError && (
        <div
          role="alert"
          className="absolute top-12 right-3 max-w-md bg-red-50 border border-red-300 text-red-800 text-xs rounded px-3 py-2 shadow"
        >
          {lastError}
          <button
            type="button"
            onClick={() => setError(null)}
            className="ml-2 underline"
          >
            dismiss
          </button>
        </div>
      )}
    </div>
  );
}
