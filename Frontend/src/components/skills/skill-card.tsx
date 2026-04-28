"use client";

import {
  approveSkill,
  rejectSkill,
  type SkillDraft,
} from "@/lib/skills";
import { ApiError } from "@/lib/api";
import type { WizardDraft } from "@/store/skill-wizard-store";

// Single skill review card (PLAN_12 W2-6).
//
// Renders a draft's CONDITION / ACTION / RATIONALE plus per-card review
// controls (Approve / Reject). When the LLM flagged the answer as
// `needs_clarification`, the card surfaces an amber border + the
// clarification hint and offers a "Answer follow-up" button that
// re-opens the wizard with the hint as the next question.
//
// Edit is deferred to a follow-up PR (requires PUT /skills/{id} on
// API_Server, which doesn't exist yet).
export function SkillCard({
  draft,
  onApprove,
  onReject,
  onAskFollowUp,
}: {
  draft: WizardDraft;
  onApprove: (skillId: string) => Promise<void> | void;
  onReject: (skillId: string) => Promise<void> | void;
  onAskFollowUp: (skillId: string) => void;
}) {
  const { skillId, draft: body, actionStatus, actionError, followUpAsked } =
    draft;
  const needsClarification = body.needs_clarification;
  const inFlight =
    actionStatus === "approving" || actionStatus === "rejecting";
  const settled =
    actionStatus === "approved" || actionStatus === "rejected";

  // Border encodes the most important visual state: amber for
  // clarification, emerald for approved, gray for rejected/default.
  const borderClass = settled
    ? actionStatus === "approved"
      ? "border-emerald-300 bg-emerald-50/40"
      : "border-gray-300 bg-gray-50"
    : needsClarification
    ? "border-amber-300 bg-amber-50/40"
    : "border-gray-200";

  return (
    <div
      className={`mb-3 rounded border ${borderClass} p-3`}
      data-testid={`skill-card-${skillId}`}
      data-action-status={actionStatus}
    >
      <header className="mb-2 flex items-center justify-between">
        <div className="font-mono text-sm">{body.name}</div>
        <StatusPill status={actionStatus} />
      </header>

      <Field label="CONDITION">{body.condition}</Field>
      <Field label="ACTION">{body.action}</Field>
      {body.rationale && <Field label="RATIONALE">{body.rationale}</Field>}

      {needsClarification && (
        <div
          className="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs text-amber-900"
          data-testid={`clarification-hint-${skillId}`}
        >
          <div className="font-semibold">Needs clarification</div>
          {body.clarification_hint && (
            <div className="mt-1 whitespace-pre-wrap">
              {body.clarification_hint}
            </div>
          )}
          {!followUpAsked && !settled && (
            <button
              type="button"
              onClick={() => onAskFollowUp(skillId)}
              className="mt-2 rounded bg-amber-600 px-2 py-1 text-xs text-white hover:bg-amber-700"
              data-testid={`follow-up-${skillId}`}
            >
              Answer follow-up
            </button>
          )}
          {followUpAsked && (
            <div className="mt-2 text-amber-700">
              Follow-up question added below.
            </div>
          )}
        </div>
      )}

      {actionError && (
        <div
          className="mt-2 rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700"
          data-testid={`action-error-${skillId}`}
        >
          {actionError}
        </div>
      )}

      {!settled && (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            disabled={inFlight}
            onClick={() => void onApprove(skillId)}
            className="rounded bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700 disabled:bg-gray-300"
            data-testid={`approve-${skillId}`}
          >
            {actionStatus === "approving" ? "Approving…" : "Approve"}
          </button>
          <button
            type="button"
            disabled={inFlight}
            onClick={() => void onReject(skillId)}
            className="rounded border border-gray-300 px-3 py-1 text-xs hover:bg-gray-100 disabled:bg-gray-100"
            data-testid={`reject-${skillId}`}
          >
            {actionStatus === "rejecting" ? "Rejecting…" : "Reject"}
          </button>
        </div>
      )}
    </div>
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

function StatusPill({
  status,
}: {
  status: WizardDraft["actionStatus"];
}) {
  const map: Record<
    WizardDraft["actionStatus"],
    { label: string; className: string }
  > = {
    pending: { label: "pending", className: "bg-gray-100 text-gray-700" },
    approving: {
      label: "approving",
      className: "bg-emerald-100 text-emerald-800",
    },
    rejecting: {
      label: "rejecting",
      className: "bg-gray-200 text-gray-700",
    },
    approved: {
      label: "approved",
      className: "bg-emerald-600 text-white",
    },
    rejected: {
      label: "rejected",
      className: "bg-gray-500 text-white",
    },
    failed: { label: "failed", className: "bg-red-100 text-red-700" },
  };
  const { label, className } = map[status];
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wide ${className}`}
    >
      {label}
    </span>
  );
}

// Helper for SkillWizard to wire approve/reject to the API + store. Kept
// here so the wizard component stays UI-only.
export function makeReviewHandlers({
  setDraftActionStatus,
  applyServerStatus,
}: {
  setDraftActionStatus: (
    skillId: string,
    s: WizardDraft["actionStatus"],
    err?: string | null,
  ) => void;
  applyServerStatus: (
    skillId: string,
    status: "active" | "rejected" | "pending_review" | "archived",
  ) => void;
}) {
  const run = async (
    skillId: string,
    inFlight: WizardDraft["actionStatus"],
    fn: () => Promise<{ status: string }>,
  ) => {
    setDraftActionStatus(skillId, inFlight, null);
    try {
      const resp = await fn();
      // Trust whatever status the server settled on (approve → active,
      // reject → rejected). Keeps the UI honest if the server applies a
      // policy override later.
      applyServerStatus(skillId, resp.status as never);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `HTTP ${e.status}: ${e.message}`
          : e instanceof Error
          ? e.message
          : String(e);
      setDraftActionStatus(skillId, "failed", msg);
    }
  };
  return {
    approve: (skillId: string) =>
      run(skillId, "approving", () => approveSkill(skillId)),
    reject: (skillId: string) =>
      run(skillId, "rejecting", () => rejectSkill(skillId)),
  };
}

// Re-exported here so consumers don't need a second import path when
// rendering a skill summary outside the wizard (future list view).
export type { SkillDraft };
