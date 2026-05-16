"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  listSkills,
  type SkillRecord,
  type SkillStatus,
} from "@/lib/skills";
import { SourceKindPill } from "./source-kind-pill";
import { SuggestedFromEdits } from "./suggested-from-edits";

// Skill library view — restructured around the marketplace narrative.
//
// Two top-level sections so the "team's accumulated automation knowledge"
// story is visible at a glance:
//
//   1. Team marketplace (workspace-scope active skills). Empty-state hero
//      explains the loop when there's nothing here yet.
//   2. Your patterns (personal-scope, rendered by SuggestedFromEdits) —
//      pending candidates + active personal skills with [Share with team]
//      that promotes a row UP into the marketplace.
//
// The older single-list tabs (pending_review / rejected / archived) live
// in a collapsed "Other status" disclosure at the bottom for power users.
//
// ADR-023 (narrative invisibility) still holds for the compose path; this
// view is intentionally narrative-visible since it IS the marketplace.

const OTHER_STATUS_TABS: { value: SkillStatus; label: string }[] = [
  { value: "pending_review", label: "Pending review" },
  { value: "rejected", label: "Rejected" },
  { value: "archived", label: "Archived" },
];

export function SkillsLibrary() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["skills", "active"],
    queryFn: () => listSkills("active"),
  });

  const allActive = data?.skills ?? [];
  const workspaceActive = allActive.filter((s) => s.scope === "workspace");

  // Pulse signal — increment on every successful Share-to-team so the
  // marketplace count chip flashes and judges register the cause/effect.
  // Cleared after the CSS animation finishes (800ms).
  const [pulseNonce, setPulseNonce] = useState(0);
  const triggerPulse = () => setPulseNonce((n) => n + 1);

  return (
    <main
      className="mx-auto min-h-screen max-w-5xl p-8"
      data-testid="skills-library"
    >
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Skill library</h1>
          <p className="mt-1 text-sm text-gray-500">
            Your team&apos;s shared automation policies — and the patterns
            the system is learning from you.
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

      <TeamMarketplace
        skills={workspaceActive}
        loading={isLoading}
        error={error}
        pulseNonce={pulseNonce}
      />

      <SuggestedFromEdits onSharedToTeam={triggerPulse} />

      <OtherStatusDisclosure />
    </main>
  );
}

// ─── Team marketplace ────────────────────────────────────────────────

function TeamMarketplace({
  skills,
  loading,
  error,
  pulseNonce,
}: {
  skills: SkillRecord[];
  loading: boolean;
  error: unknown;
  pulseNonce: number;
}) {
  const [pulseActive, setPulseActive] = useState(false);
  const lastNonce = useRef(pulseNonce);
  useEffect(() => {
    if (pulseNonce !== lastNonce.current) {
      lastNonce.current = pulseNonce;
      setPulseActive(true);
      const id = setTimeout(() => setPulseActive(false), 800);
      return () => clearTimeout(id);
    }
  }, [pulseNonce]);

  return (
    <section
      className="mb-6 rounded-lg border border-emerald-200 bg-emerald-50/40 p-5"
      data-testid="team-marketplace"
    >
      <header className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-emerald-900">
            Team marketplace
          </h2>
          <p className="mt-1 text-xs text-emerald-800/80">
            Active policies anyone on your team will get on their next AI
            draft. Promote a personal pattern with{" "}
            <span className="font-medium">Share with team</span> to add to
            this list.
          </p>
        </div>
        <span
          className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 transition-all duration-300 data-[pulse=true]:scale-150 data-[pulse=true]:bg-emerald-400 data-[pulse=true]:text-white data-[pulse=true]:shadow-lg data-[pulse=true]:shadow-emerald-300"
          data-pulse={pulseActive}
          data-testid="marketplace-count"
        >
          {skills.length} active
        </span>
      </header>

      {loading && (
        <p
          className="text-sm text-emerald-800/70"
          data-testid="marketplace-loading"
        >
          Loading…
        </p>
      )}

      {Boolean(error) && (
        <pre
          className="whitespace-pre-wrap text-sm text-red-600"
          data-testid="marketplace-error"
        >
          {error instanceof Error ? error.message : String(error)}
        </pre>
      )}

      {!loading && !error && skills.length === 0 && (
        <MarketplaceEmptyState />
      )}

      {skills.length > 0 && (
        <ul className="space-y-3" data-testid="marketplace-list">
          {skills.map((s) => (
            <MarketplaceRow key={s.id} skill={s} />
          ))}
        </ul>
      )}
    </section>
  );
}

function MarketplaceEmptyState() {
  return (
    <div
      className="rounded border border-dashed border-emerald-300 bg-white/60 p-6 text-center"
      data-testid="marketplace-empty"
    >
      <p className="text-sm font-medium text-emerald-900">
        Nothing here yet.
      </p>
      <p className="mt-1 text-xs text-emerald-800/70">
        Run the wizard, or share an active personal pattern from{" "}
        <span className="font-medium">Your patterns</span> below.
      </p>
      <Link
        href="/skills/new"
        className="mt-3 inline-block rounded bg-emerald-600 px-3 py-1.5 text-xs text-white hover:bg-emerald-700"
        data-testid="marketplace-empty-cta"
      >
        Start with the wizard
      </Link>
    </div>
  );
}

function MarketplaceRow({ skill }: { skill: SkillRecord }) {
  const condition = extractText(skill.condition);
  const action = extractText(skill.action);
  return (
    <li
      className="rounded border border-emerald-200 bg-white p-4 shadow-sm"
      data-testid={`marketplace-row-${skill.id}`}
    >
      <header className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className="rounded bg-emerald-600 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-white"
            data-testid={`marketplace-badge-${skill.id}`}
          >
            Team policy
          </span>
          <h3 className="font-mono text-sm">{skill.name}</h3>
        </div>
        <span
          className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] uppercase tracking-wide text-emerald-700"
          data-testid={`marketplace-status-${skill.id}`}
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

// ─── Other status (pending_review / rejected / archived) ────────────

function OtherStatusDisclosure() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<SkillStatus>("pending_review");
  const { data, isLoading, error } = useQuery({
    queryKey: ["skills", status],
    queryFn: () => listSkills(status),
    enabled: open,
  });

  return (
    <details
      className="mt-2 rounded border border-gray-200 bg-gray-50 p-4"
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      data-testid="other-status-disclosure"
    >
      <summary className="cursor-pointer text-sm font-medium text-gray-700">
        Other status (pending / rejected / archived)
      </summary>
      <div className="mt-3">
        <div
          className="mb-3 flex flex-wrap gap-2"
          data-testid="other-status-tabs"
        >
          {OTHER_STATUS_TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              onClick={() => setStatus(tab.value)}
              className={`rounded-full border px-3 py-1 text-xs ${
                status === tab.value
                  ? "border-blue-500 bg-blue-50 text-blue-700"
                  : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
              }`}
              data-testid={`other-status-tab-${tab.value}`}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {isLoading && (
          <p
            className="text-sm text-gray-500"
            data-testid="other-status-loading"
          >
            Loading…
          </p>
        )}
        {error && (
          <pre
            className="whitespace-pre-wrap text-sm text-red-600"
            data-testid="other-status-error"
          >
            {error instanceof Error ? error.message : String(error)}
          </pre>
        )}
        {data && data.skills.length === 0 && (
          <div
            className="rounded border border-gray-200 bg-white p-4 text-sm text-gray-600"
            data-testid="other-status-empty"
          >
            No{" "}
            {OTHER_STATUS_TABS.find((t) => t.value === status)?.label.toLowerCase()}{" "}
            skills.
          </div>
        )}
        {data && data.skills.length > 0 && (
          <ul className="space-y-3" data-testid="other-status-list">
            {data.skills.map((s) => (
              <CompactRow key={s.id} skill={s} />
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}

function CompactRow({ skill }: { skill: SkillRecord }) {
  return (
    <li
      className="rounded border border-gray-200 bg-white p-3"
      data-testid={`other-status-row-${skill.id}`}
    >
      <header className="mb-1 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ScopeBadge scope={skill.scope} />
          <h3 className="font-mono text-xs">{skill.name}</h3>
        </div>
        <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] uppercase tracking-wide text-gray-700">
          {skill.status}
        </span>
      </header>
      {skill.description && (
        <p className="text-xs text-gray-600">{skill.description}</p>
      )}
    </li>
  );
}

function ScopeBadge({ scope }: { scope: string }) {
  if (scope === "workspace") {
    return (
      <span className="rounded bg-emerald-600 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-white">
        Team
      </span>
    );
  }
  return (
    <span className="rounded bg-indigo-600 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-white">
      Personal
    </span>
  );
}

// `condition` and `action` arrive as JSONB dicts. The wizard always wraps
// prose answers as `{"text": "..."}`, so we read `.text` defensively and
// fall back to a stringified blob for anything else.
function extractText(value: Record<string, unknown>): string {
  if (typeof value.text === "string") return value.text;
  return JSON.stringify(value, null, 2);
}
