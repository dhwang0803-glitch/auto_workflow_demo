"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchNodeCatalog, type NodeCatalogEntry } from "@/lib/api";

export default function Home() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["node-catalog"],
    queryFn: fetchNodeCatalog,
  });

  return (
    <main className="min-h-screen p-8 max-w-5xl mx-auto">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold">auto_workflow</h1>
        <p className="text-sm text-gray-500 mt-1">
          Node catalog (PR A scaffolding · editor lands in PR B)
        </p>
      </header>

      {isLoading && <p className="text-gray-500">Loading catalog…</p>}
      {error && (
        <pre className="text-red-600 text-sm whitespace-pre-wrap">
          {error instanceof Error ? error.message : String(error)}
        </pre>
      )}
      {data && (
        <CatalogByCategory entries={data.nodes} categories={data.categories} />
      )}
    </main>
  );
}

function CatalogByCategory({
  entries,
  categories,
}: {
  entries: NodeCatalogEntry[];
  categories: string[];
}) {
  const grouped = categories.map((cat) => ({
    category: cat,
    nodes: entries.filter((e) => e.category === cat),
  }));
  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-600">
        {entries.length} nodes across {categories.length} categories
      </p>
      {grouped.map(({ category, nodes }) => (
        <section key={category}>
          <h2 className="text-lg font-medium mb-2 capitalize">{category}</h2>
          <ul className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {nodes.map((n) => (
              <li
                key={n.type}
                className="border rounded p-3 hover:bg-gray-50"
                title={n.description || n.type}
              >
                <div className="font-medium">{n.display_name}</div>
                <code className="text-xs text-gray-500">{n.type}</code>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
