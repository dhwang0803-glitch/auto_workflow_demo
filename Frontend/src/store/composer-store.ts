import { create } from "zustand";
import type {
  ComposeIntent,
  ComposeProposedDag,
  ComposeResult,
} from "@/lib/composer";

// Chat messages keep the turn-by-turn conversation. The assistant entry
// carries the full `ComposeResult` so the panel can render intent-specific
// affordances (clarify questions, "Apply draft" button for draft/refine)
// without rehydrating state from separate fields.
export type ChatMessage =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; result: ComposeResult };

interface ComposerState {
  sessionId: string | null;
  messages: ChatMessage[];
  pending: boolean;
  // Tokens accumulated from the in-flight SSE stream. Rendered as a "typing"
  // bubble at the bottom of the message list while `pending` is true; cleared
  // when the terminal `result` frame turns into a real assistant message.
  streamingRationale: string;
  lastError: string | null;
  // The proposed DAG from the most recent draft|refine assistant turn,
  // awaiting the user's Apply decision. Null once applied or superseded.
  pendingDraft: ComposeProposedDag | null;
  pendingIntent: ComposeIntent | null;
  open: boolean;

  setOpen: (open: boolean) => void;
  setSessionId: (id: string) => void;
  pushUser: (text: string) => void;
  pushAssistant: (result: ComposeResult) => void;
  setPending: (pending: boolean) => void;
  appendRationale: (token: string) => void;
  clearStreamingRationale: () => void;
  setError: (msg: string | null) => void;
  clearPendingDraft: () => void;
  reset: () => void;
}

let msgCounter = 0;
const newMsgId = () => `m_${Date.now()}_${msgCounter++}`;

export const useComposerStore = create<ComposerState>()((set) => ({
  sessionId: null,
  messages: [],
  pending: false,
  streamingRationale: "",
  lastError: null,
  pendingDraft: null,
  pendingIntent: null,
  open: false,

  setOpen: (open) => set({ open }),

  setSessionId: (sessionId) => set({ sessionId }),

  pushUser: (text) =>
    set((s) => ({
      messages: [...s.messages, { id: newMsgId(), role: "user", text }],
    })),

  pushAssistant: (result) =>
    set((s) => ({
      messages: [
        ...s.messages,
        { id: newMsgId(), role: "assistant", result },
      ],
      // The most recent draft|refine supersedes any earlier pending draft —
      // the user only gets one Apply/Reject decision at a time.
      pendingDraft:
        result.intent === "clarify" ? null : result.proposed_dag ?? null,
      pendingIntent:
        result.intent === "clarify" ? null : result.intent,
    })),

  setPending: (pending) => set({ pending }),

  appendRationale: (token) =>
    set((s) => ({ streamingRationale: s.streamingRationale + token })),

  clearStreamingRationale: () => set({ streamingRationale: "" }),

  setError: (lastError) => set({ lastError }),

  clearPendingDraft: () =>
    set({ pendingDraft: null, pendingIntent: null }),

  reset: () =>
    set({
      sessionId: null,
      messages: [],
      pending: false,
      streamingRationale: "",
      lastError: null,
      pendingDraft: null,
      pendingIntent: null,
      open: false,
    }),
}));
