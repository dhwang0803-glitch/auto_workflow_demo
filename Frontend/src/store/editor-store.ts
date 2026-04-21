import { create } from "zustand";
import { temporal } from "zundo";
import {
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "reactflow";
import type {
  WorkflowGraph,
  WorkflowPayload,
  WorkflowResponse,
} from "@/lib/api";
import { wouldCreateCycle } from "@/lib/dag";

export interface EditorNodeData {
  nodeType: string;
  displayName: string;
  config: Record<string, unknown>;
}

export type EditorNode = Node<EditorNodeData>;

interface EditorState {
  name: string;
  nodes: EditorNode[];
  edges: Edge[];
  selectedNodeId: string | null;
  // True when the editor diverges from the last loaded/saved state — used to
  // gate the Save button and warn on navigation away.
  dirty: boolean;
  lastSavedId: string | null;
  lastError: string | null;

  setName: (name: string) => void;
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (conn: Connection) => void;
  addNode: (
    nodeType: string,
    displayName: string,
    position: { x: number; y: number },
    configDefaults?: Record<string, unknown>,
  ) => void;
  updateNodeConfig: (id: string, config: Record<string, unknown>) => void;
  removeSelected: () => void;
  selectNode: (id: string | null) => void;
  replaceNodes: (nodes: EditorNode[]) => void;
  loadFromWorkflow: (wf: WorkflowResponse) => void;
  reset: () => void;
  toPayload: () => WorkflowPayload;
  setError: (msg: string | null) => void;
  markSaved: (id: string) => void;
}

const initialState = {
  name: "Untitled workflow",
  nodes: [] as EditorNode[],
  edges: [] as Edge[],
  selectedNodeId: null,
  dirty: false,
  lastSavedId: null,
  lastError: null,
};

let nodeCounter = 0;
const newNodeId = (type: string) => `${type}_${Date.now()}_${nodeCounter++}`;

export const useEditorStore = create<EditorState>()(
  temporal(
    (set, get) => ({
      ...initialState,

      setName: (name) => set({ name, dirty: true }),

      onNodesChange: (changes) =>
        set({
          nodes: applyNodeChanges(changes, get().nodes) as EditorNode[],
          dirty: true,
        }),

      onEdgesChange: (changes) =>
        set({ edges: applyEdgeChanges(changes, get().edges), dirty: true }),

      onConnect: (conn) => {
        if (!conn.source || !conn.target) return;
        const edges = get().edges;
        if (wouldCreateCycle(edges, conn.source, conn.target)) {
          set({ lastError: "Cannot connect — would create a cycle" });
          return;
        }
        const newEdge: Edge = {
          id: `e_${conn.source}_${conn.target}_${Date.now()}`,
          source: conn.source,
          target: conn.target,
        };
        set({ edges: [...edges, newEdge], dirty: true, lastError: null });
      },

      addNode: (nodeType, displayName, position, configDefaults = {}) => {
        const node: EditorNode = {
          id: newNodeId(nodeType),
          type: "custom",
          position,
          data: { nodeType, displayName, config: configDefaults },
        };
        set({ nodes: [...get().nodes, node], dirty: true });
      },

      updateNodeConfig: (id, config) =>
        set({
          nodes: get().nodes.map((n) =>
            n.id === id ? { ...n, data: { ...n.data, config } } : n,
          ),
          dirty: true,
        }),

      removeSelected: () => {
        const id = get().selectedNodeId;
        if (!id) return;
        set({
          nodes: get().nodes.filter((n) => n.id !== id),
          edges: get().edges.filter((e) => e.source !== id && e.target !== id),
          selectedNodeId: null,
          dirty: true,
        });
      },

      selectNode: (id) => set({ selectedNodeId: id }),

      replaceNodes: (nodes) => set({ nodes, dirty: true }),

      loadFromWorkflow: (wf) => {
        const layout = (wf.settings?.layout ?? {}) as Record<
          string,
          { x: number; y: number }
        >;
        const nodes: EditorNode[] = wf.graph.nodes.map((n, i) => ({
          id: n.id,
          type: "custom",
          position: layout[n.id] ?? { x: 100 + i * 240, y: 100 },
          data: {
            nodeType: n.type,
            displayName: n.type,
            config: n.config ?? {},
          },
        }));
        const edges: Edge[] = wf.graph.edges.map((e, i) => ({
          id: `e_${e.source}_${e.target}_${i}`,
          source: e.source,
          target: e.target,
        }));
        set({
          name: wf.name,
          nodes,
          edges,
          selectedNodeId: null,
          dirty: false,
          lastSavedId: wf.id,
          lastError: null,
        });
      },

      reset: () => set({ ...initialState }),

      toPayload: () => {
        const { name, nodes, edges } = get();
        const graph: WorkflowGraph = {
          nodes: nodes.map((n) => ({
            id: n.id,
            type: n.data.nodeType,
            config: n.data.config,
          })),
          edges: edges.map((e) => ({ source: e.source, target: e.target })),
        };
        const layout: Record<string, { x: number; y: number }> = {};
        for (const n of nodes) layout[n.id] = n.position;
        return { name, settings: { layout }, graph };
      },

      setError: (lastError) => set({ lastError }),

      markSaved: (id) => set({ dirty: false, lastSavedId: id }),
    }),
    {
      // Only nodes/edges/name are undo/redo-able. Selection, dirty, errors
      // are transient UI state and shouldn't fight the keystrokes.
      partialize: (state) => ({
        name: state.name,
        nodes: state.nodes,
        edges: state.edges,
      }),
      limit: 50,
    },
  ),
);

export const useEditorTemporal = () => useEditorStore.temporal;
