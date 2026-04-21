"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { composeJSON, type ComposeResult } from "@/lib/composer";
import { useComposerStore, type ChatMessage } from "@/store/composer-store";
import { useEditorStore } from "@/store/editor-store";

// PR C: non-stream chat. The user types, we POST, and the assistant bubble
// renders once the full `ComposeResult` comes back. PR D swaps the mutation
// for an SSE client that streams rationale tokens into an in-flight
// "typing" bubble before the same final bubble is appended.
//
// Layout: fixed 320px left panel, positioned inside the editor body between
// the NodePalette and the canvas. Toggle from the Toolbar via
// `useComposerStore.getState().setOpen(true)`.
export function ChatPanel() {
  const open = useComposerStore((s) => s.open);
  const setOpen = useComposerStore((s) => s.setOpen);
  const messages = useComposerStore((s) => s.messages);
  const pending = useComposerStore((s) => s.pending);
  const lastError = useComposerStore((s) => s.lastError);
  const pendingDraft = useComposerStore((s) => s.pendingDraft);
  const pendingIntent = useComposerStore((s) => s.pendingIntent);
  const sessionId = useComposerStore((s) => s.sessionId);
  const pushUser = useComposerStore((s) => s.pushUser);
  const pushAssistant = useComposerStore((s) => s.pushAssistant);
  const setSessionId = useComposerStore((s) => s.setSessionId);
  const setPending = useComposerStore((s) => s.setPending);
  const setError = useComposerStore((s) => s.setError);
  const clearPendingDraft = useComposerStore((s) => s.clearPendingDraft);

  const applyComposerDraft = useEditorStore((s) => s.applyComposerDraft);
  const toPayload = useEditorStore((s) => s.toPayload);

  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const compose = useMutation({
    mutationFn: async (text: string) => {
      const currentGraph = toPayload().graph;
      // Send `current_dag` only when the canvas is non-empty; otherwise the
      // backend would treat an empty graph as a refine target. The first
      // turn should be `intent=clarify` or `intent=draft`.
      const current_dag =
        currentGraph.nodes.length > 0 ? currentGraph : null;
      return composeJSON({
        session_id: sessionId ?? undefined,
        message: text,
        current_dag,
      });
    },
    onMutate: (text) => {
      pushUser(text);
      setPending(true);
      setError(null);
    },
    onSuccess: (resp) => {
      setSessionId(resp.session_id);
      pushAssistant(resp.result);
      setPending(false);
    },
    onError: (e) => {
      setError(e instanceof Error ? e.message : String(e));
      setPending(false);
    },
  });

  // Keep the scroll pinned to the bottom as messages arrive.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, pending]);

  if (!open) return null;

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || pending) return;
    setInput("");
    compose.mutate(text);
  };

  const onApply = () => {
    if (!pendingDraft) return;
    applyComposerDraft(pendingDraft);
    clearPendingDraft();
  };

  return (
    <aside
      role="complementary"
      aria-label="AI Composer chat"
      className="w-80 border-r bg-white flex flex-col h-full"
    >
      <header className="border-b px-3 py-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">AI Composer</h2>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-xs text-gray-500 hover:text-gray-800"
          aria-label="Close chat panel"
        >
          ✕
        </button>
      </header>

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-3 space-y-2 text-sm"
        data-testid="chat-messages"
      >
        {messages.length === 0 && !pending && (
          <p className="text-xs text-gray-500">
            Describe the workflow you want in natural language. The assistant
            will either ask for details or draft a DAG you can review.
          </p>
        )}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        {pending && (
          <div className="text-xs text-gray-500 italic" aria-live="polite">
            Thinking…
          </div>
        )}
        {lastError && (
          <pre
            role="alert"
            className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2 whitespace-pre-wrap"
          >
            {lastError}
          </pre>
        )}
      </div>

      {pendingDraft && (
        <div className="border-t bg-amber-50 px-3 py-2 flex items-center justify-between">
          <span className="text-xs text-amber-800">
            {pendingIntent === "refine"
              ? "Proposed refinement ready"
              : "Proposed DAG ready"}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={clearPendingDraft}
              className="text-xs border rounded px-2 py-1 hover:bg-white"
            >
              Reject
            </button>
            <button
              type="button"
              onClick={onApply}
              className="text-xs bg-emerald-600 text-white rounded px-2 py-1 hover:bg-emerald-700"
              data-testid="apply-draft"
            >
              Apply
            </button>
          </div>
        </div>
      )}

      <form onSubmit={onSubmit} className="border-t p-2 flex gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends, Shift+Enter inserts a newline — standard chat UX.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSubmit(e as unknown as React.FormEvent);
            }
          }}
          rows={2}
          placeholder="Describe the workflow you want…"
          className="flex-1 border rounded px-2 py-1 text-sm resize-none"
          disabled={pending}
          data-testid="chat-input"
        />
        <button
          type="submit"
          disabled={pending || !input.trim()}
          className="text-sm bg-blue-600 text-white rounded px-3 py-1 disabled:bg-gray-300"
        >
          Send
        </button>
      </form>
    </aside>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] bg-blue-600 text-white rounded-lg px-3 py-1.5 whitespace-pre-wrap">
          {message.text}
        </div>
      </div>
    );
  }
  return <AssistantBubble result={message.result} />;
}

function AssistantBubble({ result }: { result: ComposeResult }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="max-w-[95%] bg-gray-100 text-gray-900 rounded-lg px-3 py-1.5 whitespace-pre-wrap">
        {result.rationale || "(no rationale)"}
      </div>
      {result.intent === "clarify" &&
        result.clarify_questions &&
        result.clarify_questions.length > 0 && (
          <ul className="ml-2 space-y-1" data-testid="clarify-questions">
            {result.clarify_questions.map((q, i) => (
              <li
                key={i}
                className="text-xs text-gray-700 border-l-2 border-gray-300 pl-2"
              >
                {q}
              </li>
            ))}
          </ul>
        )}
      {(result.intent === "draft" || result.intent === "refine") &&
        result.proposed_dag && (
          <div
            className="text-[11px] text-gray-500 ml-2"
            data-testid="proposed-summary"
          >
            {result.proposed_dag.nodes.length} nodes ·{" "}
            {result.proposed_dag.edges.length} edges
          </div>
        )}
    </div>
  );
}
