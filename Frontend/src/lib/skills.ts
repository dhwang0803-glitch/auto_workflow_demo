// Skill bootstrap wizard client (PLAN_12 W2-5).
//
// Mirrors API_Server/app/models/skills.py. Both endpoints are plain JSON
// (non-SSE) — the wizard turn cadence is bounded (one POST per question)
// so we don't pay the SSE plumbing cost. The server is stateless: the
// frontend mints `session_id` and round-trips it through every /answer.
import { apiFetch } from "./api";

export type DomainCategory =
  | "ecommerce"
  | "services"
  | "consulting"
  | "content"
  | "nonprofit"
  | "other";

export const DOMAIN_LABELS: Record<DomainCategory, string> = {
  ecommerce: "E-commerce",
  services: "Services",
  consulting: "Consulting",
  content: "Content",
  nonprofit: "Nonprofit",
  other: "Other",
};

export interface ExtractedSkill {
  name: string;
  condition: string;
  action: string;
}

export interface BootstrapRequest {
  domain: DomainCategory;
  session_id?: string;
  extracted_skills?: ExtractedSkill[];
}

export interface WizardQuestion {
  text: string;
  parameter: string | null;
}

export interface PolicyGap {
  policy_id: string;
  policy_name: string;
  questions: WizardQuestion[];
}

export interface BootstrapResponse {
  session_id: string;
  domain: DomainCategory;
  missing: PolicyGap[];
}

export interface AnswerRequest {
  session_id: string;
  domain: DomainCategory;
  policy_id: string;
  question: string;
  answer: string;
}

export interface SkillDraft {
  name: string;
  description: string;
  condition: string;
  action: string;
  rationale: string;
  needs_clarification: boolean;
  clarification_hint: string;
}

export interface AnswerResponse {
  session_id: string;
  skill_id: string;
  draft: SkillDraft;
}

// Persisted skill row. Mirrors API_Server's SkillResponse — `condition`
// and `action` arrive as JSONB dicts because ADR-022 §1 leaves room for
// structured matchers. The wizard always wraps prose answers as
// `{"text": "..."}`, so the W2-6 review UI reads `.text` defensively.
export type SkillStatus =
  | "active"
  | "pending_review"
  | "rejected"
  | "archived";

export interface SkillRecord {
  id: string;
  name: string;
  description: string | null;
  condition: Record<string, unknown>;
  action: Record<string, unknown>;
  scope: string;
  status: SkillStatus;
  created_at: string;
  updated_at: string;
}

export interface SkillListResponse {
  skills: SkillRecord[];
}

const jsonInit = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const bootstrapSkills = (req: BootstrapRequest) =>
  apiFetch<BootstrapResponse>("/api/v1/skills/bootstrap", jsonInit(req));

export const answerWizardQuestion = (req: AnswerRequest) =>
  apiFetch<AnswerResponse>("/api/v1/skills/answer", jsonInit(req));

export const approveSkill = (skillId: string) =>
  apiFetch<SkillRecord>(`/api/v1/skills/${skillId}/approve`, {
    method: "POST",
  });

export const rejectSkill = (skillId: string) =>
  apiFetch<SkillRecord>(`/api/v1/skills/${skillId}/reject`, {
    method: "POST",
  });

export const listSkills = (status?: SkillStatus) => {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiFetch<SkillListResponse>(`/api/v1/skills${qs}`);
};

export const getSkill = (skillId: string) =>
  apiFetch<SkillRecord>(`/api/v1/skills/${skillId}`);
