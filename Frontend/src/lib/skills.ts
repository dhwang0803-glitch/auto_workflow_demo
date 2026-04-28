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
  ecommerce: "이커머스",
  services: "서비스업",
  consulting: "컨설팅",
  content: "콘텐츠 제작",
  nonprofit: "비영리",
  other: "기타",
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

const jsonInit = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const bootstrapSkills = (req: BootstrapRequest) =>
  apiFetch<BootstrapResponse>("/api/v1/skills/bootstrap", jsonInit(req));

export const answerWizardQuestion = (req: AnswerRequest) =>
  apiFetch<AnswerResponse>("/api/v1/skills/answer", jsonInit(req));
