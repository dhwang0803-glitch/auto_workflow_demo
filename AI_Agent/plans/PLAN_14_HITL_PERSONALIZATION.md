# PLAN_14 — HITL Personalization (user edit patterns → personalized drafts)

> **Status**: Draft (2026-05-07) · **Owner**: dhwang0803 · **Predecessor PLANs**: PLAN_12 (skill bootstrap), PLAN_13 (reflective agent) · **Deadline**: 2026-05-18 (hackathon, 11 days) · **Scheduled start**: D4 (5/11). D1–D3 is separate E2E stabilization.

---

## 1. Motivation

The **skill bootstrap** PLAN_12 built was the entry point for "skill-ifying a team's static domain knowledge" — extract if docs exist, interview if not. Once a skill is in, retrieval injects it during workflow-draft generation.

One step further — **the user's act of editing an AI-generated draft is itself a new source of skills**. Users show consistent edit patterns without realizing "I always change it this way." If the system doesn't recover those patterns:

1. **The user repeats the same edits** — this is exactly where n8n / Zapier stop. The system doesn't learn the user, so they have to polish it from scratch every single time.
2. **Skill bootstrap closes only half** — we got policy → skill, but the recovery loop from actual workflow-build artifacts → skill is empty. A direct realization of "observation-based skill candidates" listed in ADR-022's downstream impact.

This PLAN closes that recovery loop — **HITL edit diff → personal_skill candidate → review then activate → injected on the next draft generation via retrieval**. PLAN_13's reflective agent is reused as the self_eval that judges "is this diff a generalizable pattern?"

**Why now (hackathon narrative)**: 70% of judging is non-tech (Impact & Vision 40 + Storytelling 30). The differentiating headline message is "**the team doesn't train the system — the system trains itself on the team**." This is the direct hit on n8n-differentiator #2 (retuning team policy every time) — if bootstrap is the first seed, HITL recovery is self-growth. In the 30s video we can show "draft v1 → user edits → those edits pre-applied in a different workflow's draft."

**Why feasible**: no new model training, no new infra. We finish in 8 days by reusing existing assets:
- skill DB / BGE-M3 retrieval / system prompt inject — landed in PLAN_12 W3
- reflective agent (extract → self_eval → reflect) — landed in PLAN_13. self_eval reused directly as the "diff justifiable?" judge
- workflow v1 (AI draft) is the `/v1/compose` result as-is; v2 (user-edited) is at workflow save time

## 2. Decision — Diff-based personal skill, reuse the reflective judge

### 2.1 Candidate comparison

| Approach | Mechanism | Demo-ability | Data-accrual time | Hackathon fit |
|---|---|---|---|---|
| **(a) Diff-based** | v1 vs v2 diff → reflective agent judges "generalizable?" → save as personal_skill card → activate after review | The diff itself is the strongest visual | One edit produces one candidate | ★★★ |
| (b) NL retrospective | After the edit, ask "why did you change it?" and turn the answer into a skill | Clear but UX heavy | Immediate | ★ |
| (c) Implicit stats | Accumulate node / parameter selection frequencies → hint the next draft | Effect needs accumulation | Needs many workflows | ★ |

**Decision: (a) Diff-based**.

Rationale:
- **Demo-able** — the diff itself is the on-screen visual. Added nodes / changed branches / tweaked parameters are color-highlighted, and "pre-applied on the next draft" is visible to the eye.
- **Reusable infra matches exactly** — PLAN_13 self_eval's deterministic rules + LLM judge hybrid fits "is this diff a generalizable pattern?" directly. Only the judge prompt swaps.
- **Cold-start friendly** — even one user's one edit produces one candidate. Unlike statistical accumulation (c), the effect is immediate at demo time.
- **Review gate keeps trust** — no auto-activation. Activate only after user review/edit/reject. Consistent with ADR-022 §11.1's "MVP humans review" policy.

### 2.2 ADR placement

- **ADR-023 (new)**, to be added — "HITL edit recovery → personal_skill" decision. Direct implementation of ADR-022 §11.5 downstream-impact "observation-based skill candidates." Added together when this PLAN's doc PR merges.

## 3. Scope

### In Scope (this PLAN, ~8 days)

- **Capture workflow revisions** — on workflow save, store the previous AI draft (v1) alongside. New table `workflow_revisions` (workflow_id FK + revision_no + source: "ai_draft" | "user_edit" + payload JSONB + parent_revision_id).
- **Extract diff** — node-grain diff between v1 and v2 (added / removed / modified). Not text diff but semantic node/edge diff on the workflow schema. Pure function in `services/workflow_diff.py`.
- **Personal-skill domain** — add `scope: "workspace" | "user"` column + optional `user_id` to the existing skill table. At retrieval time merge workspace skills ∪ the current user's personal skills into the same pool for BGE-M3 search.
- **Diff → personal_skill candidate** — new `agents/personalization_agent.py`. Reuse the PLAN_13 reflective-agent pattern:
  - Node 1: `propose` — takes diff + v1 context → produce a "generalization hint for this change" (1 LLM call, max_tokens 256)
  - Node 2: `judge` — decides whether the proposal is (i) generalizable (ii) non-contradictory (iii) not one-off noise. Same pattern as PLAN_13's LLM judge.
  - Termination: on pass, create a personal_skill candidate row (status="pending_review"); on fail, drop + log reason.
- **Review UI** — "Suggested from your edits" section in the Frontend Library. The user can (a) activate (b) edit then activate (c) reject. Reject logs a suppression hash so the same pattern isn't re-suggested.
- **Retrieval inject** — at `/v1/compose` BGE-M3 search, **merge workspace skills and active personal_skills into the same candidate pool** for top-K. The system prompt is a single "## Skills" section — no personal vs workspace marker. The narrative ("the system learns the user") hinges on invisibility — the user receives a draft where their own taste is naturally baked in.
- **HTTP exposure** — `POST /v1/personalization/extract_from_diff` (takes two workflow_revisions to produce a candidate, returns agent_trace). `GET /v1/personalization/candidates` (current user's pending candidates).
- **Regression guard** — `scripts/plan_14_personalization_smoke.py` — using a fixture user/workflow/edit pair, create candidate + activate via review + verify inject on the next draft + verify LangSmith trace.
- **Unit tests** — diff-function determinism + per-agent-node validation + retrieval scope isolation (user A's personal skill must not leak into user B's draft) + system-prompt inject snapshot tests.

### Out of Scope

- **Multi-user cross-team learning** — workspace sharing / opt-in sharing of personal_skill is future. This PLAN is user-scope only.
- **Auto-activation (skip review)** — this PLAN keeps the human review gate. Auto-activate after accruing a trust threshold is future.
- **Conflict resolution** — auto-resolution when workspace skill and personal skill contradict. This PLAN defers to the LLM's context-priority judgment (system prompt declares "personal overrides workspace when conflicting") + post-measurement refinement.
- **Temporal decay** — auto-retire / weight-decay for old personal_skills. This PLAN ships status only (active / archived). Decay is future.
- **Reflective regression on the diff itself** — no reflective applied at the diff-extraction stage. PLAN_13's self_eval pattern is reused only as propose+judge.
- **Heavy migration tooling on the Database branch** — this PLAN only adds `workflow_revisions` + two columns to the skill table. Existing alembic procedure stands.
- **Multimodal workflow diff** — node/edge schema diff only. Changes to images / attachments inside a node are future.

## 4. Architecture

### 4.1 Data flow

```
User natural-language request
    │
    ▼
/v1/compose (AI draft generation)
    │   │  ── workspace skill + active personal_skill retrieval inject
    │   │
    │   └──► workflow_revisions row
    │           (revision_no=1, source="ai_draft", payload=v1)
    ▼
Frontend workflow editor
    │
    │   user edits
    ▼
Workflow save
    │
    │   └──► workflow_revisions row
    │           (revision_no=2, source="user_edit", parent=1, payload=v2)
    │
    │   asynchronously:
    ▼
/v1/personalization/extract_from_diff
    │
    ├─► services/workflow_diff.py (semantic diff: nodes/edges/params)
    │
    ▼
agents/personalization_agent (langgraph)
    │
    │   propose ──► judge ──► [pass] personal_skill_candidate (status="pending_review")
    │                    │
    │                    └─► [drop] log only
    ▼
Frontend Library "Suggested from your edits"
    │
    │   user review (activate / edit / reject)
    ▼
personal_skill (status="active")
    │
    ▼
Included in the retrieval pool on the next /v1/compose
```

### 4.2 Personalization agent graph

```
                ┌────────────┐
   start  ──►   │  propose   │   diff + v1 context → generalization hint (LLM, max_tokens=256)
                └─────┬──────┘
                      │ ProposalDraft
                      ▼
                ┌────────────┐
                │   judge    │   reuses PLAN_13 judge pattern — decide (i) generalize (ii) contradiction (iii) noise
                └─────┬──────┘
                      │
            ┌─────────┴──────────┐
            │                    │
        decision="accept"     decision="reject"
            │                    │
            ▼                    ▼
        ┌───────────┐         ┌───────────┐
        │ candidate │         │  end      │
        │ (DB row)  │         │ (drop)    │
        └───────────┘         └───────────┘
```

Termination branches:
- judge `decision == "accept"` → persist candidate row
- judge `decision == "reject"` + reason → drop + structured log (also a re-suggestion suppression hash)
- propose returns empty → drop with reason="empty_proposal"

`max_iter=1` — no reflect loop. If judge rejects, we end immediately. (Only propose+judge from the PLAN_13 reflective pattern; the reflect stage is intentionally dropped — if the diff is noise, looping just produces more noise.)

### 4.3 Data model

```python
# Database/migrations new
class WorkflowRevision(Base):
    id: UUID (PK)
    workflow_id: UUID (FK)
    revision_no: int           # 1-indexed
    source: Literal["ai_draft", "user_edit"]
    payload: JSONB             # full WorkflowSchema serialized
    parent_revision_id: UUID | None  # diff comparison target
    created_at: datetime
    created_by: UUID (FK users) | None  # None when ai_draft

# Existing skill table (added columns)
class Skill(Base):
    # ... existing columns ...
    scope: Literal["workspace", "user"]    # new, default "workspace"
    user_id: UUID | None                    # only when scope="user"
    source: Literal["docs", "wizard", "hitl_edit"]  # new
    suggestion_hash: str | None             # for re-suggestion suppression on hitl_edit candidates

class PersonalSkillReview(Base):
    """Candidate-review history — per-user, consistently accumulated decision record"""
    id: UUID (PK)
    user_id: UUID (FK)
    suggestion_hash: str
    action: Literal["accept", "edit", "reject"]
    rejection_reason: str | None
    created_at: datetime
```

Why `PersonalSkillReview` is a separate table — **per-user review decisions accumulate as a durable record**. Same position as Claude Code's per-project `MEMORY.md`: which patterns the user accepted and rejected accumulates over time and becomes part of the user model. Instead of cramming review fields into the skill row, a separate table:
- lets us suppress re-suggestion via hash even for rejected candidates (which never become skill rows)
- lets us query decision history chronologically (e.g., "reject pattern over the last 3 months")
- one user's review distribution doesn't bleed into another's (table-level isolation natural)

`suggestion_hash` computation: SHA256 prefix of the judge-passed proposal's generalization-hint text + diff signature (node types added/removed). If the same hash was already rejected, drop at the candidate-generation stage.

### 4.4 Diff function — semantic, not text

```python
def diff_workflow(v1: WorkflowSchema, v2: WorkflowSchema) -> WorkflowDiff:
    """Node/edge-grain comparison. Not text diff."""
    return WorkflowDiff(
        nodes_added=[...],     # v2 only
        nodes_removed=[...],   # v1 only
        nodes_modified=[       # same id, different params
            NodeChange(id=..., before=..., after=..., changed_keys=[...])
        ],
        edges_added=[...],
        edges_removed=[...],
        ordering_changed=bool, # topological-sequence change
    )
```

Matching rule: preserve node id (Frontend retains id on edits), generate id for new nodes. Parameter comparison is deep equality + extract change keys. Text diff (e.g., typo fix in a node name) has low generalization value, so it's out of scope — if `nodes_modified`'s `changed_keys` is empty, treat as noise and drop at the propose stage.

### 4.5 Propose / Judge prompts

**Propose** (system + user):
```
SYSTEM:
You inspect a single edit a user made to an AI-generated workflow draft.
Output ONE generalization hint (≤ 30 words) that captures the user's preference,
or empty string if the edit is a one-off correction (typo, label).

Examples of generalizable:
- "User adds Slack notify after every credential-touching step"
- "User prefers 5min retry instead of default 30s for HTTP nodes"

Examples of one-off (drop):
- "Renamed node from 'Step 1' to 'Send report'"
- "Fixed parameter typo"

USER:
Original draft (v1): <workflow JSON>
User-edited (v2): <workflow JSON>
Diff: <WorkflowDiff serialized>
```

**Judge** (reuses the PLAN_13 judge.py pattern):
```
SYSTEM:
You are validating a personalization hint derived from a user's edit.
Reject if:
- Hint is too specific to one workflow (won't generalize)
- Hint contradicts a known workspace policy: <inject relevant workspace skills>
- Hint is a label/typo correction
- Hint repeats a previously-rejected pattern: <inject suggestion_hash matches>

Output JSON: {"decision": "accept"|"reject", "reason": "..."}

USER:
Proposed hint: <hint>
Diff signature: <changed node types + params>
```

`max_tokens` propose=256 / judge=128. Both `@traceable` (uses the PLAN_13 LangSmith integration as-is).

### 4.6 Retrieval inject change

Expand the existing BGE-M3 retrieval (PLAN_12 W3-3) pool:
- Query: user's natural-language request
- Candidate pool: workspace skills (scope="workspace") ∪ active personal skills of current user (scope="user", user_id=current, status="active") — **merged into the same pool** for top-K 5
- Isolation — user A's personal skill never enters user B's search pool (user_id filter at retrieval query)

System-prompt assembly — **single "## Skills" section**:
```
## Skills
- {skill 1}
- {skill 2}
- {skill 3}

(rest of prompt body)
```

No split markers (`## Workspace` / `## Personal`). Reasons:
- **Narrative invisibility is the core** — the strongest moment of "the system learns the user" is when the user receives a draft where their taste is naturally baked in and they realize "huh, this is what I usually add." Splitting and saying to the LLM "this is your personal preference" breaks invisibility.
- **Conflicts are out of scope here** — split markers' main benefit (LLM priority decisions) loses no ground because conflict resolution is out of scope anyway.
- **Simplicity** — eases PR-F. Single section means PR-F finishes with just the retrieval-pool expansion.

A visibility option like a "your pattern" badge next to a generated draft node in the Frontend is §8 unresolved (PR-H optional if time remains).

### 4.7 Latency budget

| Scenario | Calls | Time (warm) |
|---|---|---|
| Workflow save (revision write only) | DB only | < 100ms |
| Candidate generation after diff (propose+judge) | 2 LLM calls (256+128 tok) | ~10–15s |
| Next compose after candidate activation | Existing compose (+N to skill pool), inject token +200–400 | Existing + 0 (retrieval cost negligible) |

Candidate generation is async post-processing of workflow save (Celery / Modal background) — no user-interaction blocking.

### 4.8 Regression guard

`scripts/plan_14_personalization_smoke.py` (reuses the PLAN_13 smoke pattern):
1. Load fixture user / workflow v1
2. Apply fixture edit → v2
3. Call `/v1/personalization/extract_from_diff` → verify candidate creation
4. Activate the candidate (direct DB update in the script)
5. New NL request from the same user → `/v1/compose` → verify personal skill appears in the system prompt
6. Different user → `/v1/compose` → verify no personal-skill leak (isolation guard)
7. Verify propose/judge nodes appear in the LangSmith trace tree

Goal — 100% of fixture scenarios pass + 100% isolation guard. Positive metrics are video-narrative-only (the demo itself is the asset, not quantitative measurement).

## 5. Directory + file layout

```
AI_Agent/app/agents/
├── personalization_agent.py        ← (new) propose+judge graph + node functions
├── state.py                         ← (modified) add PersonalizationState (or separate file)
└── eval.py                          ← (modified) generalize the judge helper (separated from PLAN_13's policy judge)

AI_Agent/app/services/
├── workflow_diff.py                 ← (new) semantic diff (pure function)
└── personalization_service.py       ← (new) candidate-generation orchestration (agent call + DB write)

AI_Agent/app/main.py                 ← (modified) /v1/personalization/* routes
AI_Agent/app/models/personalization.py ← (new) Pydantic schemas
AI_Agent/app/prompts/personalization/ ← (new) propose/judge templates

API_Server/api/v1/personalization/   ← (new) Frontend-exposed routes (AI_Agent proxy)
API_Server/services/workflow_revisions.py ← (new) revision-record hook on workflow save

Database/migrations/                 ← (new) workflow_revisions table + skill scope/user_id/source/suggestion_hash columns + personal_skill_reviews table
Database/models/workflow_revision.py ← (new)
Database/models/skill.py             ← (modified)

Frontend/src/components/library/SuggestedFromEdits.tsx ← (new) candidate-review UI

AI_Agent/scripts/plan_14_personalization_smoke.py ← (new) live regression measurement
AI_Agent/tests/test_workflow_diff.py             ← (new)
AI_Agent/tests/test_personalization_agent.py     ← (new, stub backend, tracing OFF)
AI_Agent/tests/test_personalization_route.py     ← (new)
API_Server/tests/test_workflow_revisions.py      ← (new)
```

**Why `workflow_diff.py` is under `services/`** — pure function with no LLM call. Data transformation with no agent dependency.

**Generalize `agents/eval.py`** — PLAN_13's policy_extract judge and this PLAN's personalization judge both follow the "LLM outputs JSON decision" pattern, so factor out a call helper that differs only in prompt. Avoid function sprawl (`feedback_avoid_function_sprawl.md`) — not a single-use wrapper, abstract when two call sites are clear.

## 6. PR split

Small-unit merges. Each PR merges only after explicit user approval (`feedback_no_auto_merge.md`).

| # | Content | Depends on | Branch |
|---|---|---|---|
| **(this PR)** | PLAN_14 doc + ADR-023 | — | AI_Agent (plan location) |
| **PR-A** | Database migration — `workflow_revisions` + skill scope/user_id/source/suggestion_hash + `personal_skill_reviews` + base ORM models + unit tests | this PR | Database |
| **PR-B** | API_Server — `/api/v1/workflows/<id>` save hook records a revision + revision lookup endpoint + unit tests | PR-A | API_Server |
| **PR-C** | AI_Agent — `services/workflow_diff.py` (pure function) + unit tests (semantic-diff determinism) | PR-A | AI_Agent |
| **PR-D** | AI_Agent — `agents/personalization_agent.py` (propose+judge graph) + LangSmith @traceable + stub-backend unit tests (tracing OFF) | PR-C | AI_Agent |
| **PR-E** | AI_Agent — `/v1/personalization/extract_from_diff` route + `personalization_service.py` (orchestration) + route integration tests | PR-D | AI_Agent |
| **PR-F** | AI_Agent — **scope reduced** (2026-05-12): retrieval-pool inject and the single `## Skills` section display are absorbed by PLAN_15 PR-γ (#172 `search_personal_skills` tool). This PR is the remainder — route-level cross-user isolation integration test (no leak when alice/bob both have files) only. Unit guards (path traversal / anonymous / cold-start) are guaranteed by PR-γ unit tests | PR-E | AI_Agent |
| **PR-G** | API_Server — `/api/v1/personalization/*` (extract_from_diff + list/activate/reject candidates) + Database SkillRepository `user_id`/`source`/`suggestion_hash` extension + new `PersonalSkillReviewRepository` | PR-E | API_Server + Database |
| **PR-H** | Frontend — Library "Suggested from your edits" section + activate/edit/reject UI + client enhancement to record revision automatically on workflow save | PR-G | Frontend |
| **PR-I** | (1) modal_app.py `personal_memory_volume` mount + `PERSONAL_MEMORY_DIR` env (infra PR-D/E/G missed) — (2) AI_Agent `POST /v1/personalization/memory/upsert` (file write + BGE-M3 embedding) — (3) API_Server `PersonalizationService.activate_candidate` best-effort upsert call (closes DB↔JSON sync gap) — (4) Modal smoke (`plan_14_personalization_smoke.py`) PLAN_14 §4.8 5 steps + unit tests (writer 8 + route 5 + activate sync 2) — (5) update this doc's measurements | PR-F + PR-H | AI_Agent + API_Server |
| **(W4)** | Video-demo capture + writeup integration — fit this PLAN's narrative ("the system learns the user") into a 30s sequence | PR-I | docs |

9 PRs total. Each averages 0.5–1d. Pace finishes serially within 8 days. PR-C and PR-B can run in parallel after PR-A merges (only the Database decision is shared).

**Explicit scope-cut authority**: if time runs out, PR-F (isolation guard) is the floor — for the video we can activate just one personal skill and show inject on the next draft, narrative still holds. If PR-G/H/I don't finish, fall back from live demo to Frontend mock + LangSmith trace.

## 7. Risks + mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **One-off noise mistakenly elevated to personal_skill** | Bad hint injected into the next draft → trust loss | User review gate (no auto-activate) + judge's "one-off correction" rule + reject hash suppresses re-suggestion of the same pattern |
| **Personal skill ↔ Workspace skill conflict** | Contradictory hints injected at once → LLM confusion | Out of scope here. Simple-pool inject means conflicts surface in human review or get absorbed by LLM's natural integration. If conflicts grow frequent, future PR introduces reranker / scope markers |
| **Diff extraction breaks under workflow-schema change** | New node types cause wrong added/removed | Semantic diff doesn't look at node type — only id+params — so new types absorb naturally. Schema changes are caught as regressions by unit tests |
| **Cold-start (no existing user)** | At demo time personal skill is 0 → weakens narrative | Fixture scenario — one user, workflow v1, one edit → one personal skill → inject on a different NL request. The demo itself is the cold-start case |
| **Privacy — personal skill leaks to other users** | User A's edit pattern shows on B's draft → data-isolation violation | Force user_id filter on retrieval queries + unit tests (isolation guard in PR-F) + block PR-F merge if isolation breaks |
| **LLM judge false-positive (accepts noise)** | Bad personal-skill candidates pile up in the user's review queue | Judge prompt enumerates reject rules + suggestion_hash blocks repeated accumulation. User review is the final gate, so final impact is 0 |
| **Reflective-agent reuse assumption breaks (judge can't generalize outside the policy domain)** | propose+judge doesn't behave as designed | The judge prompt is separate from the policy judge (reuse is the call helper + state pattern only). Fallback on failure — pin `decision="accept"` + 100% delegate to humans (good enough for hackathon demo) |
| **Modal cold start + async candidate generation** | 30s+ delay between save and candidate generation | Async post-processing means no interaction blocking. Pre-warm with one call before the demo. Video focuses on showing the effect after activation |
| **Database migration causes staging schema pollution** | Worsens flakiness 8 (`project_test_flakiness_debt.md`) | Only add new tables (column additions are nullable + default). Monitor pollution after PR-A smoke |
| **This PLAN doesn't finish by the W4 video** | Unfinished demo | Scope cut — through PR-F is enough for a LangSmith trace + DB query demo. PR-H's Frontend UI if time permits |

## 8. Unresolved decisions (to settle during implementation)

1. **Diff-extraction unit precision** — if node-parameter deep equality is too strict (catching label changes etc. in `nodes_modified`), strengthen the propose-stage drop rule. Settle after measurements.
2. **Personal-skill temporal decay** — this PLAN only has active/archived. Auto-retire threshold is future. A manual archive button for demo purposes could land in PR-H.
3. **Workspace sharing (opt-in)** — user clicks "share this personal skill with the team." This PLAN is user-scope only. Bundle as a follow-up with ADR-022 §11.5's multi-membership future.
4. **Effect of personal-skill inject at compose** — whether the merged-single-pool inject behaves as intended (LLM naturally absorbs personal skills as the user's taste). If measurements show ignore or over-application, consider reranker / scope markers / weights. A Frontend "your pattern" badge as a visibility option is on the table after measuring narrative effect.
5. **User display of reject reasons** — by default, judge-rejected candidates aren't shown to the user. A "show rejected suggestions" toggle is future.
6. **suggestion_hash collisions** — SHA256-prefix length (16 chars) makes collision negligible, but different patterns mapping to the same hash would wrongly suppress. If measurement reveals collisions, include the hint text in the hash input.

## 9. Milestones (5/11 → 5/18)

> 5/8-10 (D1-D3) is separate E2E stabilization work. This PLAN starts at D4.

| Date | Work | Status |
|---|---|---|
| 5/11 (D4) | doc PR + ADR-023 + PR-A (DB migration, absorbed PLAN_15 PR-γ #171) + PR-Ba (#176) + PR-B (#177) | ✅ |
| 5/12 (D5) | PR-C (#178 semantic diff) + PR-D (#179 personalization agent) + PR-E (#180 extract_from_diff) + PR-F (#181 cross-user isolation guard, scope-reduced) | ✅ |
| 5/13 (D6) | PR-G (#182 API_Server `/api/v1/personalization/*` proxy + DB write — SkillRepository expansion + new PersonalSkillReviewRepository + 14 route tests) | ✅ |
| 5/14 (D7) | PR-H (#183 Frontend "Suggested from your edits" UI + workflow save → revision_source + auto-trigger extract — 4 new Playwright + tsc/lint/build green) | ✅ |
| 5/14 (D7+) | **PR-I — modal_app personal_memory volume + `/v1/personalization/memory/upsert` + activate sync + smoke + ADR-023 refresh** (DB↔JSON write-path gap discovery → scope explosion) | In progress |
| 5/15-17 | Video-demo capture + writeup | — |
| 5/18 (D11) | Submit (buffer) | — |

1d slack. Scope-cut trigger: if PR-F doesn't merge by D7, cut immediately — drop Frontend UI, fall back to a LangSmith trace + DB query demo.

## 10. Related ADRs / memory / docs

- **ADR-022** (`docs/context/decisions.md`) — parent ADR. This PLAN is a direct implementation of "observation-based skill candidates" in ADR-022 §11.5 downstream-impact
- **ADR-023 (new)** — added together when this PLAN's doc PR merges. The "HITL edit recovery → personal_skill" decision
- **PLAN_12** (`AI_Agent/plans/PLAN_12_skill_bootstrap.md`) — parent of the skill DB / retrieval / inject infra
- **PLAN_13** (`AI_Agent/plans/PLAN_13_LANGGRAPH_AGENT.md`) — parent of the propose+judge pattern + LangSmith integration
- memory `project_skill_bootstrap_design.md` — the skill-bootstrap unified pipeline (this PLAN closes the recovery loop)
- memory `project_plan_13_reflective_agent.md` — source of the judge-pattern reuse
- memory `project_gemma4_hackathon.md` — judging 70% non-tech → reason for narrative center of gravity
- memory `feedback_test_before_pr.md` — duty of external validation (Modal rebuild, DB migration) first
- memory `feedback_no_auto_merge.md` — PRs merge only after explicit user approval
- memory `feedback_avoid_function_sprawl.md` — when generalizing `agents/eval.py`, avoid thin wrappers
- memory `project_test_flakiness_debt.md` — post-DB-migration pollution monitoring duty
