"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  activatePersonalCandidate,
  listPersonalCandidates,
  rejectPersonalCandidate,
  sharePersonalCandidate,
  type PersonalCandidate,
} from "@/lib/personalization";

// "Your patterns" panel (PLAN_14 PR-H + marketplace-narrative UX overhaul).
//
// Surfaces pending HITL-personalization candidates AND active personal
// patterns the user can promote to the team marketplace. Visual framing
// parallels TeamMarketplace so the "personal → team" promotion is the
// obvious next step.
//
// Promotion flow:
//   1. Share click → ActivePersonalRow goes into "flying" state (CSS
//      transform: card translates up + fades).
//   2. After the animation duration (600ms) the row's onPromoted callback
//      lifts up to this section: invalidates queries (row leaves, new
//      workspace skill appears in marketplace) AND surfaces a brief
//      promotion banner so the cause→effect is explicit.
//   3. Parent's onSharedToTeam fires in parallel so TeamMarketplace
//      pulses its count chip at the same beat.
//
// 600ms is tuned for the demo recording: short enough that the take
// doesn't blow the 30s budget, long enough that the eye registers the
// motion.

const FLIGHT_MS = 600;
const PROMOTION_BANNER_MS = 2500;

interface Props {
  onSharedToTeam?: () => void;
}

export function SuggestedFromEdits({ onSharedToTeam }: Props = {}) {
  const queryClient = useQueryClient();
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

  // Banner survives the row's unmount so the user reads what happened
  // after the card flies away. Auto-clears after PROMOTION_BANNER_MS.
  const [promotedHint, setPromotedHint] = useState<string | null>(null);
  useEffect(() => {
    if (!promotedHint) return;
    const id = setTimeout(() => setPromotedHint(null), PROMOTION_BANNER_MS);
    return () => clearTimeout(id);
  }, [promotedHint]);

  // Split into "start" (optimistic banner + pulse — fires on click so
  // the viewer sees the promotion as a single intentional beat) and
  // "confirmed" (refetch — runs after the mutation + animation actually
  // complete). The split removes the race where a slow share API would
  // make the banner appear after the wait_for in record-demo.spec.ts
  // had already timed out.
  const handlePromotionStart = (hint: string) => {
    setPromotedHint(hint);
    onSharedToTeam?.();
  };

  const handlePromotionConfirmed = () => {
    queryClient.invalidateQueries({
      queryKey: ["personalization-candidates"],
      exact: false,
    });
    queryClient.invalidateQueries({ queryKey: ["skills"] });
    queryClient.invalidateQueries({ queryKey: ["skills", "active"] });
  };

  return (
    <section
      className="mb-6 rounded-lg border border-indigo-200 bg-indigo-50/40 p-5"
      data-testid="your-patterns"
    >
      <header className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-indigo-900">
            Your patterns
          </h2>
          <p className="mt-1 text-xs text-indigo-800/80">
            Drafts the system noticed when you edited recent AI workflows.
            Activate to teach the system for your own future drafts, then{" "}
            <span className="font-medium">Share with team</span> to promote
            into the marketplace above.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <span
            className="rounded-full bg-indigo-100 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-700"
            data-testid="suggested-count"
          >
            {pending.length} pending
          </span>
          <span
            className="rounded-full bg-indigo-100 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo-700"
            data-testid="active-personal-count"
          >
            {active.length} active
          </span>
        </div>
      </header>

      {promotedHint && (
        <div
          className="mb-4 flex items-start gap-2 rounded-md border-2 border-emerald-400 bg-emerald-100 px-4 py-3 text-sm text-emerald-900 shadow-lg shadow-emerald-200"
          data-testid="promotion-banner"
        >
          <span className="text-lg leading-none">↑</span>
          <div>
            <div className="font-semibold">
              Promoted to the team marketplace
            </div>
            <div className="mt-0.5 text-xs text-emerald-800/90">
              <span className="font-mono">
                &ldquo;{truncate(promotedHint, 80)}&rdquo;
              </span>{" "}
              is now part of your team&apos;s baseline — anyone drafting a
              workflow gets the same lift.
            </div>
          </div>
        </div>
      )}

      {/* Pending review lane */}
      <div className="mb-4" data-testid="suggested-pending-section">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-indigo-900/80">
          Suggested from your edits
        </h3>
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
            No new suggestions. Edit an AI-drafted workflow and save —
            we&apos;ll look for patterns automatically.
          </div>
        )}
        {pending.length > 0 && (
          <ul className="space-y-2" data-testid="suggested-list">
            {pending.map((c) => (
              <CandidateRow key={c.id} candidate={c} />
            ))}
          </ul>
        )}
      </div>

      {/* Active personal lane — promote to team marketplace */}
      {active.length > 0 && (
        <div data-testid="active-personal-section">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-indigo-900/80">
            Active patterns (only you)
          </h3>
          <ul className="space-y-2" data-testid="active-personal-list">
            {active.map((c) => (
              <ActivePersonalRow
                key={c.id}
                candidate={c}
                onPromotionStart={handlePromotionStart}
                onPromotionConfirmed={handlePromotionConfirmed}
              />
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
  // gains it on activate).
  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: ["personalization-candidates"],
      exact: false,
    });

  const activateMutation = useMutation({
    mutationFn: () => activatePersonalCandidate(candidate.id),
    onSuccess: () => {
      invalidate();
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
      <div className="mb-2 flex items-start gap-2">
        <span className="mt-0.5 rounded bg-indigo-600 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-white">
          Personal
        </span>
        <p
          className="text-sm text-gray-800"
          data-testid={`suggested-hint-${candidate.id}`}
        >
          {candidate.hint || "(empty hint)"}
        </p>
      </div>
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
              {rejectMutation.isPending ? "Rejecting…" : "Confirm reject"}
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

// Active personal row — promotes to team marketplace with a deliberate
// flight animation. Optimistic: the banner + pulse fire on click;
// the refetch waits for both the share API and the visual transition.
function ActivePersonalRow({
  candidate,
  onPromotionStart,
  onPromotionConfirmed,
}: {
  candidate: PersonalCandidate;
  onPromotionStart: (hint: string) => void;
  onPromotionConfirmed: () => void;
}) {
  const [flying, setFlying] = useState(false);

  const shareMutation = useMutation({
    mutationFn: () => sharePersonalCandidate(candidate.id),
  });

  const handleShare = async () => {
    setFlying(true);
    // Surface banner + marketplace pulse immediately — the actual API
    // call almost always succeeds, and decoupling the visual story from
    // the network round-trip is more important than the (small) risk
    // of showing a confirmation that later gets reverted.
    onPromotionStart(candidate.hint || "(empty hint)");
    try {
      await Promise.all([
        shareMutation.mutateAsync(),
        new Promise((resolve) => setTimeout(resolve, FLIGHT_MS)),
      ]);
      onPromotionConfirmed();
    } catch {
      setFlying(false);
    }
  };

  return (
    <li
      className="overflow-visible"
      data-testid={`active-personal-row-${candidate.id}`}
    >
      <div
        className="rounded border border-indigo-200 bg-white p-3 transition-all duration-500 ease-in-out data-[flying=true]:scale-90 data-[flying=true]:opacity-0 data-[flying=true]:border-emerald-400 data-[flying=true]:shadow-2xl data-[flying=true]:shadow-emerald-400"
        data-flying={flying}
      >
        <div className="mb-2 flex items-start gap-2">
          <span className="mt-0.5 rounded bg-indigo-600 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-white">
            Personal
          </span>
          <p
            className="text-sm text-gray-800"
            data-testid={`active-personal-hint-${candidate.id}`}
          >
            {candidate.hint || "(empty hint)"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={shareMutation.isPending || flying}
            onClick={handleShare}
            className="rounded bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700 disabled:bg-gray-300"
            data-testid={`active-personal-share-${candidate.id}`}
          >
            {shareMutation.isPending || flying
              ? "Promoting…"
              : "Share with team ↑"}
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
      </div>
    </li>
  );
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max - 1).trimEnd() + "…";
}
