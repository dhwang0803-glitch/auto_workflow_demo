# PLAN_14 — HITL Personalization (사용자 수정 패턴 → 개인화 초안)

> **Status**: Draft (2026-05-07) · **Owner**: dhwang0803 · **선행 PLAN**: PLAN_12 (skill bootstrap), PLAN_13 (reflective agent) · **마감**: 2026-05-18 (해커톤, 11일) · **시작 예정**: D4 (5/11), D1-D3 은 별도 E2E 안정화

---

## 1. 동기

PLAN_12 가 만든 **skill bootstrap** 은 "팀의 정적 도메인 지식을 skill 화" 하는 입구였다 — docs 가 있으면 추출, 없으면 인터뷰. 일단 skill 이 들어가면 워크플로 초안 생성 시 retrieval 로 inject 된다.

여기서 한 단계 더 — **AI 가 만든 워크플로 초안을 사용자가 수정하는 행위 자체가 skill 의 새로운 원천이다**. 사용자는 "내가 매번 이렇게 고치네" 라고 자각하지 않은 채 일관된 편집 패턴을 보인다. 이 패턴이 시스템에 회수되지 않으면:

1. **사용자가 같은 수정을 매번 반복** — n8n / Zapier 가 정확히 이 지점에서 멈춰있다. 시스템이 사용자를 학습하지 않으니 매번 처음부터 같은 손맛으로 다듬어야 한다.
2. **Skill bootstrap 이 절반만 닫힘** — 정책 → skill 까지는 만들었지만, 실제 워크플로 빌드 산출물 → skill 의 회수 루프가 비어있다. ADR-022 후속 영향에 명시된 "관찰 기반 skill 후보" 의 직접 구현.

본 PLAN 은 그 회수 루프를 닫는다 — **HITL 편집 diff → personal_skill 후보 → 검토 후 활성화 → 다음 초안 생성 시 retrieval inject**. PLAN_13 의 reflective agent 가 "diff 가 일반화 가능한 패턴인가?" 를 판단하는 self_eval 역할로 그대로 재활용된다.

**Why now (해커톤 narrative)**: 평가기준 70% 가 비기술 (Impact & Vision 40 + Storytelling 30). "**팀이 시스템을 학습시키는 게 아니라, 시스템이 팀을 학습한다**" 가 차별화 핵심 메시지다. n8n 대비 차별화 #2 (팀 정책 매번 수정) 의 진짜 직격 — bootstrap 이 첫 시드라면, HITL 회수가 자가성장이다. 영상 30초 안에 "초안 v1 → 사용자 수정 → 다른 워크플로 초안에서 그 수정이 미리 반영됨" 시퀀스로 보여줄 수 있다.

**Why feasible**: 새 모델 학습 X, 새 인프라 X. 기존 자산 재활용으로 8일 안에 완결:
- skill DB / BGE-M3 retrieval / system prompt inject — PLAN_12 W3 에 깔림
- reflective agent (extract → self_eval → reflect) — PLAN_13 에 깔림. self_eval 노드가 "diff 정당화 가능?" 판단으로 직접 재활용 가능
- 워크플로 v1 (AI 초안) 은 `/v1/compose` 결과 그대로, v2 (사용자 수정본) 은 워크플로 저장 시점

## 2. 결정 — Diff-based personal skill, reflective judge 재활용

### 2.1 후보 비교

| 방법 | 동작 | 시연성 | 데이터 누적 시간 | 해커톤 적합 |
|---|---|---|---|---|
| **(a) Diff 기반** | v1 vs v2 diff → reflective agent 가 "일반화 가능한가" 판단 → personal_skill 카드로 저장 → 검토 후 활성 | diff 자체가 가장 강한 visual | 1회 수정으로 후보 1개 생성 가능 | ★★★ |
| (b) 자연어 회고 | 수정 후 "왜 바꿨어?" 직접 묻고 답변을 skill 화 | 명확하지만 UX 부담 | 즉시 | ★ |
| (c) 암묵적 통계 | 노드/매개변수 선택 빈도 누적 → 다음 초안 hint | 통계가 누적되어야 효과 | 다수 워크플로 필요 | ★ |

**결정: (a) Diff 기반**.

근거:
- **시연성** — diff 자체가 영상 화면. 사용자가 추가한 노드 / 변경한 분기 / 수정한 매개변수가 색깔로 표시되고, "다음 초안에서 미리 반영됨" 을 눈으로 본다.
- **재활용 인프라 정확히 매치** — PLAN_13 self_eval 의 deterministic 룰 + LLM judge hybrid 가 본 PLAN 의 "diff 가 일반화 가능한 패턴인가?" 판단에 그대로 fit. judge prompt 만 교체.
- **Cold-start 친화** — 사용자 1명의 1회 수정으로도 후보 1개 생성. 통계 누적 (c) 와 달리 시연 시점에 즉시 효과.
- **검토 게이트로 신뢰** — 자동 활성 X. 사용자 검토/편집/거절 후 활성. ADR-022 §11.1 "MVP 사람 검토" 정책 일관 유지.

### 2.2 ADR 위치

- **ADR-023 (신규)** 신설 예정 — "HITL 편집 회수 → personal_skill" 결정. ADR-022 §11.5 후속 영향의 "관찰 기반 skill 후보" 직접 구현. 본 PLAN doc PR 머지 시 같이 추가.

## 3. 범위

### In Scope (본 PLAN, ~8일)

- **워크플로 revision 캡처** — 워크플로 저장 시 직전 AI 초안 (v1) 과 함께 저장. 새 테이블 `workflow_revisions` (workflow_id FK + revision_no + source: "ai_draft" | "user_edit" + payload JSONB + parent_revision_id).
- **Diff 추출** — v1 vs v2 의 노드 단위 diff (added / removed / modified). text diff 가 아니라 워크플로 schema 의 노드/엣지 단위 semantic diff. `services/workflow_diff.py` 에 순수 함수.
- **Personal skill 도메인** — 기존 skill 테이블에 `scope: "workspace" | "user"` 컬럼 추가 + `user_id` 옵션. retrieval 시 워크스페이스 skill ∪ 현재 사용자 personal skill 을 같은 풀에 합쳐 BGE-M3 검색.
- **Diff → personal_skill 후보** — `agents/personalization_agent.py` 신설. PLAN_13 reflective agent 패턴 재활용:
  - 노드 1: `propose` — diff 와 v1 컨텍스트를 받아 "이 변화의 일반화 hint" 생성 (LLM 호출 1회, max_tokens 256)
  - 노드 2: `judge` — proposal 이 (i) 일반화 가능 (ii) 모순 없음 (iii) 1회성 noise 가 아님 을 판단. PLAN_13 LLM judge 와 동일 패턴.
  - 종료: 통과하면 personal_skill 후보 row 생성 (status="pending_review"), 실패하면 drop + 사유 로그.
- **검토 UI** — Frontend Library 에 "Suggested from your edits" 섹션. 사용자가 (a) 활성 (b) 편집 후 활성 (c) 거절. 거절은 같은 패턴 재추천 억제 hash 로 기록.
- **Retrieval inject** — `/v1/compose` 가 BGE-M3 검색 시 workspace skill 과 active personal_skill 을 **같은 후보 풀에 합쳐** top-K 선정. 시스템 프롬프트도 단일 "## Skills" 섹션 — personal vs workspace 구분 표시 X. 사용자가 자기 손맛이 자연스럽게 녹은 초안을 받는 경험 자체가 narrative ("시스템이 사용자를 학습한다" 의 invisibility 가 핵심).
- **HTTP 노출** — `POST /v1/personalization/extract_from_diff` (workflow_revisions 두 개 받아 후보 생성, agent_trace 포함). `GET /v1/personalization/candidates` (현재 사용자 pending 후보).
- **회귀 가드** — `scripts/plan_14_personalization_smoke.py` — fixture 사용자/워크플로/edit pair 로 후보 생성 + 검토 활성 + 다음 초안 생성 시 inject 확인 + LangSmith trace 검증.
- **단위 테스트** — diff 함수 결정성 + agent 노드별 검증 + retrieval scope 격리 (사용자 A 의 personal skill 이 사용자 B 초안 생성에 누출 X) + 시스템 프롬프트 inject 단면 테스트.

### Out of Scope

- **다중 사용자 cross-team 학습** — personal_skill 의 workspace 공유 / opt-in 공유는 future. 본 PLAN 은 user-scope 한정.
- **자동 활성 (검토 생략)** — 본 PLAN 은 사람 검토 게이트 유지. 신뢰 임계 누적 후 auto-activate 는 future.
- **Conflict resolution** — workspace skill 과 personal skill 이 모순될 때의 자동 해소. 본 PLAN 은 LLM 의 컨텍스트 우선순위 판단에 위임 (system prompt 에 "personal overrides workspace when conflicting" 명시) + 실측 후 정교화.
- **시간적 감쇠** — 오래된 personal_skill 의 자동 retire / 가중치 감소. 본 PLAN 은 status 만 (active / archived). 감쇠는 future.
- **Diff 자체의 reflective 회귀** — diff 추출 단계에 reflective 적용은 X. PLAN_13 의 self_eval 패턴은 propose+judge 로만 재활용.
- **Database 브랜드의 본격 마이그레이션 도구화** — 본 PLAN 은 `workflow_revisions` 테이블 추가 + skill 테이블에 컬럼 2개 추가 가 전부. 기존 alembic 절차 그대로.
- **Multimodal 워크플로 diff** — 노드/엣지 schema 단위 diff 만. 노드 안의 이미지/첨부파일 변경은 future.

## 4. 아키텍처

### 4.1 데이터 흐름

```
사용자 자연어 요청
    │
    ▼
/v1/compose (AI 초안 생성)
    │   │  ── workspace skill + active personal_skill retrieval inject
    │   │
    │   └──► workflow_revisions row
    │           (revision_no=1, source="ai_draft", payload=v1)
    ▼
Frontend 워크플로 편집기
    │
    │   사용자 수정
    ▼
워크플로 저장
    │
    │   └──► workflow_revisions row
    │           (revision_no=2, source="user_edit", parent=1, payload=v2)
    │
    │   비동기:
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
    │   사용자 검토 (활성 / 편집 / 거절)
    ▼
personal_skill (status="active")
    │
    ▼
다음 /v1/compose 시 retrieval 풀에 포함
```

### 4.2 Personalization agent 그래프

```
                ┌────────────┐
   start  ──►   │  propose   │   diff + v1 컨텍스트 → 일반화 hint (LLM, max_tokens=256)
                └─────┬──────┘
                      │ ProposalDraft
                      ▼
                ┌────────────┐
                │   judge    │   PLAN_13 judge 패턴 재활용 — (i) 일반화 (ii) 모순 (iii) noise 판단
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

종료 분기:
- judge `decision == "accept"` → 후보 row 저장
- judge `decision == "reject"` + reason → 드롭 + structured log (반복 추천 억제 hash 도)
- propose 가 빈 출력 → drop with reason="empty_proposal"

`max_iter=1` — reflect 루프 없음. judge 가 거절하면 즉시 종료. (PLAN_13 reflective 패턴 중 propose+judge 만 채택, reflect 단계 미채택 — diff 가 noise 면 반복해도 노이즈)

### 4.3 데이터 모델

```python
# Database/migrations 신규
class WorkflowRevision(Base):
    id: UUID (PK)
    workflow_id: UUID (FK)
    revision_no: int           # 1부터 증가
    source: Literal["ai_draft", "user_edit"]
    payload: JSONB             # WorkflowSchema 전체 직렬화
    parent_revision_id: UUID | None  # diff 비교 대상
    created_at: datetime
    created_by: UUID (FK users) | None  # ai_draft 면 None

# 기존 skill 테이블 (컬럼 추가)
class Skill(Base):
    # ... 기존 컬럼 ...
    scope: Literal["workspace", "user"]    # 신규, default "workspace"
    user_id: UUID | None                    # scope="user" 일 때만
    source: Literal["docs", "wizard", "hitl_edit"]  # 신규
    suggestion_hash: str | None             # hitl_edit 후보의 중복 추천 억제용

class PersonalSkillReview(Base):
    """후보 검토 이력 — 사용자별 일관 누적되는 결정 기록"""
    id: UUID (PK)
    user_id: UUID (FK)
    suggestion_hash: str
    action: Literal["accept", "edit", "reject"]
    rejection_reason: str | None
    created_at: datetime
```

`PersonalSkillReview` 가 별도 테이블인 이유 — **사용자별 검토 결정이 일관 누적되는 영속 기록**. Claude Code 프로젝트별 `MEMORY.md` 와 같은 위치 — 사용자가 어떤 패턴을 받아들이고 거절했는지가 시간에 걸쳐 쌓이고, 그 자체가 사용자 모델의 일부. skill 행에 review 필드를 누적하는 대신 별 테이블로 둠으로써:
- 거절된 후보 (skill row 안 만듦) 도 hash 기록으로 재추천 억제
- 사용자 결정 이력의 시간순 query 가능 (e.g., "최근 3개월 거절 패턴")
- 한 사용자의 검토 분포가 다른 사용자에게 옮겨가지 않음 (테이블 단위 격리 자연)

`suggestion_hash` 산정: judge 가 통과시킨 proposal 의 일반화 hint 텍스트 + diff signature (node types added/removed) 의 SHA256 prefix. 같은 hash 가 이미 reject 됐으면 후보 생성 단계에서 drop.

### 4.4 Diff 함수 — semantic, not text

```python
def diff_workflow(v1: WorkflowSchema, v2: WorkflowSchema) -> WorkflowDiff:
    """노드/엣지 단위 비교. text diff 아님."""
    return WorkflowDiff(
        nodes_added=[...],     # v2 only
        nodes_removed=[...],   # v1 only
        nodes_modified=[       # 같은 id, 다른 params
            NodeChange(id=..., before=..., after=..., changed_keys=[...])
        ],
        edges_added=[...],
        edges_removed=[...],
        ordering_changed=bool, # 토폴로지 sequence 변화
    )
```

매칭 규칙: 노드 id 보존 (Frontend 가 수정 시 id 유지), 신규 노드는 id 생성. 매개변수 비교는 deep equality + 변화 키 추출. text diff (e.g., 노드 이름 안의 typo 수정) 는 일반화 가치 낮으므로 본 PLAN 범위 밖 — `nodes_modified` 의 `changed_keys` 가 비어있으면 noise 로 간주, propose 단계에서 drop.

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

**Judge** (PLAN_13 judge.py 패턴 재활용):
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

`max_tokens` propose=256 / judge=128. 둘 다 `@traceable` (PLAN_13 LangSmith 통합 그대로 사용).

### 4.6 Retrieval inject 변화

기존 BGE-M3 retrieval (PLAN_12 W3-3) 의 풀 확장:
- 쿼리: 사용자 자연어 요청
- 후보 풀: workspace skills (scope="workspace") ∪ active personal skills of current user (scope="user", user_id=current, status="active") — **같은 풀에 합쳐** top-K 5 선정
- 격리 — 사용자 A 의 personal skill 이 사용자 B 검색 풀에 절대 들어가지 않음 (retrieval 쿼리 단계에서 user_id filter)

System prompt 조립 — **단일 "## Skills" 섹션**:
```
## Skills
- {skill 1}
- {skill 2}
- {skill 3}

(기존 prompt 본체)
```

분리 표시 (`## Workspace` / `## Personal`) 안 함. 이유:
- **Narrative invisibility 가 핵심** — "시스템이 사용자를 학습한다" 는 사용자가 자기 손맛이 자연스럽게 녹은 초안을 받고 "어 내가 보통 추가하던 거네" 자각하는 순간이 가장 강함. 분리해서 "이건 당신의 personal preference 입니다" 라고 LLM 에 명시하면 invisibility 가 깨짐
- **충돌은 본 PLAN Out of Scope** — 분리 표시의 주된 효용 (LLM 우선순위 판단) 은 충돌 해소가 본 PLAN 범위 밖이라 손해 없음
- **단순화** — PR-F 부담 감소. 단일 섹션이라 PR-F 가 retrieval 풀 확장만으로 끝남

Frontend 의 generated draft 노드 옆 "당신의 패턴" 배지 같은 visibility 옵션은 §8 미해결 결정으로 (시간 남으면 PR-H 에 옵션).

### 4.7 latency 예산

| 시나리오 | 호출 | 시간 (warm) |
|---|---|---|
| 워크플로 저장 시 (revision 기록만) | DB only | < 100ms |
| Diff 추출 후 후보 생성 (propose+judge) | LLM 2회 (256+128 tok) | ~10-15s |
| 후보 활성 후 다음 compose | 기존 compose (skill 풀 +N), inject 토큰 +200-400 | 기존 + 0 (retrieval 비용 무관) |

후보 생성은 워크플로 저장 비동기 후처리 (Celery / Modal background) — 사용자 인터랙션 차단 X.

### 4.8 회귀 가드

`scripts/plan_14_personalization_smoke.py` (PLAN_13 smoke 패턴 재활용):
1. fixture 사용자/워크플로 v1 로드
2. fixture 편집 적용 → v2
3. `/v1/personalization/extract_from_diff` 호출 → 후보 생성 검증
4. 후보 활성 (script 내 직접 DB update)
5. 같은 사용자로 새 자연어 요청 `/v1/compose` → 시스템 프롬프트에 personal skill 포함 검증
6. 다른 사용자 `/v1/compose` → personal skill 누출 X 검증 (격리 가드)
7. LangSmith trace tree 의 propose/judge 노드 표시 확인

목표 — fixture 시나리오 100% 통과 + 격리 가드 100%. 양수 지표는 영상 narrative 전용 (정량 측정보다 시연 자체가 자산).

## 5. 디렉터리 + 파일 배치

```
AI_Agent/app/agents/
├── personalization_agent.py        ← (신규) propose+judge 그래프 + 노드 함수
├── state.py                         ← (수정) PersonalizationState 추가 (or 별도 파일)
└── eval.py                          ← (수정) judge 헬퍼 일반화 (PLAN_13 의 정책 judge 와 분리)

AI_Agent/app/services/
├── workflow_diff.py                 ← (신규) semantic diff (순수 함수)
└── personalization_service.py       ← (신규) 후보 생성 오케스트레이션 (agent 호출 + DB write)

AI_Agent/app/main.py                 ← (수정) /v1/personalization/* 라우트
AI_Agent/app/models/personalization.py ← (신규) Pydantic 스키마
AI_Agent/app/prompts/personalization/ ← (신규) propose/judge 템플릿

API_Server/api/v1/personalization/   ← (신규) Frontend 노출 라우트 (AI_Agent 프록시)
API_Server/services/workflow_revisions.py ← (신규) 워크플로 저장 시 revision 기록 hook

Database/migrations/                 ← (신규) workflow_revisions 테이블 + skill scope/user_id/source/suggestion_hash 컬럼 + personal_skill_reviews 테이블
Database/models/workflow_revision.py ← (신규)
Database/models/skill.py             ← (수정)

Frontend/src/components/library/SuggestedFromEdits.tsx ← (신규) 후보 검토 UI

AI_Agent/scripts/plan_14_personalization_smoke.py ← (신규) 라이브 회귀 측정
AI_Agent/tests/test_workflow_diff.py             ← (신규)
AI_Agent/tests/test_personalization_agent.py     ← (신규, stub backend, 트레이싱 OFF)
AI_Agent/tests/test_personalization_route.py     ← (신규)
API_Server/tests/test_workflow_revisions.py      ← (신규)
```

**`workflow_diff.py` 가 `services/` 인 이유** — LLM 호출 없는 순수 함수. agent 의존성 없는 데이터 변환.

**`agents/eval.py` 일반화** — PLAN_13 의 policy_extract judge 와 본 PLAN 의 personalization judge 가 모두 "LLM 이 JSON decision 출력" 패턴이라, 프롬프트만 다른 호출 헬퍼로 분리. 함수 증식 지양 (`feedback_avoid_function_sprawl.md`) — 1회성 wrapper 가 아니라 두 사용처가 명확하면 추상화.

## 6. PR 분할

작은 단위로 머지. 각 PR 머지는 사용자 명시 승인 (`feedback_no_auto_merge.md`).

| # | 내용 | 종속 | 브랜드 |
|---|---|---|---|
| **(본 PR)** | PLAN_14 doc + ADR-023 | — | AI_Agent (plan 위치) |
| **PR-A** | Database 마이그레이션 — `workflow_revisions` + skill scope/user_id/source/suggestion_hash + `personal_skill_reviews` + 기본 ORM 모델 + 단위 테스트 | 본 PR | Database |
| **PR-B** | API_Server — `/api/v1/workflows/<id>` 저장 hook 이 revision 기록 + revision 조회 endpoint + 단위 테스트 | PR-A | API_Server |
| **PR-C** | AI_Agent — `services/workflow_diff.py` (순수 함수) + 단위 테스트 (semantic diff 결정성) | PR-A | AI_Agent |
| **PR-D** | AI_Agent — `agents/personalization_agent.py` (propose+judge 그래프) + LangSmith @traceable + stub backend 단위 테스트 (트레이싱 OFF) | PR-C | AI_Agent |
| **PR-E** | AI_Agent — `/v1/personalization/extract_from_diff` 라우트 + `personalization_service.py` (오케스트레이션) + 라우트 통합 테스트 | PR-D | AI_Agent |
| **PR-F** | AI_Agent — **scope 축소** (2026-05-12): retrieval 풀 inject 및 단일 `## Skills` 섹션 표시는 PLAN_15 PR-γ (#172 `search_personal_skills` tool) 로 흡수됨. 본 PR 은 잔여 — route-level cross-user 격리 통합 테스트 (alice/bob 둘 다 file 보유 시 누출 X) 만. 단위 가드 (path traversal / anonymous / cold-start) 는 PR-γ 단위 테스트로 보장 | PR-E | AI_Agent |
| **PR-G** | API_Server — `/api/v1/personalization/*` (extract_from_diff + list/activate/reject candidates) + Database SkillRepository `user_id`/`source`/`suggestion_hash` 확장 + 신규 `PersonalSkillReviewRepository` | PR-E | API_Server + Database |
| **PR-H** | Frontend — Library "Suggested from your edits" 섹션 + 활성/편집/거절 UI + 워크플로 저장 시 revision 자동 기록 클라이언트 보강 | PR-G | Frontend |
| **PR-I** | (1) modal_app.py `personal_memory_volume` mount + `PERSONAL_MEMORY_DIR` env (PR-D/E/G 가 누락한 인프라) — (2) AI_Agent `POST /v1/personalization/memory/upsert` (file write + BGE-M3 embedding) — (3) API_Server `PersonalizationService.activate_candidate` 가 best-effort upsert 호출 (closes DB↔JSON sync gap) — (4) Modal smoke (`plan_14_personalization_smoke.py`) PLAN_14 §4.8 5단계 + 단위 테스트 (writer 8 + route 5 + activate sync 2) — (5) 본 doc 측정결과 갱신 | PR-F + PR-H | AI_Agent + API_Server |
| **(W4)** | 영상 시연 캡처 + writeup 통합 — 본 PLAN 의 narrative ("시스템이 사용자를 학습한다") 를 30초 시퀀스로 | PR-I | docs |

총 9 PR. 각 PR 평균 0.5-1일. 8일 안에 직렬로 끝나는 페이스. PR-C 와 PR-B 는 PR-A 머지 후 병렬 가능 (Database 결정만 공유).

**스코프 컷 권한 명시**: 시간 부족 시 PR-F (격리 가드) 까지가 최소선 — 영상에 personal skill 한 개만 활성하고 다음 초안 inject 보여주면 narrative 성립. PR-G/H/I 가 안 끝나면 라이브 시연 대신 Frontend mock + LangSmith trace 보여주는 fallback.

## 7. 리스크 + 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| **1회성 noise 가 personal_skill 로 잘못 등극** | 다음 초안에 잘못된 hint 주입 → 사용자 신뢰 하락 | 사용자 검토 게이트 (자동 활성 X) + judge 의 "one-off correction" 룰 + reject hash 로 같은 패턴 재추천 억제 |
| **Personal skill ↔ Workspace skill 충돌** | 모순된 hint 가 동시 inject → LLM 혼란 | 본 PLAN Out of Scope. 합쳐서 inject 하는 단순 풀 모델이라 충돌은 사용자 검토 단계에서 잡거나 LLM 의 자연 통합에 위임. 충돌 빈번 시 future PR 에서 reranker / scope 표시 도입 |
| **Diff 추출이 워크플로 schema 변화에 깨짐** | 새 노드 타입 추가 시 diff 가 잘못된 added/removed 보고 | semantic diff 가 노드 type 을 안 보고 id+params 만 봄 — 신규 type 도 자연 흡수. schema 변경 시 단위 테스트가 회귀 잡음 |
| **Cold-start (기존 사용자 없음)** | 시연 시점에 personal skill 0 → narrative 약화 | fixture 시나리오 — 사용자 1명, 워크플로 v1, 1회 수정 → personal skill 1개 → 다른 자연어 요청에서 inject. 시연 자체가 cold-start 케이스 |
| **Privacy — personal skill 의 다른 사용자 누출** | 사용자 A 의 편집 패턴이 B 의 초안에 노출 → 데이터 격리 위반 | retrieval 쿼리에 user_id filter 강제 + 단위 테스트 (격리 가드 PR-F) + 격리 깨지면 PR-F 머지 차단 |
| **LLM judge 의 false-positive (noise 를 accept)** | 잘못된 personal skill 후보가 사용자 검토 큐에 쌓임 | judge prompt 의 reject 룰 명시 + suggestion_hash 로 반복 누적 차단. 사용자 검토가 최종 게이트라 final 영향 0 |
| **Reflective agent 재활용 가정이 깨짐 (judge 가 정책 도메인 외 일반화 못함)** | propose+judge 가 의도대로 동작 X | judge prompt 가 정책 judge 와 별도 (재활용은 호출 헬퍼 + state 패턴만). 실패 시 fallback — `decision="accept"` 고정 + 사람 검토에 100% 위임 (해커톤 시연엔 충분) |
| **Modal 콜드 스타트 + 후보 생성 비동기** | 사용자 저장 후 후보 생성까지 30s+ 지연 | 비동기 후처리이므로 인터랙션 차단 X. 시연 시 미리 한 번 warm-up 호출. 영상은 활성 후 효과 보여주는 데 집중 |
| **Database 마이그레이션이 staging schema pollution** | flakiness 8건 (`project_test_flakiness_debt.md`) 악화 | 새 테이블만 추가 (기존 테이블 컬럼 추가는 nullable + default). pollution 모니터링은 PR-A smoke 후 |
| **본 PLAN 이 W4 영상까지 못 끝남** | 미완성 데모 | 스코프 컷 — PR-F 까지면 LangSmith trace + DB query 만으로 데모 가능. PR-H 의 Frontend UI 는 시간 남으면 |

## 8. 미해결 결정 (구현 중 확정)

1. **Diff 추출 단위의 정밀도** — 노드 매개변수 deep equality 가 너무 엄격 (label 변경 같은 noise 도 `nodes_modified` 로 잡힘) 하면 propose 단계 drop 룰 강화. 실측 후.
2. **Personal skill 의 시간적 감쇠** — 본 PLAN 은 active/archived 만. 자동 retire 임계는 future. 단 시연용으로 manual archive 버튼 정도는 PR-H 에 포함 검토.
3. **Workspace 공유 (opt-in)** — 사용자가 "이 personal skill 을 팀에 공유" 버튼. 본 PLAN 은 user-scope 한정. ADR-022 §11.5 의 다중 멤버십 future 와 같이 묶어 후속.
4. **Compose 시 personal skill inject 효과** — 합쳐서 단일 풀 inject 가 의도대로 동작하는지 (LLM 이 personal skill 을 사용자 손맛으로 자연 흡수). 실측에서 무시되거나 과적용 시 reranker / scope 표시 / 가중치 도입 검토. Frontend "당신의 패턴" 배지 같은 visibility 도 narrative 효과 측정 후 옵션.
5. **Reject 사유의 사용자 표시** — judge 가 거절한 후보를 사용자에게 안 보여주는 게 기본. "거절된 제안 보기" 토글 옵션은 future.
6. **Suggestion_hash 의 충돌** — SHA256 prefix 길이 (16 char) 면 충돌 확률 무시 가능하지만, 서로 다른 패턴이 같은 hash 면 잘못 억제. 실측 시 충돌 발견되면 hint 텍스트도 hash 입력에 포함.

## 9. 마일스톤 (5/11 → 5/18)

> 5/8-10 (D1-D3) 은 별도 E2E 안정화 작업. 본 PLAN 은 D4 부터.

| 일자 | 작업 | 상태 |
|---|---|---|
| 5/11 (D4) | doc PR + ADR-023 + PR-A (DB 마이그레이션, PLAN_15 PR-γ #171 흡수) + PR-Ba (#176) + PR-B (#177) | ✅ |
| 5/12 (D5) | PR-C (#178 semantic diff) + PR-D (#179 personalization agent) + PR-E (#180 extract_from_diff) + PR-F (#181 cross-user 격리 가드, scope 축소) | ✅ |
| 5/13 (D6) | PR-G (#182 API_Server `/api/v1/personalization/*` 프록시 + DB write — SkillRepository 확장 + PersonalSkillReviewRepository 신규 + 14 route tests) | ✅ |
| 5/14 (D7) | PR-H (#183 Frontend "Suggested from your edits" UI + workflow save → revision_source + auto-trigger extract — 4 새 Playwright + tsc/lint/build green) | ✅ |
| 5/14 (D7+) | **PR-I — modal_app personal_memory volume + `/v1/personalization/memory/upsert` + activate sync + smoke + ADR-023 갱신** (DB↔JSON write path gap 발견 → scope 폭발) | 진행 중 |
| 5/15-17 | 영상 시연 캡처 + writeup | — |
| 5/18 (D11) | 제출 (버퍼) | — |

여유 1일. 스코프 컷 트리거: D7 까지 PR-F 머지 못 하면 즉시 컷 — Frontend UI 포기, LangSmith trace + DB query 데모로 fallback.

## 10. 관련 ADR / 메모리 / 문서

- **ADR-022** (`docs/context/decisions.md`) — 부모 ADR. 본 PLAN 은 ADR-022 §11.5 후속 영향의 "관찰 기반 skill 후보" 직접 구현
- **ADR-023 (신규)** — 본 PLAN doc PR 머지 시 같이 추가. "HITL 편집 회수 → personal_skill" 결정
- **PLAN_12** (`AI_Agent/plans/PLAN_12_skill_bootstrap.md`) — skill DB / retrieval / inject 인프라 부모
- **PLAN_13** (`AI_Agent/plans/PLAN_13_LANGGRAPH_AGENT.md`) — propose+judge 패턴 + LangSmith 통합 부모
- 메모리 `project_skill_bootstrap_design.md` — skill bootstrap 통합 파이프라인 (본 PLAN 이 회수 루프 닫음)
- 메모리 `project_plan_13_reflective_agent.md` — judge 패턴 재활용 출처
- 메모리 `project_gemma4_hackathon.md` — 평가기준 70% 비기술 → narrative 무게중심 근거
- 메모리 `feedback_test_before_pr.md` — 외부 검증 (Modal rebuild, DB 마이그레이션) 선행 의무
- 메모리 `feedback_no_auto_merge.md` — PR 머지는 사용자 명시 승인 후
- 메모리 `feedback_avoid_function_sprawl.md` — `agents/eval.py` 일반화 시 thin wrapper 회피
- 메모리 `project_test_flakiness_debt.md` — DB 마이그레이션 후 pollution 모니터링 의무
