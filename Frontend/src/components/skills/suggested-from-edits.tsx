"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  activatePersonalCandidate,
  listPersonalCandidates,
  rejectPersonalCandidate,
  sharePersonalCandidate,
  type PersonalCandidate,
} from "@/lib/personalization";

// "Suggested from your edits" panel (PLAN_14 PR-H).
//
// Surfaces pending HITL-personalization candidates harvested from the
// user's workflow edits. Each card carries the propose-stage hint and
// Activate / Reject affordances. Activation promotes the candidate
// into the active skill pool (visible in the workspace library);
// rejection archives it AND records a `personal_skill_reviews` row
// with the same suggestion_hash so the next edit cycle doesn't
// re-propose it.
//
// The "Why this exists" copy is intentional — the narrative is the
// product. Judges scanning the library page should understand the
// loop ("you edited a workflow → we noticed → you decide") without
// reading the README. Memory: feedback_hackathon_ui_english.md (UI
// text in English for Kaggle review).

export function SuggestedFromEdits() {
  const pendingQuery = useQuery({
    queryKey: ["personalization-candidates", "pending_review"],
    queryFn: () => listPersonalCandidates("pending_review"),
  });
  const activeQuery = useQuery({
    queryKey: ["personalization-candidates", "active"],
    queryFn: () => listPersonalCandidates("active"),
  });

  const pending = pendingQuery.data?.candidates ?? [];
  const active = activeQuery.data?.candidates ?? [];

  return (
    <section
      className="mb-6 rounded border border-indigo-200 bg-indigo-50 p-4"
      data-testid="suggested-from-edits"
    >
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-indigo-900">
            Suggested from your edits
          </h2>
          <p className="mt-1 text-xs text-indigo-800/80">
            Patterns we noticed when you edited recent AI drafts. Activate
            to teach the system; reject to silence the same hint; share to
            give the rest of the team the same lift.
          </p>
        </div>
        <span
          className="rounded-full bg-indigo-100 px-2 py-0.5 text-[10px] uppercase tracking-wide text-indigo-700"
          data-testid="suggested-count"
        >
          {pending.length} pending
        </span>
      </header>

      {pendingQuery.isLoading && (
        <p
          className="text-sm text-indigo-700"
          data-testid="suggested-loading"
        >
          Loading suggestions…
        </p>
      )}

      {pendingQuery.error && (
        <pre
          className="whitespace-pre-wrap text-sm text-red-600"
          data-testid="suggested-error"
        >
          {pendingQuery.error instanceof Error
            ? pendingQuery.error.message
            : String(pendingQuery.error)}
        </pre>
      )}

      {pendingQuery.data && pending.length === 0 && (
        <div
          className="text-sm text-indigo-800/70"
          data-testid="suggested-empty"
        >
          No new suggestions. Edit an AI-drafted workflow and save — we&apos;ll
          look for patterns automatically.
        </div>
      )}

      {pending.length > 0 && (
        <ul className="space-y-2" data-testid="suggested-list">
          {pending.map((c) => (
            <CandidateRow key={c.id} candidate={c} />
          ))}
        </ul>
      )}

      {/* PR-J — active personal-skill lane: share with the team */}
      {active.length > 0 && (
        <div className="mt-4" data-testid="active-personal-section">
          <header className="mb-2 flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-indigo-900">
                Patterns the system learned from you
              </h3>
              <p className="mt-1 text-xs text-indigo-800/80">
                Share with the team to add this to the workspace baseline —
                anyone drafting a workflow will get the same lift.
              </p>
            </div>
            <span
              className="rounded-full bg-indigo-100 px-2 py-0.5 text-[10px] uppercase tracking-wide text-indigo-700"
              data-testid="active-personal-count"
            >
              {active.length} active
            </span>
          </header>
          <ul className="space-y-2" data-testid="active-personal-list">
            {active.map((c) => (
              <ActivePersonalRow key={c.id} candidate={c} />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function CandidateRow({ candidate }: { candidate: PersonalCandidate }) {
  const queryClient = useQueryClient();
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectForm, setShowRejectForm] = useState(false);

  // Both lanes share the prefix so a single invalidate refreshes the
  // pending query (which loses this row) AND the active query (which
  // gains it on activate). React Query treats `["personalization-candidates"]`
  // as a prefix when the predicate uses `exact: false`.
  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: ["personalization-candidates"],
      exact: false,
    });

  const activateMutation = useMutation({
    mutationFn: () => activatePersonalCandidate(candidate.id),
    onSuccess: () => {
      invalidate();
      // The activated row also appears in the workspace library under
      // the user-scope filter (when we add that filter post-hackathon);
      // for now just refreshing the personalization list is enough.
      queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: () =>
      rejectPersonalCandidate(candidate.id, rejectReason || undefined),
    onSuccess: () => {
      setShowRejectForm(false);
      setRejectReason("");
      invalidate();
    },
  });

  const busy = activateMutation.isPending || rejectMutation.isPending;

  return (
    <li
      className="rounded border border-indigo-200 bg-white p-3"
      data-testid={`suggested-row-${candidate.id}`}
    >
      <p
        className="mb-2 text-sm text-gray-800"
        data-testid={`suggested-hint-${candidate.id}`}
      >
        {candidate.hint || "(empty hint)"}
      </p>
      {candidate.diff_signature && (
        <p
          className="mb-2 break-all font-mono text-[10px] text-gray-400"
          data-testid={`suggested-diff-${candidate.id}`}
          title="diff signature"
        >
          {candidate.diff_signature}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => activateMutation.mutate()}
          className="rounded bg-indigo-600 px-3 py-1 text-xs text-white hover:bg-indigo-700 disabled:bg-gray-300"
          data-testid={`suggested-activate-${candidate.id}`}
        >
          {activateMutation.isPending ? "Activating…" : "Activate"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => setShowRejectForm((v) => !v)}
          className="rounded border border-gray-300 px-3 py-1 text-xs text-gray-700 hover:bg-gray-50 disabled:bg-gray-100"
          data-testid={`suggested-reject-toggle-${candidate.id}`}
        >
          Reject
        </button>
        {(activateMutation.error || rejectMutation.error) && (
          <span
            className="text-[11px] text-red-600"
            data-testid={`suggested-error-${candidate.id}`}
          >
            {((activateMutation.error || rejectMutation.error) as Error)
              .message}
          </span>
        )}
      </div>
      {showRejectForm && (
        <div
          className="mt-2 flex flex-col gap-2"
          data-testid={`suggested-reject-form-${candidate.id}`}
        >
          <textarea
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            placeholder="Why are you rejecting? (optional — recorded for your audit log)"
            rows={2}
            className="rounded border border-gray-300 p-2 text-xs"
            data-testid={`suggested-reject-reason-${candidate.id}`}
          />
          <div className="flex gap-2">
            <button
              type="button"
              disabled={rejectMutation.isPending}
              onClick={() => rejectMutation.mutate()}
              className="rounded bg-red-600 px-3 py-1 text-xs text-white hover:bg-red-700 disabled:bg-gray-300"
              data-testid={`suggested-reject-confirm-${candidate.id}`}
            >
              {rejectMutation.isPending
                ? "Rejecting…"
                : "Confirm reject"}
            </button>
            <button
              type="button"
              onClick={() => {
                setShowRejectForm(false);
                setRejectReason("");
              }}
              className="rounded border border-gray-300 px-3 py-1 text-xs text-gray-700 hover:bg-gray-50"
              data-testid={`suggested-reject-cancel-${candidate.id}`}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

// PR-J — active personal-skill row with a single Share affordance.
// Activate / Reject have already happened by the time a row reaches
// here; the only remaining decision is whether to keep it private or
// promote it to the workspace pool so the rest of the team gets the
// same lift on their next compose request.
function ActivePersonalRow({ candidate }: { candidate: PersonalCandidate }) {
  const queryClient = useQueryClient();

  const shareMutation = useMutation({
    mutationFn: () => sharePersonalCandidate(candidate.id),
    onSuccess: () => {
      // Drops the row from the active personal lane (now workspace).
      // Invalidate the workspace skill listing too so the library
      // surface picks the new shared row up immediately.
      queryClient.invalidateQueries({
        queryKey: ["personalization-candidates"],
        exact: false,
      });
      queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });

  return (
    <li
      className="rounded border border-indigo-200 bg-white p-3"
      data-testid={`active-personal-row-${candidate.id}`}
    >
      <p
        className="mb-2 text-sm text-gray-800"
        data-testid={`active-personal-hint-${candidate.id}`}
      >
        {candidate.hint || "(empty hint)"}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={shareMutation.isPending}
          onClick={() => shareMutation.mutate()}
          className="rounded bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700 disabled:bg-gray-300"
          data-testid={`active-personal-share-${candidate.id}`}
        >
          {shareMutation.isPending ? "Sharing…" : "Share with team"}
        </button>
        {shareMutation.error && (
          <span
            className="text-[11px] text-red-600"
            data-testid={`active-personal-error-${candidate.id}`}
          >
            {(shareMutation.error as Error).message}
          </span>
        )}
      </div>
    </li>
  );
}
