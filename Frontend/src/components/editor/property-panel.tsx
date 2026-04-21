"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchNodeCatalog } from "@/lib/api";
import { useEditorStore } from "@/store/editor-store";
import { PropertyForm } from "./property-form";

export function PropertyPanel() {
  const { selectedNodeId, nodes, updateNodeConfig, removeSelected } = useEditorStore();
  const selected = nodes.find((n) => n.id === selectedNodeId);

  const { data: catalog } = useQuery({
    queryKey: ["node-catalog"],
    queryFn: fetchNodeCatalog,
  });
  const schema =
    selected && catalog
      ? (catalog.nodes.find((n) => n.type === selected.data.nodeType)
          ?.config_schema ?? {})
      : {};

  return (
    <aside className="w-80 border-l bg-white p-3 overflow-y-auto h-full">
      <h2 className="text-sm font-semibold mb-3">Properties</h2>
      {!selected && (
        <p className="text-xs text-gray-500">Select a node to edit its config.</p>
      )}
      {selected && (
        <div className="space-y-3">
          <div>
            <p className="text-xs text-gray-500">Type</p>
            <p className="text-sm font-medium">{selected.data.displayName}</p>
            <code className="text-[10px] text-gray-500">{selected.data.nodeType}</code>
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-1">Node ID</p>
            <code className="text-[10px] block bg-gray-50 px-2 py-1 rounded">
              {selected.id}
            </code>
          </div>
          <div className="border-t pt-3">
            <PropertyForm
              schema={schema as Parameters<typeof PropertyForm>[0]["schema"]}
              value={selected.data.config}
              onChange={(next) => updateNodeConfig(selected.id, next)}
            />
          </div>
          <button
            type="button"
            onClick={removeSelected}
            className="w-full text-xs text-red-600 border border-red-200 rounded px-2 py-1 hover:bg-red-50"
          >
            Delete node
          </button>
        </div>
      )}
    </aside>
  );
}
