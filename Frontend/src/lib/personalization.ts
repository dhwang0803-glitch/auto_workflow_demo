// HITL personalization client (PLAN_14 PR-H).
//
// Mirrors API_Server/app/models/personalization.py. Four endpoints sit
// under /api/v1/personalization. extract_from_diff is fire-and-forget
// from the editor's Save success handler — the response is small and
// fast enough to await, but Frontend never blocks user input on it.
import { apiFetch } from "./api";

export type DropReason =
  | ""
  | "empty_diff"
  | "empty_proposal"
  | "hash_previously_rejected"
  | "judge_reject";

export type PersonalCandidateStatus =
  | "pending_review"
  | "active"
  | "rejected"
  | "archived";

export interface ExtractFromDiffResponse {
  candidate_id: string | null;
  drop_reason: DropReason;
  suggestion_hash: string | null;
  diff_signature: string | null;
  langsmith_run_id: string | null;
}

export interface PersonalCandidate {
  id: string;
  user_id: string;
  hint: string;
  diff_signature: string;
  suggestion_hash: string | null;
  status: PersonalCandidateStatus;
  created_at: string;
  updated_at: string;
}

export interface PersonalCandidateListResponse {
  candidates: PersonalCandidate[];
}

const jsonInit = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const extractFromDiff = (workflowId: string) =>
  apiFetch<ExtractFromDiffResponse>(
    "/api/v1/personalization/extract_from_diff",
    jsonInit({ workflow_id: workflowId }),
  );

export const listPersonalCandidates = () =>
  apiFetch<PersonalCandidateListResponse>(
    "/api/v1/personalization/candidates",
  );

export const activatePersonalCandidate = (id: string) =>
  apiFetch<PersonalCandidate>(
    `/api/v1/personalization/candidates/${id}/activate`,
    { method: "POST" },
  );

export const rejectPersonalCandidate = (id: string, reason?: string) =>
  apiFetch<PersonalCandidate>(
    `/api/v1/personalization/candidates/${id}/reject`,
    jsonInit({ reason: reason ?? null }),
  );
