import { create } from "zustand";
import type {
  AnswersResponse,
  BootstrapResponse,
  DomainCategory,
  ParameterAnswer,
  PolicyGap,
  PolicySource,
  SkillDraft,
  SkillStatus,
  SourceKind,
  WizardQuestion,
} from "@/lib/skills";

// Wizard phases drive which view the panel renders:
//   domain   — chip picker (no session yet)
//   loading  — bootstrap or answer in flight
//   asking   — queue[currentIndex] is the policy awaiting all parameter answers
//   done     — every policy answered; show drafts summary
//   error    — terminal failure surface (user retries from "domain")
export type WizardPhase = "domain" | "loading" | "asking" | "done" | "error";

// W2-5b: queue is now policy-grained, not flat-question-grained. Each
// turn is one policy with N parameter cards the user fills in together
// before submitting a single batch (`POST /skills/answers`).
export interface PolicyTurn {
  policyId: string;
  policyName: string;
  parameters: WizardQuestion[];
  sources: PolicySource[];
  sourceKind: SourceKind;
  // Set when the wizard pushes a follow-up turn from a clarification
  // hint — used to render a fallback prompt when the seed parameters
  // aren't reusable as-is.
  followUpQuestion?: string;
}

// One produced skill draft, paired with the per-parameter answers used
// to generate it. `actionStatus` tracks the user's review decision
// optimistically so the SkillCard can show "Approving…" / "Approved"
// without a refetch round-trip. `followUpAsked` stays true after the
// user clicks the clarification button so the affordance hides.
export interface WizardDraft {
  skillId: string;
  draft: SkillDraft;
  policyId: string;
  policyName: string;
  // W2-5b: per-parameter answer trail (instead of the old single
  // question/answer pair). The review summary renders these so the
  // user can see exactly what was sent.
  answers: ParameterAnswer[];
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
  // Policy-grained queue (W2-5b). One entry per gap; the user fills in
  // every parameter card on it before the batch submit.
  queue: PolicyTurn[];
  currentIndex: number;
  // Working answers for the current policy turn. Cleared on policy
  // advance (acceptAnswers) and on reset.
  currentAnswers: Record<string, string>;
  drafts: WizardDraft[];
  lastError: string | null;

  // Actions
  start: (domain: DomainCategory, sessionId: string) => void;
  setLoading: () => void;
  acceptBootstrap: (resp: BootstrapResponse) => void;
  setCurrentAnswer: (parameter: string, answer: string) => void;
  acceptAnswers: (
    resp: AnswersResponse,
    turn: PolicyTurn,
    answers: ParameterAnswer[],
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

// W2-5b: gaps stay grouped by policy so the wizard can render N
// parameter cards under one policy header. PolicyGap is normalised
// here — `parameters` wins, `questions` is the deprecated alias and is
// only used as a fallback for callers stuck on the pre-#143 shape.
const buildQueue = (missing: PolicyGap[]): PolicyTurn[] =>
  missing.map((gap) => ({
    policyId: gap.policy_id,
    policyName: gap.policy_name,
    parameters: gap.parameters?.length
      ? gap.parameters
      : (gap.questions ?? []),
    sources: gap.sources ?? [],
    sourceKind: gap.source_kind ?? "synthesized",
  }));

export const useSkillWizardStore = create<WizardState>()((set) => ({
  phase: "domain",
  sessionId: null,
  domain: null,
  queue: [],
  currentIndex: 0,
  currentAnswers: {},
  drafts: [],
  lastError: null,

  start: (domain, sessionId) =>
    set({
      phase: "loading",
      domain,
      sessionId,
      queue: [],
      currentIndex: 0,
      currentAnswers: {},
      drafts: [],
      lastError: null,
    }),

  setLoading: () => set({ phase: "loading", lastError: null }),

  acceptBootstrap: (resp) => {
    const queue = buildQueue(resp.missing);
    set({
      phase: queue.length > 0 ? "asking" : "done",
      sessionId: resp.session_id,
      domain: resp.domain,
      queue,
      currentIndex: 0,
      currentAnswers: {},
      drafts: [],
      lastError: null,
    });
  },

  setCurrentAnswer: (parameter, answer) =>
    set((s) => ({
      currentAnswers: { ...s.currentAnswers, [parameter]: answer },
    })),

  acceptAnswers: (resp, turn, answers) =>
    set((s) => {
      const drafts: WizardDraft[] = [
        ...s.drafts,
        {
          skillId: resp.skill_id,
          draft: resp.draft,
          policyId: turn.policyId,
          policyName: turn.policyName,
          answers,
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
        currentAnswers: {},
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
      const hint =
        target.draft.clarification_hint ||
        target.answers[0]?.parameter ||
        "Please clarify your previous answer.";
      // Drop a fresh policy turn at the end of the queue with a single
      // follow-up "parameter" so the existing parameter-card UI works
      // without a special-case branch. The server-side answers_to_skill
      // accepts a 1-element batch.
      const followUpTurn: PolicyTurn = {
        policyId: target.policyId,
        policyName: `Follow-up: ${target.policyName}`,
        parameters: [
          {
            text: hint,
            parameter: target.answers[0]?.parameter ?? "FOLLOW_UP",
            default_baseline: "",
            baseline_source: "",
            help_text: "",
            example_answer: "",
          },
        ],
        sources: [],
        sourceKind: "synthesized",
        followUpQuestion: hint,
      };
      return {
        queue: [...s.queue, followUpTurn],
        currentIndex: s.queue.length,
        currentAnswers: {},
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
      currentAnswers: {},
      drafts: [],
      lastError: null,
    }),
}));
