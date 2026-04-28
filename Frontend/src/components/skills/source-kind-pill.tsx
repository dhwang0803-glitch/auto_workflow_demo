"use client";

import type { PolicySource, SourceKind } from "@/lib/skills";

// Source-kind attribution pill (PLAN_12 W2-6b + W2-9).
//
// Honest labelling per memory `project_wizard_polish_abc.md`:
//   - regulatory       → real legal/regulatory grounding
//   - industry-baseline → linkable external industry references
//   - synthesized      → best-practice patchwork; no authoritative URL
//
// The pill lives both inside the wizard (mid-flow on the policy header)
// and on the SkillCard / library list — same component, same colour
// language, so a user who learns the meaning once carries it across
// surfaces. Synthesized policies render as a soft gray badge so they
// don't visually compete with regulatory/industry-baseline pills, but
// they are still surfaced (silence would imply an authoritative source).
export function SourceKindPill({
  kind,
  sources,
  testIdSuffix = "",
}: {
  kind: SourceKind;
  sources: PolicySource[];
  // Distinguishes the wizard mid-flow pill from the SkillCard pill in
  // Playwright tests so a single page can host both without selector
  // collisions.
  testIdSuffix?: string;
}) {
  const meta: Record<
    SourceKind,
    { label: string; className: string }
  > = {
    regulatory: {
      label: "Regulatory",
      className: "bg-purple-50 text-purple-800 border-purple-200",
    },
    "industry-baseline": {
      label: "Industry baseline",
      className: "bg-emerald-50 text-emerald-800 border-emerald-200",
    },
    synthesized: {
      label: "Synthesized baseline",
      className: "bg-gray-50 text-gray-700 border-gray-200",
    },
  };
  const { label, className } = meta[kind];
  const testId = testIdSuffix
    ? `source-kind-${kind}-${testIdSuffix}`
    : `source-kind-${kind}`;

  return (
    <div
      className={`inline-flex max-w-full flex-wrap items-center gap-2 rounded border px-2 py-1 text-[11px] ${className}`}
      data-testid={testId}
    >
      <span className="font-semibold uppercase tracking-wide">{label}</span>
      {sources.slice(0, 2).map((s) => (
        <a
          key={s.url}
          href={s.url}
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:no-underline"
        >
          {s.title}
        </a>
      ))}
    </div>
  );
}
