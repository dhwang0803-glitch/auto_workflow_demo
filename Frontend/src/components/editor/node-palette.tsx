"use client";

import { useQuery } from "@tanstack/react-query";
import {
  fetchNodeCatalog,
  type NodeCatalogEntry,
  type NodeCatalogResponse,
} from "@/lib/api";

const DRAG_MIME = "application/x-auto-workflow-node";

export interface DraggedNode {
  type: string;
  display_name: string;
  config_schema: Record<string, unknown>;
}

export function NodePalette() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["node-catalog"],
    queryFn: fetchNodeCatalog,
  });

  return (
    <aside className="w-64 border-r bg-gray-50 p-3 overflow-y-auto h-full">
      <h2 className="text-sm font-semibold mb-3">Nodes</h2>
      {isLoading && <p className="text-xs text-gray-500">Loading…</p>}
      {error && (
        <pre className="text-xs text-red-600 whitespace-pre-wrap">
          {error instanceof Error ? error.message : String(error)}
        </pre>
      )}
      {data && <PaletteGrouped data={data} />}
    </aside>
  );
}

function PaletteGrouped({ data }: { data: NodeCatalogResponse }) {
  return (
    <div className="space-y-4">
      {data.categories.map((cat) => {
        const nodes = data.nodes.filter((n) => n.category === cat);
        return (
          <section key={cat}>
            <h3 className="text-xs uppercase tracking-wide text-gray-500 mb-1">
              {cat}
            </h3>
            <ul className="space-y-1">
              {nodes.map((n) => (
                <PaletteItem key={n.type} entry={n} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function PaletteItem({ entry }: { entry: NodeCatalogEntry }) {
  return (
    <li
      draggable
      onDragStart={(e) => {
        const payload: DraggedNode = {
          type: entry.type,
          display_name: entry.display_name,
          config_schema: entry.config_schema,
        };
        e.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload));
        e.dataTransfer.effectAllowed = "copy";
      }}
      className="px-2 py-1.5 rounded border bg-white text-sm cursor-grab hover:bg-gray-100"
      title={entry.type}
    >
      {entry.display_name}
    </li>
  );
}

export const NODE_DRAG_MIME = DRAG_MIME;
