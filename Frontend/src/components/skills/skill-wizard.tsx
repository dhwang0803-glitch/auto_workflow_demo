"use client";

import { useEffect, useRef, useState } from "react";
import {
  answerWizardQuestion,
  bootstrapSkills,
  DOMAIN_LABELS,
  type DomainCategory,
} from "@/lib/skills";
import { useSkillWizardStore } from "@/store/skill-wizard-store";
import { ApiError } from "@/lib/api";
import { SkillCard, makeReviewHandlers } from "./skill-card";

// Persona A interview wizard (PLAN_12 W2-5).
//
// Flow: user picks a domain chip → POST /skills/bootstrap returns a flat
// queue of (policy_id, question) pairs → for each turn the user types an
// answer → POST /skills/answer returns a SkillDraft. When the queue is
// empty we land on `done` and show a read-only summary. The W2-6 PR will
// replace the summary with the editable skill-card review UI.
//
// Drafts are persisted server-side as `pending_review` rows (skill ID
// returned in AnswerResponse), so a refresh mid-wizard loses chat state
// but not the skills already produced.

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
  const drafts = useSkillWizardStore((s) => s.drafts);
  const lastError = useSkillWizardStore((s) => s.lastError);
  const start = useSkillWizardStore((s) => s.start);
  const setLoading = useSkillWizardStore((s) => s.setLoading);
  const acceptBootstrap = useSkillWizardStore((s) => s.acceptBootstrap);
  const acceptAnswer = useSkillWizardStore((s) => s.acceptAnswer);
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

  const [input, setInput] = useState("");
  const transcriptRef = useRef<HTMLDivElement | null>(null);

  // Keep the transcript pinned to the bottom as new turns land.
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [currentIndex, drafts.length, phase]);

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

  const submitAnswer = async () => {
    if (!sessionId || !domain) return;
    const turn = queue[currentIndex];
    if (!turn) return;
    const text = input.trim();
    if (!text) return;
    setInput("");
    setLoading();
    try {
      const resp = await answerWizardQuestion({
        session_id: sessionId,
        domain,
        policy_id: turn.policyId,
        question: turn.question,
        answer: text,
      });
      acceptAnswer(resp, turn.policyId, turn.question, text);
    } catch (e) {
      // Restore the typed answer so the user doesn't have to retype it.
      setInput(text);
      setError(formatError(e));
    }
  };

  const onFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void submitAnswer();
  };

  const total = queue.length;
  const answered = drafts.length;
  const currentTurn = queue[currentIndex];

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
        className="flex-1 overflow-y-auto px-4 py-3 text-sm"
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

        {/* While the user is still answering, the prior turns stay as
          compact bubbles so the chat reads chronologically. The full
          SkillCard with review controls only appears in the `done`
          phase — until then, drafts are still accumulating and locking
          half of them behind approve/reject would be confusing. */}
        {(phase === "asking" || phase === "loading") &&
          drafts.map((d, i) => (
            <AnsweredTurn
              key={d.skillId}
              index={i + 1}
              question={d.question}
              answer={d.answer}
              draftName={d.draft.name}
              needsClarification={d.draft.needs_clarification}
            />
          ))}

        {phase === "asking" && currentTurn && (
          <AskingTurn
            policyName={currentTurn.policyName}
            question={currentTurn.question}
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
        <form
          onSubmit={onFormSubmit}
          className="flex gap-2 border-t p-3"
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onFormSubmit(e as unknown as React.FormEvent);
              }
            }}
            rows={2}
            placeholder="Type your answer…"
            className="flex-1 resize-none rounded border px-2 py-1 text-sm"
            data-testid="wizard-input"
          />
          <button
            type="submit"
            disabled={!input.trim()}
            className="rounded bg-blue-600 px-3 py-1 text-sm text-white disabled:bg-gray-300"
          >
            Send
          </button>
        </form>
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
          {answered} / {total} answered
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

function AskingTurn({
  policyName,
  question,
}: {
  policyName: string;
  question: string;
}) {
  return (
    <div className="mb-3" data-testid="wizard-current-question">
      <div className="mb-1 text-[11px] uppercase tracking-wide text-gray-500">
        {policyName}
      </div>
      <div className="max-w-[90%] whitespace-pre-wrap rounded-lg bg-gray-100 px-3 py-2 text-gray-900">
        {question}
      </div>
    </div>
  );
}

function AnsweredTurn({
  index,
  question,
  answer,
  draftName,
  needsClarification,
}: {
  index: number;
  question: string;
  answer: string;
  draftName: string;
  needsClarification: boolean;
}) {
  return (
    <div className="mb-3 space-y-1" data-testid={`wizard-turn-${index}`}>
      <div className="max-w-[90%] whitespace-pre-wrap rounded-lg bg-gray-100 px-3 py-1.5 text-gray-900">
        {question}
      </div>
      <div className="ml-auto max-w-[90%] whitespace-pre-wrap rounded-lg bg-blue-600 px-3 py-1.5 text-right text-white">
        {answer}
      </div>
      <div className="ml-2 text-[11px] text-gray-500">
        →{" "}
        <span className="font-mono">{draftName}</span>
        {needsClarification && (
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
