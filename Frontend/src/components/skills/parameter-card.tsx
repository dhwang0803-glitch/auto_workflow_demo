"use client";

import { useState } from "react";
import type { WizardQuestion } from "@/lib/skills";

// Single micro-question card (PLAN_12 W2-5b).
//
// Renders one parameter as: prompt + optional help_text expander +
// example_answer placeholder + a "Use baseline" button that one-shot
// fills the textarea with the seed default and surfaces the baseline
// source attribution.
//
// State is hoisted: the wizard owns the answer string for each
// parameter (keyed by parameter name), so this component is purely
// controlled. That keeps the batch submit a single map lookup over
// store.currentAnswers.
export function ParameterCard({
  question,
  value,
  onChange,
}: {
  question: WizardQuestion;
  value: string;
  onChange: (next: string) => void;
}) {
  const parameter = question.parameter ?? "";
  const [helpOpen, setHelpOpen] = useState(false);

  const useBaseline = () => {
    if (!question.default_baseline) return;
    onChange(question.default_baseline);
  };

  return (
    <div
      className="mb-3 rounded border border-gray-200 bg-white p-3"
      data-testid={`parameter-card-${parameter || question.text}`}
    >
      <div className="mb-1 flex items-baseline justify-between gap-2">
        {parameter && (
          <span className="font-mono text-[10px] uppercase tracking-wide text-gray-500">
            {parameter}
          </span>
        )}
        {question.help_text && (
          <button
            type="button"
            onClick={() => setHelpOpen((v) => !v)}
            className="text-[11px] text-blue-600 hover:underline"
            data-testid={`help-toggle-${parameter}`}
            aria-expanded={helpOpen}
          >
            {helpOpen ? "Hide help" : "What is this?"}
          </button>
        )}
      </div>

      <div className="mb-2 whitespace-pre-wrap text-sm text-gray-900">
        {question.text}
      </div>

      {helpOpen && question.help_text && (
        <div
          className="mb-2 rounded bg-blue-50 px-2 py-1.5 text-xs text-blue-900"
          data-testid={`help-text-${parameter}`}
        >
          {question.help_text}
        </div>
      )}

      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={2}
        placeholder={question.example_answer || "Type your answer…"}
        className="w-full resize-none rounded border border-gray-300 px-2 py-1 text-sm focus:border-blue-400 focus:outline-none"
        data-testid={`answer-input-${parameter}`}
      />

      {question.default_baseline && (
        <div className="mt-2 flex flex-wrap items-start gap-2">
          <button
            type="button"
            onClick={useBaseline}
            className="shrink-0 rounded border border-blue-300 bg-blue-50 px-2 py-1 text-xs text-blue-700 hover:bg-blue-100"
            data-testid={`use-baseline-${parameter}`}
          >
            Use baseline: {question.default_baseline}
          </button>
          {question.baseline_source && (
            <span
              className="text-[11px] leading-relaxed text-gray-500"
              data-testid={`baseline-source-${parameter}`}
            >
              {question.baseline_source}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
