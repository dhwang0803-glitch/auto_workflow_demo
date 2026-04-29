"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  listSkills,
  type SkillRecord,
  type SkillStatus,
} from "@/lib/skills";
import { SourceKindPill } from "./source-kind-pill";

// Skill library view (PLAN_12 W2-9 + source round-trip).
//
// Surfaces the team's persisted policy library so a user (and a hackathon
// judge) can see the active rules at a glance without going through the
// wizard. Active by default, with a status filter for pending_review /
// rejected / archived. Each row shows the source-kind pill if the
// skill carries provenance — hydrated from skill_sources.source_ref via
// PR γ + α.

const STATUS_TABS: { value: SkillStatus; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "pending_review", label: "Pending review" },
  { value: "rejected", label: "Rejected" },
  { value: "archived", label: "Archived" },
];

export function SkillsLibrary({
  initialStatus = "active",
}: {
  initialStatus?: SkillStatus;
}) {
  const [status, setStatus] = useState<SkillStatus>(initialStatus);
  const { data, isLoading, error } = useQuery({
    queryKey: ["skills", status],
    queryFn: () => listSkills(status),
  });

  return (
    <main className="mx-auto min-h-screen max-w-5xl p-8" data-testid="skills-library">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Skill library</h1>
          <p className="mt-1 text-sm text-gray-500">
            The team&apos;s active policy rules. New skills come in via the
            wizard and land here once approved.
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            href="/"
            className="rounded border px-3 py-1.5 text-sm hover:bg-gray-50"
            data-testid="link-back-home"
          >
            Workflows
          </Link>
          <Link
            href="/skills/new"
            className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
            data-testid="link-skill-wizard"
          >
            + New skill
          </Link>
        </div>
      </header>

      <div
        className="mb-4 flex flex-wrap gap-2"
        data-testid="library-status-tabs"
      >
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            onClick={() => setStatus(tab.value)}
            className={`rounded-full border px-3 py-1 text-xs ${
              status === tab.value
                ? "border-blue-500 bg-blue-50 text-blue-700"
                : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
            }`}
            data-testid={`library-tab-${tab.value}`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {isLoading && (
        <p className="text-sm text-gray-500" data-testid="library-loading">
          Loading…
        </p>
      )}

      {error && (
        <pre
          className="whitespace-pre-wrap text-sm text-red-600"
          data-testid="library-error"
        >
          {error instanceof Error ? error.message : String(error)}
        </pre>
      )}

      {data && data.skills.length === 0 && (
        <div
          className="rounded border border-gray-200 bg-gray-50 p-4 text-sm text-gray-600"
          data-testid="library-empty"
        >
          No {STATUS_TABS.find((t) => t.value === status)?.label.toLowerCase()}{" "}
          skills yet.{" "}
          {status === "active" && (
            <Link href="/skills/new" className="text-blue-600 underline">
              Run the wizard
            </Link>
          )}
        </div>
      )}

      {data && data.skills.length > 0 && (
        <ul className="space-y-3" data-testid="library-list">
          {data.skills.map((s) => (
            <SkillRow key={s.id} skill={s} />
          ))}
        </ul>
      )}
    </main>
  );
}

function SkillRow({ skill }: { skill: SkillRecord }) {
  const condition = extractText(skill.condition);
  const action = extractText(skill.action);
  return (
    <li
      className="rounded border border-gray-200 bg-white p-4"
      data-testid={`library-row-${skill.id}`}
    >
      <header className="mb-2 flex items-center justify-between">
        <h2 className="font-mono text-sm">{skill.name}</h2>
        <span
          className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] uppercase tracking-wide text-gray-700"
          data-testid={`library-status-${skill.id}`}
        >
          {skill.status}
        </span>
      </header>
      {skill.source_kind && (
        <div className="mb-2">
          <SourceKindPill
            kind={skill.source_kind}
            sources={skill.sources}
            testIdSuffix={skill.id}
          />
        </div>
      )}
      {skill.description && (
        <p className="mb-2 text-xs text-gray-600">{skill.description}</p>
      )}
      <Field label="CONDITION">{condition}</Field>
      <Field label="ACTION">{action}</Field>
      <p className="mt-2 text-[11px] text-gray-400">
        Updated {new Date(skill.updated_at).toLocaleString()}
      </p>
    </li>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-1.5 last:mb-0">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">
        {label}
      </div>
      <div className="whitespace-pre-wrap text-sm text-gray-800">
        {children}
      </div>
    </div>
  );
}

// `condition` and `action` arrive as JSONB dicts because ADR-022 §1
// leaves room for structured matchers. The wizard always wraps prose
// answers as `{"text": "..."}`, so we read `.text` defensively and fall
// back to a stringified blob for anything else (which a future structured
// matcher would render with a richer component anyway).
function extractText(value: Record<string, unknown>): string {
  if (typeof value.text === "string") return value.text;
  return JSON.stringify(value, null, 2);
}
