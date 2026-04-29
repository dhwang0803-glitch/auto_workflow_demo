// Skill bootstrap wizard client (PLAN_12 W2-5 + W2-5b batch cut-over).
//
// Mirrors API_Server/app/models/skills.py. Both endpoints are plain JSON
// (non-SSE) — the wizard cadence is bounded (one POST per policy after
// W2-5b: N parameter answers batched into a single SkillDraft) so we
// don't pay the SSE plumbing cost. The server is stateless: the frontend
// mints `session_id` and round-trips it through every /answers.
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

// Honest labelling of where a policy comes from. Drives the source-kind
// pill on skill cards and library view (memory: project_wizard_polish_abc.md).
export type SourceKind = "regulatory" | "industry-baseline" | "synthesized";

export interface PolicySource {
  title: string;
  url: string;
}

export interface WizardQuestion {
  text: string;
  parameter: string | null;
  // W2-4 polish + W2-4d additions (default `""` for forward-compat with
  // pre-#143 PolicyGap payloads — the wizard renders fields conditionally
  // when these are empty).
  default_baseline: string;
  baseline_source: string;
  help_text: string;
  example_answer: string;
}

export interface PolicyGap {
  policy_id: string;
  policy_name: string;
  // `parameters` is the authoritative list (W2-4 polish). `questions`
  // stays as a backward-compat alias of `parameters` while API_Server
  // keeps both fields populated. Frontend always reads `parameters`.
  parameters: WizardQuestion[];
  sources: PolicySource[];
  source_kind: SourceKind;
  questions?: WizardQuestion[];
}

export interface BootstrapResponse {
  session_id: string;
  domain: DomainCategory;
  missing: PolicyGap[];
}

export interface ParameterAnswer {
  parameter: string;
  answer: string;
}

export interface AnswersRequest {
  session_id: string;
  domain: DomainCategory;
  policy_id: string;
  answers: ParameterAnswer[];
  // Provenance forwarded from the wizard's PolicyTurn so the persisted
  // skill carries source_kind / sources into /skills list + library
  // view (PR β of the source round-trip). Optional on the wire — the
  // backend stores `null` / `[]` if omitted (PR α).
  source_kind?: SourceKind | null;
  sources?: PolicySource[];
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

export interface AnswersResponse {
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
  // Provenance hydrated from skill_sources.source_ref by API_Server's
  // _to_response (PR α). `source_kind=null` + `sources=[]` for skills
  // created before the round-trip landed; the library view falls back
  // to hiding the pill rather than mis-labelling them as synthesized.
  source_kind: SourceKind | null;
  sources: PolicySource[];
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

export const answerWizardQuestions = (req: AnswersRequest) =>
  apiFetch<AnswersResponse>("/api/v1/skills/answers", jsonInit(req));

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
