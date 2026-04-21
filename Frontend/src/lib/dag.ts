// Cycle detection for the editor's onConnect handler. Server-side
// `dag_validator` is the source of truth at save time; this is just UX
// feedback so users don't drop a self-loop and only learn at save.

interface MinimalEdge {
  source: string;
  target: string;
}

export function wouldCreateCycle(
  edges: MinimalEdge[],
  newSource: string,
  newTarget: string,
): boolean {
  if (newSource === newTarget) return true;
  // DFS from newTarget — if we reach newSource, the new edge closes a cycle.
  const adjacency = new Map<string, string[]>();
  for (const e of edges) {
    if (!adjacency.has(e.source)) adjacency.set(e.source, []);
    adjacency.get(e.source)!.push(e.target);
  }
  const stack = [newTarget];
  const seen = new Set<string>();
  while (stack.length) {
    const node = stack.pop()!;
    if (node === newSource) return true;
    if (seen.has(node)) continue;
    seen.add(node);
    const next = adjacency.get(node);
    if (next) stack.push(...next);
  }
  return false;
}
