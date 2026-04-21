"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { listWorkflows } from "@/lib/api";

export function WorkflowsList() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["workflows"],
    queryFn: listWorkflows,
  });

  return (
    <main className="min-h-screen p-8 max-w-5xl mx-auto">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Workflows</h1>
          <p className="text-sm text-gray-500 mt-1">
            {data
              ? `${data.total} of ${data.limit} (${data.plan_tier})`
              : "Loading…"}
          </p>
        </div>
        <Link
          href="/workflows/new"
          className="text-sm bg-blue-600 text-white rounded px-3 py-1.5 hover:bg-blue-700"
        >
          + New workflow
        </Link>
      </header>

      {isLoading && <p className="text-gray-500 text-sm">Loading…</p>}
      {error && (
        <pre className="text-red-600 text-sm whitespace-pre-wrap">
          {error instanceof Error ? error.message : String(error)}
        </pre>
      )}
      {data && (
        <ul className="border rounded divide-y">
          {data.items.length === 0 && (
            <li className="p-4 text-sm text-gray-500">
              No workflows yet — start with the New button.
            </li>
          )}
          {data.items.map((wf) => (
            <li key={wf.id} className="p-3 flex items-center justify-between">
              <Link
                href={`/workflows/${wf.id}`}
                className="text-sm font-medium hover:underline"
              >
                {wf.name}
              </Link>
              <span className="text-xs text-gray-500">
                {wf.is_active ? "active" : "inactive"} ·{" "}
                {wf.updated_at
                  ? new Date(wf.updated_at).toLocaleString()
                  : "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
