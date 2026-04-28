import { create } from "zustand";
import type {
  AnswerResponse,
  BootstrapResponse,
  DomainCategory,
  PolicyGap,
  SkillDraft,
} from "@/lib/skills";

// Wizard phases drive which view the panel renders:
//   domain   — chip picker (no session yet)
//   loading  — bootstrap or answer in flight
//   asking   — questions[currentIndex] is awaiting an answer
//   done     — every gap question answered; show drafts summary
//   error    — terminal failure surface (user retries from "domain")
export type WizardPhase = "domain" | "loading" | "asking" | "done" | "error";

// One produced skill draft, paired with its source question for the
// review summary. `parameter` is whatever the gap_analyze prompt tagged
// the slot with (free-form for now) — we surface it so W2-6's edit UI
// can match drafts back to the question that generated them.
export interface WizardDraft {
  skillId: string;
  draft: SkillDraft;
  policyId: string;
  question: string;
  answer: string;
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
      const drafts = [
        ...s.drafts,
        {
          skillId: resp.skill_id,
          draft: resp.draft,
          policyId,
          question,
          answer,
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
