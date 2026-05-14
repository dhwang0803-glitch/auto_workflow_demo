"""PersonalizationService — orchestrates AI_Agent + DB (PLAN_14 PR-G).

Flow per `extract_from_diff` call:

1. Resolve v1/v2 revisions for the workflow (latest user_edit + its
   parent ai_draft via WorkflowRevisionRepository).
2. Concatenate per-user suppression hashes:
   - `SkillRepository.list_personal_suggestion_hashes(user_id)` —
     anything already in the user's `scope='user'` skills table
     (pending, active, archived).
   - `PersonalSkillReviewRepository.list_rejected_hashes(user_id)` —
     rejected proposals that never became skill rows.
3. Proxy AI_Agent — agent stays stateless; rejected_hashes is its only
   dedup signal.
4. Persist the outcome:
   - `accepted` → `Skill(scope='user', user_id, source='hitl_edit',
     suggestion_hash, status='pending_review')`.
   - `drop_reason='judge_reject'` → `PersonalSkillReview(action='reject',
     rejection_reason=judge.reason)` so the next run short-circuits
     even though no skill row exists.
   - other drop_reasons (`empty_diff`, `empty_proposal`,
     `hash_previously_rejected`) → no DB write; the caller surfaces the
     reason to the client.

`activate` / `reject` are post-review user actions, mirroring the
existing skill bootstrap approve/reject path but writing to the
`personal_skill_reviews` audit log on reject (a workspace skill reject
just transitions status; a personal candidate reject also records the
hash so the same proposal stays suppressed).
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from auto_workflow_database.repositories.base import (
    PersonalSkillReviewRepository,
    Skill,
    SkillRepository,
    WorkflowRepository,
    WorkflowRevisionRepository,
)

from app.errors import DomainError, NotFoundError
from app.models.personalization import (
    DropReasonLiteral,
    ExtractFromDiffResponse,
    PersonalCandidateResponse,
)
from app.services.ai_agent_client import AIAgentHTTPBackend

logger = logging.getLogger(__name__)


class NoDiffAvailableError(DomainError):
    """422 — workflow has fewer than two revisions, or top revision is
    an ai_draft (nothing the user edited yet).

    Frontend should not call extract_from_diff in this state, but the
    auto-trigger on save races against the user clicking "extract" from
    a stale UI. 422 is the same shape SkillBootstrapService uses for
    "input was structurally wrong".
    """

    http_status = 422


class PersonalCandidateNotActionableError(DomainError):
    """409 — activate/reject called on a candidate that isn't pending."""

    http_status = 409

    def __init__(self, skill_id: UUID, current_status: str) -> None:
        super().__init__(
            f"candidate {skill_id} cannot transition from {current_status!r}"
        )


class PersonalizationService:
    def __init__(
        self,
        *,
        ai_agent: AIAgentHTTPBackend,
        workflow_repo: WorkflowRepository,
        revision_repo: WorkflowRevisionRepository,
        skill_repo: SkillRepository,
        review_repo: PersonalSkillReviewRepository,
    ) -> None:
        self._ai = ai_agent
        self._workflows = workflow_repo
        self._revisions = revision_repo
        self._skills = skill_repo
        self._reviews = review_repo

    # --- extract -------------------------------------------------------

    async def extract_from_diff(
        self,
        *,
        owner_user_id: UUID,
        workflow_id: UUID,
    ) -> ExtractFromDiffResponse:
        # Owner check is on the workflow row, not the revisions. Frontend
        # only knows about workflows; an attacker passing a workflow_id
        # they don't own gets a 404 here before any revision lookup
        # leaks the existence of someone else's edit history.
        wf = await self._workflows.get(workflow_id)
        if wf is None or wf.owner_id != owner_user_id:
            raise NotFoundError(f"workflow {workflow_id} not found")

        v1, v2 = await self._resolve_revision_pair(workflow_id)

        # Concatenate dedup hashes from both tables before the agent call.
        # Order doesn't matter; AI_Agent treats it as a set.
        existing = await self._skills.list_personal_suggestion_hashes(
            owner_user_id
        )
        rejected = await self._reviews.list_rejected_hashes(owner_user_id)
        rejected_hashes = sorted(set(existing) | set(rejected))

        body = await self._ai.extract_personalization_from_diff(
            v1=v1,
            v2=v2,
            rejected_hashes=rejected_hashes,
            user_id=str(owner_user_id),
        )

        return await self._persist_outcome(
            owner_user_id=owner_user_id,
            body=body,
        )

    async def _resolve_revision_pair(
        self, workflow_id: UUID
    ) -> tuple[dict, dict]:
        """Return (v1_payload, v2_payload) for the freshest user_edit on
        top of its parent ai_draft. Raises `NoDiffAvailableError` when
        the chain isn't present.
        """
        # list_by_workflow returns newest-first, so the first user_edit
        # we find is the candidate v2. Its parent_revision_id points back
        # to the v1 the user actually started from — that single jump
        # avoids accidentally diffing against an older ai_draft when the
        # user has edited the workflow more than once.
        head_chunk = await self._revisions.list_by_workflow(
            workflow_id, limit=10
        )
        if not head_chunk:
            raise NoDiffAvailableError(
                f"workflow {workflow_id} has no revisions"
            )
        v2_row = next(
            (r for r in head_chunk if r.source == "user_edit"), None
        )
        if v2_row is None:
            raise NoDiffAvailableError(
                f"workflow {workflow_id} has no user_edit revision"
            )
        if v2_row.parent_revision_id is None:
            raise NoDiffAvailableError(
                f"workflow {workflow_id} user_edit has no parent draft"
            )
        v1_row = await self._revisions.get(v2_row.parent_revision_id)
        if v1_row is None:
            raise NoDiffAvailableError(
                f"workflow {workflow_id} parent revision missing"
            )
        return v1_row.payload, v2_row.payload

    async def _persist_outcome(
        self,
        *,
        owner_user_id: UUID,
        body: dict[str, Any],
    ) -> ExtractFromDiffResponse:
        outcome = body.get("outcome", {})
        diff_signature: str | None = body.get("diff_signature") or None
        langsmith_run_id: str | None = body.get("langsmith_run_id") or None
        suggestion_hash: str | None = outcome.get("suggestion_hash")
        drop_reason: DropReasonLiteral = (
            outcome.get("drop_reason") or ""
        )  # type: ignore[assignment]
        accepted: bool = bool(outcome.get("accepted"))

        if accepted:
            # Propose hint lives on the agent's proposal payload; we
            # mirror it into condition.text so the existing skill DTO
            # surface (condition is JSONB) renders identically to wizard
            # skills. action is a placeholder — the candidate hasn't
            # been concretized into a node yet; PR-H's "edit before
            # activate" flow fills it in.
            proposal = outcome.get("proposal") or {}
            hint = (proposal.get("hint") or "").strip()
            candidate = await self._skills.create(
                owner_user_id=owner_user_id,
                name=hint[:80] or "Personal skill candidate",
                description=hint or None,
                condition={"text": hint},
                action={"text": ""},
                scope="user",
                status="pending_review",
                user_id=owner_user_id,
                source="hitl_edit",
                suggestion_hash=suggestion_hash,
                source_type="observation",
                source_ref={
                    "diff_signature": diff_signature,
                    "suggestion_hash": suggestion_hash,
                    "langsmith_run_id": langsmith_run_id,
                },
            )
            return ExtractFromDiffResponse(
                candidate_id=candidate.id,
                drop_reason="",
                suggestion_hash=suggestion_hash,
                diff_signature=diff_signature,
                langsmith_run_id=langsmith_run_id,
            )

        # Reject from the judge — record so future runs short-circuit.
        # Other drop reasons (`empty_diff`, `empty_proposal`,
        # `hash_previously_rejected`) don't need a row: empty_diff and
        # empty_proposal don't carry a hash, and hash_previously_rejected
        # already has one on file.
        if drop_reason == "judge_reject" and suggestion_hash:
            judgment = outcome.get("judgment") or {}
            await self._reviews.record(
                user_id=owner_user_id,
                suggestion_hash=suggestion_hash,
                action="reject",
                rejection_reason=judgment.get("reason") or None,
            )

        return ExtractFromDiffResponse(
            candidate_id=None,
            drop_reason=drop_reason,
            suggestion_hash=suggestion_hash,
            diff_signature=diff_signature,
            langsmith_run_id=langsmith_run_id,
        )

    # --- list / activate / reject --------------------------------------

    async def list_pending_candidates(
        self,
        owner_user_id: UUID,
    ) -> list[PersonalCandidateResponse]:
        rows = await self._skills.list_owned(
            owner_user_id,
            status="pending_review",
            scope="user",
        )
        return [_to_candidate_response(r) for r in rows]

    async def list_active_personal_candidates(
        self,
        owner_user_id: UUID,
    ) -> list[PersonalCandidateResponse]:
        """Return the caller's active personal skills.

        PR-J needs this so the Frontend can render an "active" lane
        next to the pending list — that lane is where the "Share with
        team" button lives. Workspace skills sit in a different
        surface (skills router) and never appear here.
        """
        rows = await self._skills.list_owned(
            owner_user_id,
            status="active",
            scope="user",
        )
        return [_to_candidate_response(r) for r in rows]

    async def share_candidate(
        self,
        *,
        owner_user_id: UUID,
        candidate_id: UUID,
    ) -> PersonalCandidateResponse:
        """Promote one of the caller's active personal skills to the
        workspace pool — Track C of the demo narrative.

        Two side effects beyond the DB scope flip:
        1. Audit row in `skill_sources` records `shared_by_user_id` so
           future readers can attribute the policy ("originally from
           alice's editing pattern").
        2. Best-effort sync to the per-user JSON memory file marking the
           entry inactive — once the row is workspace, the workspace
           pool covers retrieval and the per-user file would otherwise
           double-inject the same skill.

        Raises NotFoundError if the caller doesn't own the row;
        PersonalCandidateNotActionableError (409) if the skill is
        already workspace OR isn't active.
        """
        existing = await self._skills.get_owned(owner_user_id, candidate_id)
        if existing is None or existing.scope != "user":
            raise NotFoundError(f"candidate {candidate_id} not found")
        if existing.status != "active":
            raise PersonalCandidateNotActionableError(
                candidate_id, existing.status
            )

        shared = await self._skills.share_to_workspace(
            owner_user_id, candidate_id
        )
        if shared is None:
            # Race: someone else flipped the scope between the get and
            # the share. Surface the same 409 the caller would see if
            # they had hit the active-status check first.
            raise PersonalCandidateNotActionableError(
                candidate_id, "workspace"
            )

        await self._deactivate_in_personal_memory(
            owner_user_id=owner_user_id,
            shared_skill_id=candidate_id,
            existing=existing,
        )

        # _to_candidate_response asserts user_id is set, but a shared
        # workspace row has user_id=NULL by DB constraint. Build the
        # response by hand using the caller's id as `user_id` — that's
        # the right surface for the Frontend's "you just shared this"
        # confirmation, and matches the attribution that
        # `source_ref.shared_by_user_id` records persistently.
        assert shared.created_at is not None
        assert shared.updated_at is not None
        src = shared.source_ref or {}
        hint = (shared.condition or {}).get("text") or shared.description or ""
        return PersonalCandidateResponse(
            id=shared.id,
            user_id=owner_user_id,
            hint=hint,
            diff_signature=src.get("diff_signature") or "",
            suggestion_hash=shared.suggestion_hash,
            status=shared.status,  # type: ignore[arg-type]
            created_at=shared.created_at,
            updated_at=shared.updated_at,
        )

    async def _deactivate_in_personal_memory(
        self,
        *,
        owner_user_id: UUID,
        shared_skill_id: UUID,
        existing: Skill,
    ) -> None:
        """Mark the just-shared skill inactive in the user's JSON file.

        Re-uses the upsert endpoint with `active=False` so the next
        reflective-extract for this user sees pool_size shrink by one
        — the workspace pool now covers the policy. Best-effort: a
        transient sync failure logs a warning, the DB transition stays.
        """
        try:
            await self._ai.upsert_personal_memory(
                user_id=str(owner_user_id),
                skill={
                    "id": str(shared_skill_id),
                    "condition": existing.condition or {},
                    "action": existing.action or {},
                    "suggestion_hash": existing.suggestion_hash or "",
                    "source": existing.source or "hitl_edit",
                    "first_observed_at": (
                        existing.created_at.isoformat()
                        if existing.created_at is not None
                        else ""
                    ),
                    "active": False,
                },
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "personalization: memory deactivate failed for skill %s (%s)",
                shared_skill_id,
                exc,
            )

    async def activate_candidate(
        self,
        *,
        owner_user_id: UUID,
        candidate_id: UUID,
    ) -> PersonalCandidateResponse:
        existing = await self._require_candidate(owner_user_id, candidate_id)
        updated = await self._skills.update_status(
            owner_user_id, candidate_id, "active"
        )
        if updated is None:
            raise NotFoundError(f"candidate {candidate_id} not found")
        # Record an explicit accept on the review log so the per-user
        # activity history is queryable independently of skill state.
        if existing.suggestion_hash:
            await self._reviews.record(
                user_id=owner_user_id,
                suggestion_hash=existing.suggestion_hash,
                action="accept",
            )
        # PR-I — propagate the active row to the per-user JSON memory
        # file so the next /v1/policy/extract_reflective request from
        # the same user finds it in the in-memory pool. Best-effort: a
        # transient AI_Agent failure must not poison the user's
        # activate click. The next activate / extract retries the sync.
        await self._sync_active_skill_to_memory(updated)
        return _to_candidate_response(updated)

    async def _sync_active_skill_to_memory(self, skill: Skill) -> None:
        """Write one active personal skill into AI_Agent's memory file.

        Quiet on failure (warning log only); the DB row is the source of
        truth, and a missed sync just means the next reflective-extract
        request misses one entry until the user re-activates or a new
        extract triggers `_persist_outcome` again. The blast radius is
        bounded by the per-user file boundary.
        """
        # Only personal skills participate in retrieval; workspace
        # skills go through a different surface (skills router).
        if skill.scope != "user" or skill.user_id is None:
            return
        try:
            await self._ai.upsert_personal_memory(
                user_id=str(skill.user_id),
                skill={
                    "id": str(skill.id),
                    "condition": skill.condition or {},
                    "action": skill.action or {},
                    "suggestion_hash": skill.suggestion_hash or "",
                    "source": skill.source or "hitl_edit",
                    "first_observed_at": (
                        skill.created_at.isoformat()
                        if skill.created_at is not None
                        else ""
                    ),
                    "active": True,
                },
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "personalization: memory sync failed for skill %s (%s)",
                skill.id,
                exc,
            )

    async def reject_candidate(
        self,
        *,
        owner_user_id: UUID,
        candidate_id: UUID,
        reason: str | None,
    ) -> PersonalCandidateResponse:
        existing = await self._require_candidate(owner_user_id, candidate_id)
        updated = await self._skills.update_status(
            owner_user_id, candidate_id, "archived"
        )
        if updated is None:
            raise NotFoundError(f"candidate {candidate_id} not found")
        if existing.suggestion_hash:
            # archived skill + reject row together — the row is what makes
            # the same hash stay suppressed in future extract calls.
            await self._reviews.record(
                user_id=owner_user_id,
                suggestion_hash=existing.suggestion_hash,
                action="reject",
                rejection_reason=reason,
            )
        return _to_candidate_response(updated)

    async def _require_candidate(
        self, owner_user_id: UUID, candidate_id: UUID
    ) -> Skill:
        row = await self._skills.get_owned(owner_user_id, candidate_id)
        if row is None or row.scope != "user":
            # Treat workspace skills as not-found from this endpoint —
            # callers must use /api/v1/skills for those.
            raise NotFoundError(f"candidate {candidate_id} not found")
        if row.status != "pending_review":
            raise PersonalCandidateNotActionableError(
                candidate_id, row.status
            )
        return row


def _to_candidate_response(skill: Skill) -> PersonalCandidateResponse:
    assert skill.created_at is not None
    assert skill.updated_at is not None
    # user_id must be set when scope='user' per DB constraint; the type
    # checker doesn't know that, so assert and narrow.
    assert skill.user_id is not None
    src = skill.source_ref or {}
    hint = (skill.condition or {}).get("text") or skill.description or ""
    return PersonalCandidateResponse(
        id=skill.id,
        user_id=skill.user_id,
        hint=hint,
        diff_signature=src.get("diff_signature") or "",
        suggestion_hash=skill.suggestion_hash,
        status=skill.status,  # type: ignore[arg-type]
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )
