"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  answerWizardQuestions,
  bootstrapSkills,
  DOMAIN_LABELS,
  type DomainCategory,
  type ParameterAnswer,
} from "@/lib/skills";
import {
  useSkillWizardStore,
  type PolicyTurn,
  type WizardDraft,
} from "@/store/skill-wizard-store";
import { ApiError } from "@/lib/api";
import { ParameterCard } from "./parameter-card";
import { SkillCard, makeReviewHandlers } from "./skill-card";
import { SourceKindPill } from "./source-kind-pill";

// Persona A interview wizard (PLAN_12 W2-5 + W2-5b).
//
// Flow: user picks a domain chip → POST /skills/bootstrap returns one
// policy gap per missing standard policy → for each policy the user
// fills N parameter cards (with help_text / example_answer / Use
// baseline affordances) → POST /skills/answers batches the parameter
// answers into one SkillDraft. When the queue is empty we land on
// `done` and the W2-6 SkillCard list takes over for review.

const DOMAIN_OPTIONS: DomainCategory[] = [
  "ecommerce",
  "services",
  "consulting",
  "content",
  "nonprofit",
  "other",
];

const newSessionId = () => crypto.randomUUID();

export function SkillWizard() {
  const phase = useSkillWizardStore((s) => s.phase);
  const sessionId = useSkillWizardStore((s) => s.sessionId);
  const domain = useSkillWizardStore((s) => s.domain);
  const queue = useSkillWizardStore((s) => s.queue);
  const currentIndex = useSkillWizardStore((s) => s.currentIndex);
  const currentAnswers = useSkillWizardStore((s) => s.currentAnswers);
  const drafts = useSkillWizardStore((s) => s.drafts);
  const lastError = useSkillWizardStore((s) => s.lastError);
  const start = useSkillWizardStore((s) => s.start);
  const setLoading = useSkillWizardStore((s) => s.setLoading);
  const acceptBootstrap = useSkillWizardStore((s) => s.acceptBootstrap);
  const setCurrentAnswer = useSkillWizardStore((s) => s.setCurrentAnswer);
  const acceptAnswers = useSkillWizardStore((s) => s.acceptAnswers);
  const setError = useSkillWizardStore((s) => s.setError);
  const setDraftActionStatus = useSkillWizardStore(
    (s) => s.setDraftActionStatus,
  );
  const applyServerStatus = useSkillWizardStore((s) => s.applyServerStatus);
  const pushFollowUpQuestion = useSkillWizardStore(
    (s) => s.pushFollowUpQuestion,
  );
  const reset = useSkillWizardStore((s) => s.reset);

  const reviewHandlers = makeReviewHandlers({
    setDraftActionStatus,
    applyServerStatus,
  });

  const transcriptRef = useRef<HTMLDivElement | null>(null);

  // Keep the transcript pinned to the bottom as new turns land.
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [currentIndex, drafts.length, phase]);

  const currentTurn = queue[currentIndex];

  // The submit button is disabled until every parameter on the current
  // policy has a non-empty answer — batch is all-or-nothing per policy.
  const allAnswered = useMemo(() => {
    if (!currentTurn) return false;
    return currentTurn.parameters.every((p) => {
      const key = p.parameter ?? p.text;
      return (currentAnswers[key] ?? "").trim().length > 0;
    });
  }, [currentTurn, currentAnswers]);

  const onPickDomain = async (picked: DomainCategory) => {
    const sid = newSessionId();
    start(picked, sid);
    try {
      const resp = await bootstrapSkills({
        domain: picked,
        session_id: sid,
        extracted_skills: [],
      });
      acceptBootstrap(resp);
    } catch (e) {
      setError(formatError(e));
    }
  };

  const submitPolicyAnswers = async () => {
    if (!sessionId || !domain || !currentTurn) return;
    if (!allAnswered) return;
    const answers: ParameterAnswer[] = currentTurn.parameters.map((p) => {
      const key = p.parameter ?? p.text;
      return {
        // The server requires a non-empty parameter name; for follow-up
        // turns with no seed parameter we fall back to the prompt key.
        parameter: p.parameter ?? key,
        answer: (currentAnswers[key] ?? "").trim(),
      };
    });
    setLoading();
    try {
      const resp = await answerWizardQuestions({
        session_id: sessionId,
        domain,
        policy_id: currentTurn.policyId,
        answers,
      });
      acceptAnswers(resp, currentTurn, answers);
    } catch (e) {
      setError(formatError(e));
    }
  };

  const total = queue.length;
  const answered = drafts.length;

  return (
    <main
      className="mx-auto flex h-screen max-w-2xl flex-col bg-white"
      data-testid="skill-wizard"
    >
      <header className="flex items-center justify-between border-b px-4 py-3">
        <div>
          <h1 className="text-base font-semibold">Skill wizard</h1>
          <p className="text-xs text-gray-500">
            A few questions to capture your team&apos;s standard policies.
          </p>
        </div>
        {phase !== "domain" && (
          <button
            type="button"
            onClick={reset}
            className="text-xs text-gray-500 hover:text-gray-800"
            data-testid="wizard-reset"
          >
            Start over
          </button>
        )}
      </header>

      {phase !== "domain" && total > 0 && (
        <ProgressGauge answered={answered} total={total} />
      )}

      <div
        ref={transcriptRef}
        className="flex-1 overflow-y-auto bg-gray-50 px-4 py-3 text-sm"
        data-testid="wizard-transcript"
      >
        {phase === "domain" && (
          <DomainPicker
            options={DOMAIN_OPTIONS}
            onPick={onPickDomain}
          />
        )}

        {phase !== "domain" && domain && (
          <div className="mb-3 inline-block rounded-full bg-blue-50 px-3 py-1 text-xs text-blue-700">
            Domain: {DOMAIN_LABELS[domain]}
          </div>
        )}

        {/* Prior policy turns stay as compact summaries; the SkillCard
            with full review controls only appears in the `done` phase
            so the user isn't approving half a session's drafts mid-flow. */}
        {(phase === "asking" || phase === "loading") &&
          drafts.map((d, i) => (
            <AnsweredPolicyTurn key={d.skillId} index={i + 1} draft={d} />
          ))}

        {phase === "asking" && currentTurn && (
          <AskingPolicyTurn
            turn={currentTurn}
            currentAnswers={currentAnswers}
            onAnswerChange={setCurrentAnswer}
          />
        )}

        {phase === "loading" && (
          <p
            className="text-xs text-gray-500"
            data-testid="wizard-loading"
          >
            Working…
          </p>
        )}

        {phase === "done" && drafts.length > 0 && (
          <div data-testid="wizard-review">
            <DoneHeader count={drafts.length} />
            {drafts.map((d) => (
              <SkillCard
                key={d.skillId}
                draft={d}
                onApprove={reviewHandlers.approve}
                onReject={reviewHandlers.reject}
                onAskFollowUp={pushFollowUpQuestion}
              />
            ))}
          </div>
        )}

        {phase === "done" && total === 0 && <NoGapsBanner />}

        {phase === "error" && lastError && (
          <ErrorBanner
            message={lastError}
            onRetry={reset}
          />
        )}
      </div>

      {phase === "asking" && currentTurn && (
        <div className="border-t bg-white p-3">
          <button
            type="button"
            onClick={() => void submitPolicyAnswers()}
            disabled={!allAnswered}
            className="w-full rounded bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:bg-gray-300"
            data-testid="wizard-submit-policy"
          >
            {allAnswered
              ? "Submit policy answers"
              : `Answer all ${currentTurn.parameters.length} questions to continue`}
          </button>
        </div>
      )}
    </main>
  );
}

function ProgressGauge({
  answered,
  total,
}: {
  answered: number;
  total: number;
}) {
  const pct = total === 0 ? 0 : Math.round((answered / total) * 100);
  return (
    <div className="border-b px-4 py-2" data-testid="wizard-progress">
      <div className="mb-1 flex justify-between text-xs text-gray-600">
        <span>
          {answered} / {total} policies answered
        </span>
        <span>{pct}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded bg-gray-100">
        <div
          className="h-full bg-blue-500 transition-[width]"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function DomainPicker({
  options,
  onPick,
}: {
  options: DomainCategory[];
  onPick: (d: DomainCategory) => void;
}) {
  return (
    <div data-testid="domain-picker">
      <p className="mb-3 text-sm text-gray-700">
        Pick the domain that best describes your work. We compare your
        existing skills against the standard policies for that domain and
        only ask about what&apos;s missing.
      </p>
      <div className="flex flex-wrap gap-2">
        {options.map((opt) => (
          <button
            key={opt}
            type="button"
            onClick={() => onPick(opt)}
            className="rounded-full border bg-white px-3 py-1.5 text-sm hover:bg-blue-50 hover:border-blue-300"
            data-testid={`domain-chip-${opt}`}
          >
            {DOMAIN_LABELS[opt]}
          </button>
        ))}
      </div>
    </div>
  );
}

function AskingPolicyTurn({
  turn,
  currentAnswers,
  onAnswerChange,
}: {
  turn: PolicyTurn;
  currentAnswers: Record<string, string>;
  onAnswerChange: (parameter: string, answer: string) => void;
}) {
  return (
    <div className="mb-3" data-testid="wizard-current-policy">
      <div className="mb-1 text-[11px] uppercase tracking-wide text-gray-500">
        Policy {turn.policyId}
      </div>
      <div className="mb-2 text-sm font-semibold text-gray-900">
        {turn.policyName}
      </div>
      {turn.sourceKind && turn.sourceKind !== "synthesized" && (
        <div className="mb-3">
          <SourceKindPill
            kind={turn.sourceKind}
            sources={turn.sources}
          />
        </div>
      )}
      {turn.parameters.map((p) => {
        const key = p.parameter ?? p.text;
        return (
          <ParameterCard
            key={key}
            question={p}
            value={currentAnswers[key] ?? ""}
            onChange={(next) => onAnswerChange(key, next)}
          />
        );
      })}
    </div>
  );
}

function AnsweredPolicyTurn({
  index,
  draft,
}: {
  index: number;
  draft: WizardDraft;
}) {
  return (
    <div
      className="mb-3 rounded border border-gray-200 bg-white p-3"
      data-testid={`wizard-turn-${index}`}
    >
      <div className="mb-1 text-[11px] uppercase tracking-wide text-gray-500">
        {draft.policyName}
      </div>
      <div className="space-y-1 text-xs">
        {draft.answers.map((a) => (
          <div key={a.parameter} className="flex gap-2">
            <span className="font-mono text-gray-500">{a.parameter}</span>
            <span className="text-gray-900">{a.answer}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 text-[11px] text-gray-500">
        →{" "}
        <span className="font-mono">{draft.draft.name}</span>
        {draft.draft.needs_clarification && (
          <span
            className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-amber-800"
            data-testid="needs-clarification-badge"
          >
            Needs clarification
          </span>
        )}
      </div>
    </div>
  );
}

function DoneHeader({ count }: { count: number }) {
  return (
    <div
      className="mb-3 rounded border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800"
      data-testid="wizard-done"
    >
      {count} skill draft{count === 1 ? "" : "s"} ready for review. Approve
      to activate, reject to discard, or expand a clarification prompt to
      refine the answer.
    </div>
  );
}

function NoGapsBanner() {
  return (
    <div
      className="rounded border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700"
      data-testid="wizard-no-gaps"
    >
      No standard-policy gaps to fill for this domain. Nothing to ask.
    </div>
  );
}

function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div
      className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
      data-testid="wizard-error"
    >
      <div className="mb-2 whitespace-pre-wrap">{message}</div>
      <button
        type="button"
        onClick={onRetry}
        className="rounded bg-red-600 px-2 py-1 text-xs text-white"
      >
        Start over
      </button>
    </div>
  );
}

function formatError(e: unknown): string {
  if (e instanceof ApiError) return `HTTP ${e.status}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
