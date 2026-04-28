import { create } from "zustand";
import type {
  AnswerResponse,
  BootstrapResponse,
  DomainCategory,
  PolicyGap,
  SkillDraft,
  SkillStatus,
} from "@/lib/skills";

// Wizard phases drive which view the panel renders:
//   domain   — chip picker (no session yet)
//   loading  — bootstrap or answer in flight
//   asking   — questions[currentIndex] is awaiting an answer
//   done     — every gap question answered; show drafts summary
//   error    — terminal failure surface (user retries from "domain")
export type WizardPhase = "domain" | "loading" | "asking" | "done" | "error";

// One produced skill draft, paired with its source question for the
// review summary. `actionStatus` tracks the user's review decision
// optimistically so the SkillCard can show "Approving…" / "Approved"
// without a refetch round-trip. `followUpAsked` stays true after the
// user clicks the clarification button so the affordance hides.
export interface WizardDraft {
  skillId: string;
  draft: SkillDraft;
  policyId: string;
  question: string;
  answer: string;
  // 'pending'    — awaiting user decision (server status pending_review)
  // 'approving' / 'rejecting' — API call in flight (UI lock)
  // 'approved'   — server returned status active
  // 'rejected'   — server returned status rejected
  // 'failed'     — API call failed; UI shows error + lets user retry
  actionStatus:
    | "pending"
    | "approving"
    | "rejecting"
    | "approved"
    | "rejected"
    | "failed";
  actionError: string | null;
  followUpAsked: boolean;
}

interface WizardState {
  phase: WizardPhase;
  sessionId: string | null;
  domain: DomainCategory | null;
  // Flat queue of (policy_id, question) pairs derived from the
  // BootstrapResponse. We flatten gaps × questions up front so the panel
  // only needs a single index — gap-aware grouping is W2-6 territory.
  queue: { policyId: string; policyName: string; question: string }[];
  currentIndex: number;
  drafts: WizardDraft[];
  lastError: string | null;

  // Actions
  start: (domain: DomainCategory, sessionId: string) => void;
  setLoading: () => void;
  acceptBootstrap: (resp: BootstrapResponse) => void;
  acceptAnswer: (
    resp: AnswerResponse,
    policyId: string,
    question: string,
    answer: string,
  ) => void;
  setError: (msg: string) => void;
  // W2-6: per-draft review actions.
  setDraftActionStatus: (
    skillId: string,
    actionStatus: WizardDraft["actionStatus"],
    actionError?: string | null,
  ) => void;
  applyServerStatus: (skillId: string, status: SkillStatus) => void;
  pushFollowUpQuestion: (skillId: string) => void;
  reset: () => void;
}

const flattenGaps = (
  missing: PolicyGap[],
): WizardState["queue"] =>
  missing.flatMap((gap) =>
    gap.questions.map((q) => ({
      policyId: gap.policy_id,
      policyName: gap.policy_name,
      question: q.text,
    })),
  );

export const useSkillWizardStore = create<WizardState>()((set) => ({
  phase: "domain",
  sessionId: null,
  domain: null,
  queue: [],
  currentIndex: 0,
  drafts: [],
  lastError: null,

  start: (domain, sessionId) =>
    set({
      phase: "loading",
      domain,
      sessionId,
      queue: [],
      currentIndex: 0,
      drafts: [],
      lastError: null,
    }),

  setLoading: () => set({ phase: "loading", lastError: null }),

  acceptBootstrap: (resp) => {
    const queue = flattenGaps(resp.missing);
    set({
      phase: queue.length > 0 ? "asking" : "done",
      sessionId: resp.session_id,
      domain: resp.domain,
      queue,
      currentIndex: 0,
      drafts: [],
      lastError: null,
    });
  },

  acceptAnswer: (resp, policyId, question, answer) =>
    set((s) => {
      const drafts: WizardDraft[] = [
        ...s.drafts,
        {
          skillId: resp.skill_id,
          draft: resp.draft,
          policyId,
          question,
          answer,
          actionStatus: "pending",
          actionError: null,
          followUpAsked: false,
        },
      ];
      const nextIndex = s.currentIndex + 1;
      const more = nextIndex < s.queue.length;
      return {
        drafts,
        currentIndex: nextIndex,
        phase: more ? "asking" : "done",
        sessionId: resp.session_id,
        lastError: null,
      };
    }),

  setError: (lastError) => set({ phase: "error", lastError }),

  setDraftActionStatus: (skillId, actionStatus, actionError = null) =>
    set((s) => ({
      drafts: s.drafts.map((d) =>
        d.skillId === skillId ? { ...d, actionStatus, actionError } : d,
      ),
    })),

  applyServerStatus: (skillId, status) =>
    set((s) => ({
      drafts: s.drafts.map((d) =>
        d.skillId === skillId
          ? {
              ...d,
              actionStatus:
                status === "active"
                  ? "approved"
                  : status === "rejected"
                  ? "rejected"
                  : d.actionStatus,
              actionError: null,
            }
          : d,
      ),
    })),

  pushFollowUpQuestion: (skillId) =>
    set((s) => {
      const target = s.drafts.find((d) => d.skillId === skillId);
      if (!target) return {};
      const hint = target.draft.clarification_hint || target.question;
      // Drop a fresh turn at the current end of the queue and re-enter
      // asking. acceptAnswer's `nextIndex < queue.length` check handles
      // the re-done transition once the user submits an answer for it.
      return {
        queue: [
          ...s.queue,
          {
            policyId: target.policyId,
            policyName: `Follow-up: ${target.policyId}`,
            question: hint,
          },
        ],
        currentIndex: s.queue.length,
        phase: "asking",
        drafts: s.drafts.map((d) =>
          d.skillId === skillId ? { ...d, followUpAsked: true } : d,
        ),
      };
    }),

  reset: () =>
    set({
      phase: "domain",
      sessionId: null,
      domain: null,
      queue: [],
      currentIndex: 0,
      drafts: [],
      lastError: null,
    }),
}));
