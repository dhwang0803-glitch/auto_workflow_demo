# PLAN_13 — Self-Evaluating Agent (closed-loop policy_extract)

> **Status**: Draft (2026-05-07) · **Owner**: dhwang0803 · **선행 PLAN**: PLAN_12 (W3 종결, 멀티모달 max pivot 완료) · **마감**: 2026-05-18 (해커톤, **11일**)

---

## 1. 동기

PLAN_12 까지 만든 파이프라인은 **단방향 DAG** 다 — `policy_extract` 가 한 chunk 를 한 번 LLM 에 던지고 그 결과를 그대로 라이브러리에 넘긴다. 이 모델의 한계는 두 가지:

1. **추출 누락 회복 불가** — 모델이 dense table 청크에서 일부 정책을 빠뜨리면 (Phase 0/1/2/3 sweep 의 systematic 누락 #8/#12/#15) 다음 단계는 그 누락을 알 길이 없다. 사람이 검토 UI 에서 발견할 때까지 묻힌다.
2. **자체 신뢰도 자가진단 부재** — `needs_clarification` 은 모델이 "내가 모호한 거 같다" 고 자기보고 하는 신호인데, 그 자체가 추가 검증 없이 그대로 통과한다. ADR-022 §8.2 의 "구체화 필요 → multi-turn 후속 질문" 약속이 docs path 에선 미구현.

memory `project_w3_then_langgraph_pivot.md` 의 결정대로, **closed-loop self-evaluating agent** 로 전환한다. 추출 → 자체평가 → 부족 시 회귀 (조정된 prompt 로 재추출) → 종료조건 만족 시 stop. PLAN_12 의 한 번 추출 모델은 이 그래프의 1-iter 한정 케이스로 자연스럽게 흡수된다.

**Why now**: Phase D 가 `policy_extract` 의 vision recall baseline (10 cands / 5-chunk sample) 을 입증했다. 이 baseline 위에서 reflection 이 **무엇을** 회복해야 하는지 측정 가능한 회귀 가드가 생겼다 — Phase D 결과가 PLAN_13 의 ground truth 역할.

## 2. 결정 — Langgraph + LangSmith 채택

**결정: langgraph (graph runtime) + LangSmith (observability).**

> **2026-05-07 결정 반전 노트** — 본 PLAN 초안은 DIY mini-state-machine 채택이었음. 그 비교표는 langgraph 의존성 비용은 잡았으면서 **observability 인프라를 새로 짓는 비용** 을 빠뜨렸음 (옵션 A trace 응답 + 옵션 B structured log + 옵션 C DB 영속화 = 우리가 직접 설계/구현/유지해야 함). 이 비용을 정직하게 비교에 포함하면 비대칭이 반대 방향 — langgraph 채택이 맞음.

| 기준 | langgraph + LangSmith | DIY |
|---|---|---|
| 의존성 비용 | `langgraph` + `langsmith` ~50MB (16.9GB GGUF 옆에서 노이즈) + Modal image 1회 rebuild ~250s | 0 |
| Observability | LangSmith 가 노드 실행 / state 변이 / 조건 분기 / 타이밍 자동 캡처. `@traceable` 로 LLM payload 까지 trace tree | 우리가 직접 — 응답 trace 필드 + structured log + (future) DB 영속화 |
| 학습 곡선 | StateGraph / 노드 함수 / 조건 엣지 — 몇 시간 | 표준 Python async + Pydantic |
| 우리가 쓸 기능 | 노드 3-5 + 조건 엣지 + state 누적 — langgraph 가 그대로 제공 | 직접 구현 |
| 시각화 / 데모 | LangSmith trace tree 가 심사위원에게 보여줄 가장 강한 시각자료. public read-only run URL 공유 가능 | mermaid 직접 export + 우리 Frontend 가 그려야 함 |
| 영상 narrative | "self-evaluating" 을 외부 도구로 입증 (third-party trust) | 우리 응답 dump 만 |

**채택 근거**:
- **Observability infra zero-cost** — `LANGCHAIN_TRACING_V2=true` + API key 만으로 즉시 동작. 우리가 trace 채널 직접 짓는 비용 (옵션 A+B+C) 이 langgraph dep 비용을 압도.
- **데모 narrative 의 시각자료 자체** — 심사위원에게 "AI 가 자기 결과를 평가해서 다시 추출했다" 를 LangSmith trace tree 로 보여주는 게 우리 Frontend 에 trace UI 직접 그리는 것보다 빠르고 신뢰성 있음.
- **노드 시그니처는 단순 async 함수** — `LLMBackend` 가 langchain LLM wrapper 와 무관하게 잘 분리돼 있어 노드 안에서 그대로 호출. Protocol 변경 없음.
- **Replay / 비교** — LangSmith 가 run 별 input/output 영속화. recall 회귀 측정 / iteration 별 비교 / prompt 튜닝 시 retro 검사 도구로 그대로 쓰임.

**개인정보 주의**: LangSmith 는 third-party SaaS — prompt/response 가 외부 송출됨. 해커톤 fixture (gitlab handbook MIT 공개) 는 무관하지만, 실고객 데이터 적용 시 self-hosted LangSmith 또는 trace sampling 결정 필요. 본 PLAN 범위 안에선 zero-issue. README/제출자료에 명시.

**Free tier 한도**: LangSmith 무료 5K traces/mo. 본 PLAN 의 Modal smoke (5-chunk × 2 mode × 2 iter ≈ 20 trace/run) + 개발 중 manual run 합쳐도 한도 한참 안쪽. 한도 임박하면 trace sampling 또는 paid tier.

## 3. 범위

### In Scope (본 PLAN, 약 5-7일)

- **langgraph StateGraph 정의** — `extract` / `self_eval` / `reflect` 3 노드 + 조건 엣지. state 는 Pydantic 모델 (langgraph 0.2+ 지원).
- **노드 1: extract** — 기존 `policy_extract.extract_policies` 의 thin async wrapper. iteration 1 은 vanilla, iteration 2+ 는 reflect 가 주입한 hint 가 system prompt 에 추가.
- **노드 2: self_eval** — 추출 결과 + 청크 (text/image) 를 받아 `EvalReport` 출력. deterministic 룰 우선 + LLM judge fallback (PR-D 에서 추가).
- **노드 3: reflect** — `EvalReport.coverage_concerns` 를 prompt hint 로 변환 (LLM 호출 없는 순수 문자열 조립).
- **조건 엣지** — `self_eval` 이후 `decision == "converge"` → END / `decision == "retry"` 이고 `iter < max_iter` → reflect / 그 외 → END.
- **종료**: `max_iter=2` (최대 1회 회귀). 무한루프 방지는 langgraph 내장 step limit 으로도 이중 보호.
- **HTTP 노출** — `POST /v1/policy/extract_reflective` (기존 `/v1/policy/extract` 와 병존 — A/B 비교 + 회귀 가드 양쪽 실행 가능). 응답에 `agent_trace` (state.iterations dump) + LangSmith run URL 포함.
- **LangSmith 통합** — `LANGCHAIN_TRACING_V2=true` + Modal Secret `langsmith-api-key` + `LANGCHAIN_PROJECT=auto-workflow-policy-extract`. backend `complete()` 를 `@traceable` 로 감싸 LLM payload 까지 trace tree 에 노출.
- **회귀 가드** — `scripts/phase_d_vision_smoke.py` 확장 또는 sibling script 가 reflective vs single-shot 양쪽 측정. LangSmith run URL 도 stdout 에 출력해 retro 비교 자산화.
- **단위 테스트** — stub backend 로 노드별 결정성 검증 + 종료조건 검증 + 무한루프 방지 검증. LangSmith 호출은 `LANGCHAIN_TRACING_V2=false` 로 테스트에서 비활성.

### Out of Scope (W4 이후 / future)

- 다른 호출 site 의 reflective 화 (`compose`, `gap_analyze`, `answers_to_skill`) — Phase D 이미 Phase F skip 결정. 이들은 입력이 정해진 단일 표현이라 self-eval 의미 약함.
- LLM-judge 모드의 cross-domain 일반화 — 본 PLAN 은 policy_extract 도메인 한정.
- 자동 prompt 진화 / RL — 정적 reflection prompt 만.
- 자동 충돌 감지, 관찰 기반 skill 후보 — ADR-022 후속 항목 그대로 유지.
- **DB 영속화 (`agent_runs` 테이블)** — Database 브랜드 작업 + workspace scope 결정 필요해 11일 안에 무거움. LangSmith 가 run-level 영속화 대체 (free tier 한도 안에서). 실제 production 에선 self-hosted LangSmith 또는 자체 DB persist 별도 PR.
- **자체 Frontend trace UI** — 본 PLAN 응답에 `agent_trace` JSON 은 제공하지만 wizard/skills 라이브러리 뷰가 그걸 시각화하는 건 W4 데모 작업. 심사위원에겐 LangSmith UI 직접 보여주는 게 우선.
- **trace sampling / PII 마스킹** — 해커톤 fixture 가 공개 자료라 본 PLAN 범위 밖. 실고객 적용 시 langsmith client 의 `redact` 후크 또는 self-hosted 전환 별도 PR.

## 4. 아키텍처

### 4.1 그래프

```
                ┌────────────┐
   start  ──►   │  extract   │
                └─────┬──────┘
                      │ drafts
                      ▼
                ┌────────────┐
                │ self_eval  │
                └─────┬──────┘
                      │ EvalReport
            ┌─────────┴──────────┐
            │                    │
   converge / max_iter        retry
            │                    │
            ▼                    ▼
        ┌───────┐         ┌───────────┐
        │ end   │         │  reflect  │── (prompt 조정) ──► extract (iter+1)
        └───────┘         └───────────┘
```

종료 분기:
- self_eval `decision == "converge"` → end
- iteration > max_iter → end (reason="max_iter_exhausted")
- reflect 가 prompt 변화 없음 (no-improvement) → end (reason="no_change")

### 4.2 State (Pydantic, langgraph 호환)

```python
from typing import Annotated
from operator import add
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END

class AgentIteration(BaseModel):
    drafts: list[SkillDraft]
    eval: "EvalReport"
    prompt_hint: str = ""  # reflect 가 다음 iter 에 주입한 힌트 (iter 1 은 "")

class AgentState(BaseModel):
    chunk: str
    images: list[str] | None = None
    domain: DomainCategory = "other"
    max_iter: int = 2
    # langgraph reducer: 노드가 [new_iter] 반환하면 누적 append
    iterations: Annotated[list[AgentIteration], add] = Field(default_factory=list)
    terminated: bool = False
    reason: str = ""  # "converge" | "max_iter_exhausted" | "no_change" | "schema_error"
```

`Annotated[..., add]` 가 langgraph 의 reducer hint — 노드는 `{"iterations": [new_iter]}` 만 반환하면 자동 append. 노드 함수 시그니처는 `async def extract(state: AgentState) -> dict` 같은 단순 형태.

### 4.3 EvalReport (self_eval 출력)

`self_eval` 은 deterministic 룰 + LLM judge 의 hybrid:

```python
class EvalReport(BaseModel):
    decision: Literal["converge", "retry"]
    coverage_concerns: list[str] = []  # 자연어, reflect 가 prompt hint 로 사용
    schema_issues: list[str] = []      # 거의 0 — _parse_response 가 이미 거름
    rationale: str = ""                # judge 의 한 줄 요약 (디버깅 + 데모용)
```

룰 (deterministic 우선, LLM 호출 절약):
1. drafts 가 빈 리스트이고 chunk 가 정책 키워드 (e.g., "must", "shall", "approve", "require") 를 포함 → retry
2. drafts 가 모두 needs_clarification=True → retry
3. iteration > 1 이고 새 drafts 가 이전과 동일 (no improvement) → converge (no_change 로 마킹)
4. 위 룰에 안 걸리면 LLM judge 1회 호출 — chunk + drafts 보여주고 "혹시 빠진 게 있느냐" 물어 자연어 hint 받음

LLM judge max_tokens 는 256 (짧은 critique) — 추가 latency ~5-10s.

### 4.4 Reflect

`reflect` 는 LLM 호출 없는 **순수 문자열 조립**:
- `coverage_concerns` 를 `_system_prompt` 끝에 "Previous pass may have missed: <list>. Re-examine the chunk." 로 합쳐서 다음 iter 의 system prompt 로 전달.
- 같은 hint 가 두 번 연속 나오면 no-improvement → converge.

### 4.5 latency 예산

| 시나리오 | 호출 수 | 토큰 | 시간 (warm) |
|---|---|---|---|
| 1-iter (가장 흔함) | extract 1회 | 4096 | ~30s |
| 1-iter + judge | extract 1회 + judge 1회 | 4096 + 256 | ~35-40s |
| 2-iter (full retry) | extract 2회 + judge 1회 | 4096×2 + 256 | ~65-70s |

Phase D 의 5-chunk sample 기준, 평균 6.1s × 2 + judge ≈ 18-20s/chunk reflective. handbook 23 chunks 풀 sweep 시 ~5-8분 — Modal 콜드 스타트 1회 + warm 처리.

#### PR-D 실측 (2026-05-07, text-mode, max_iter=2, 5-chunk sample)

| 모드 | 평균 latency | 총 candidates |
|---|---|---|
| single-shot (`/v1/policy/extract`) | 1.5s | 1 |
| reflective (`/v1/policy/extract_reflective`) | 4.0s | **4** |
| 비율 | **2.67x latency** | **4.00x recall (+3 cand)** |

text-mode 의 single-shot 1 cand 는 Phase D 결과와 일치 (text-only baseline). reflective 가 4 cand 까지 회복 — `max_iter=2` 의 1회 추가 iteration + judge 가 chunks 6/11/16 각각 +1 cand 씩 recovery. chunks 0/22 는 정책 부재 (converge 1-iter). chunk 11 은 max_iter_exhausted (judge 가 retry 결정 + budget 부족) — `max_iter=3` 으로 올리면 추가 회수 가능성. LangSmith API key sync 후 trace tree 검증은 사용자 직후 단계 (smoke 자체는 trace 없이도 측정 무영향).

### 4.6 회귀 가드

`scripts/phase_d_vision_smoke.py` 가 baseline. 새 sibling `scripts/plan_13_reflective_smoke.py` 를:
- 같은 deterministic sample (5 chunks)
- 양쪽 모드 (single-shot + reflective) 실행
- recall delta + latency delta 출력
- expected: recall ≥ Phase D baseline (1 text / 10 vision), latency ≤ 2x

목표 — reflective 가 baseline 회귀 안 시키는 것이 minimum bar. 향상은 +1 cand 라도 OK (해커톤 데모 narrative 용).

**PR-D 실측 (2026-05-07)**: text-mode 단독에서 single-shot 1 cand → reflective **4 cand (+3, 4x recovery)**. latency 2.67x (목표 ≤ 2x 살짝 초과). recall 회귀 없음 (단조 증가). vision-mode 측정은 사용자 LangSmith key sync 후 별도 단계 — 본 measurement 만으로도 데모 narrative ("AI 가 자기 결과를 다시 검토해서 +3 정책 회복") 충분.

## 5. 디렉터리 + 파일 배치

```
AI_Agent/app/agents/                          ← (신규)
├── __init__.py
├── policy_extract_agent.py                    ← StateGraph 빌더 + 노드 함수들 + compile()
├── state.py                                   ← AgentState (Pydantic + Annotated reducers)
├── eval.py                                    ← EvalReport + self_eval 룰 + judge prompt
└── tracing.py                                 ← LangSmith 설정 헬퍼 (env 없으면 no-op)

AI_Agent/app/main.py                           ← (수정) /v1/policy/extract_reflective 라우트 추가
AI_Agent/app/models/skills.py                  ← (수정) PolicyExtractReflectiveRequest/Response (agent_trace + langsmith_run_id)

AI_Agent/scripts/modal_app.py                  ← (수정) Modal Secret 에 langsmith-api-key 추가

AI_Agent/tests/test_policy_extract_agent.py    ← (신규) stub backend 로 그래프 검증 (트레이싱 OFF)
AI_Agent/tests/test_policy_extract_agent_route.py ← (신규) 라우트 통합

AI_Agent/scripts/plan_13_reflective_smoke.py   ← (신규) Modal 라이브 회귀 측정 + LangSmith run URL 출력
AI_Agent/pyproject.toml                        ← (수정) langgraph + langsmith 의존성 추가
```

**`app/agents/` 신설 이유** — `services/` 는 단일 LLM 호출 단위. agent 는 여러 서비스 조합 + state 관리라 의미가 다름. 향후 다른 reflective wrapper 추가 시 같은 디렉터리에.

**`tracing.py`** — `LANGCHAIN_TRACING_V2` env 가 없거나 false 면 `@traceable` 데코레이터를 no-op 으로 치환. 로컬/CI 에서 LangSmith 키 없이도 단위 테스트 통과.

### 5.1 의존성 추가 (pyproject.toml)

```toml
dependencies = [
    # ... 기존 ...
    "langgraph>=0.2",   # StateGraph + 조건 엣지 + reducer
    "langsmith>=0.1",   # @traceable + run URL helper. 트레이싱 비활성 시 import 만 발생, runtime 비용 0
]
```

### 5.2 환경 변수 (Modal Secret)

| 키 | 용도 | 부재 시 |
|---|---|---|
| `LANGCHAIN_TRACING_V2=true` | 트레이싱 ON 마스터 스위치 | 트레이스 미전송 (로컬/CI) |
| `LANGCHAIN_API_KEY` | LangSmith 인증 (Modal Secret `langsmith-api-key`) | 트레이싱 사일런트 비활성 |
| `LANGCHAIN_PROJECT` | LangSmith 프로젝트 명 (e.g., `auto-workflow-policy-extract`) | 기본 프로젝트로 fallback |

기존 `scripts/sync-modal-secrets.py` (PR #157 머지) 가 GCP Secret Manager 의 `langsmith-api-key-staging` 을 Modal Secret 으로 동기화. GCP 시크릿 생성은 본 PLAN 의 PR-D 작업의 외부 선행.

## 6. PR 분할

작은 단위로 머지 (`feedback_test_before_pr.md`).

| # | 내용 | 종속 |
|---|---|---|
| **(본 PR)** | PLAN_13 doc-only | — |
| **PR-A** | pyproject 의존성 (`langgraph`, `langsmith`) + `app/agents/state.py` + `app/agents/eval.py` (deterministic 룰만, LLM judge 미포함) + `app/agents/tracing.py` (no-op 폴백) + 단위 테스트 | 본 PR |
| **PR-B** | `policy_extract_agent.py` StateGraph 빌더 + extract/reflect 노드 + 조건 엣지 + 종료조건 + 단위 테스트 (stub backend, 트레이싱 OFF) | PR-A |
| **PR-C** | `/v1/policy/extract_reflective` 라우트 + request/response 스키마 (`agent_trace` + `langsmith_run_id`) + 라우트 통합 테스트 | PR-B |
| **PR-D** | LLM judge 노드 + LangSmith 통합 (`@traceable` LLM wrapper + Modal Secret 동기화) + Modal smoke (`plan_13_reflective_smoke.py`) + recall/latency 측정 + LangSmith run URL 검증 + 본 doc 측정결과 갱신 | PR-C |
| **(W4)** | 데모 시연 통합 — 영상에 LangSmith trace tree 화면 캡처 / Frontend 카드에 trace URL 링크 (옵션) | PR-D |

**PR 머지 절차**: 각 PR 은 사용자 명시 승인 전까지 머지 X (`feedback_no_auto_merge.md`).

## 7. 리스크 + 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| LLM judge 의 hallucinated coverage_concerns | reflect 가 잘못된 힌트로 노이즈만 추가 | judge prompt 에 "list ONLY the items the chunk explicitly states" 강제 + 같은 hint 반복 시 no-improvement converge 룰 |
| Modal 콜드 스타트 + 다중 호출 누적 | reflective sweep 이 비현실적으로 느림 | `scaledown_window` 충분히 큼 + smoke 는 deterministic 5-sample 만, 풀 23-sweep 은 옵션 플래그 |
| 무한루프 (max_iter 무시 버그) | 비용 폭발 + 테스트 행 | langgraph 내장 step limit (`recursion_limit`) + 우리 `iter < max_iter` 조건 엣지 이중 보호. 단위 테스트가 max+1 도달 시 raise 검증 |
| reflective 가 single-shot 대비 recall 저하 | Phase D baseline 회귀 — 데모 후퇴 | `plan_13_reflective_smoke.py` 가 양쪽 모드 회귀 비교. 회귀 발견 시 reflect prompt 튜닝 또는 본 PLAN 후퇴 (single-shot 유지 + 부분 도입) |
| LLM judge 추가 호출이 비용 / latency 두 배 | Modal 비용 + 데모 어색 | judge max_tokens=256 으로 짧게. deterministic 룰이 retry 결정 시 judge skip. 더 작은 모델 쓰는 옵션은 future |
| **LangSmith 외부 데이터 송출** | 실고객 prompt/response 가 third-party SaaS 로 — 컴플라이언스/PII 우려 | 해커톤 fixture 는 공개 자료 (gitlab handbook MIT) 라 무관. 실고객 적용 시 self-hosted LangSmith 또는 `langsmith` client `redact` 후크 또는 trace sampling. README/제출자료 명시. |
| **LangSmith free tier 5K traces/mo 한도 초과** | 트레이스 손실 + 데모 시점에 트레이스 미저장 | smoke run 은 5-chunk × 2 mode × 2 iter ≈ 20 trace/run, 마진 충분. paid tier 는 future. 한도 임박 모니터링은 LangSmith 대시보드 |
| **LangSmith 서비스 다운 / 네트워크 장애** | trace 미전송 — 본 기능 동작 자체엔 무영향이지만 데모 시 trace tree 못 보여줌 | `langsmith` client 가 ingestion 실패 시 silent log + 본 노드 실행은 계속 진행 (라이브러리 기본 동작). 데모 fallback 은 응답 `agent_trace` JSON dump 직접 보여주기 |
| **langgraph 의존성 충돌** | Modal image 빌드 / 다른 패키지와 버전 충돌 | `langgraph>=0.2,<1.0` lower bound 만 설정. 충돌 시 specific 버전 핀. PR-A 머지 전 Modal rebuild 검증 (`feedback_test_before_pr.md`) |
| 본 PLAN 이 W4 영상까지 못 끝남 | 미완성 데모 | scope cut 권한 명시 — 최소 PR-A/B/C 까지면 LangSmith trace 자체로 데모 가능. PR-D 의 LLM judge 는 시간 남으면 |

## 8. 미해결 결정 (구현 중 확정)

1. **judge 모델 = main 모델 동일?** — 같은 Gemma 4 26B-A4B 로 시작. 더 작은 모델 비교는 future.
2. **반복 prompt hint 형식** — 처음엔 단순 string concat. 효과 약하면 system prompt 의 별도 섹션 (`## Previous pass`) 으로 격상.
3. **사용자 노출 (UI)** — 본 PLAN 은 backend 응답 + LangSmith URL 까지. Frontend 가 wizard/skills 카드에 trace URL 링크 거는 건 W4.
4. **converge 후 drafts 출력 = 최종 iter 만? union? best-by-rule?** — 일단 "최종 iter 만" 으로 시작 (이전 iter 결과는 reflect 가 보강했으므로 superset 가정). 실측 보고 union 결정.
5. **Modal warm 유지 전략** — sweep 중간 콜드 스타트 회피용 keepalive ping 필요할까? PR-D 측정 보고.
6. **LangSmith run URL 의 public 가시성** — 영상에 띄우려면 run 별 share-link 필요 (LangSmith 의 public read-only). 자동 생성 가능 여부 + 설정은 PR-D 작업 중 확인.

## 9. 마일스톤 (5/7 → 5/18)

| 일자 | 작업 | 상태 |
|---|---|---|
| 5/7 (오늘) | doc PR + PR-A + PR-B + PR-C + PR-D 전부 머지 | **완료 (당일 압축)** |
| 5/8-13 | wizard flow 통합 + 라이브 데모 시나리오 (PLAN_13 종결, W4 진입) | — |
| 5/14-15 | burn-in + 제출자료 (README, 데모영상) | — |
| 5/16-17 | LangSmith trace tree 영상 캡처 + 사용자 환경 secret 등록 후 vision-mode 측정 | — |
| 5/18 | 제출 | — |

여유 +5일. PR-D 가 동일 세션 내 끝남 → W4 영상/통합 작업에 시간 확보. text-mode 만으로도 +3 cand recall recovery 입증 — 데모 narrative 확보됨.

## 10. 관련 ADR / 메모리 / 문서

- **ADR-022** (`docs/context/decisions.md`) — 부모 ADR. 본 PLAN 이 ADR-022 §8.2 (모호한 정책 multi-turn 후속) + 후속 영향의 "관찰 기반 skill 후보 / adversarial harness 자동화" 의 선행
- **PLAN_12** (`AI_Agent/plans/PLAN_12_skill_bootstrap.md`) — 본 PLAN 의 직속 부모 (W3 종결, multimodal pivot 흡수)
- 메모리 `project_w3_then_langgraph_pivot.md` — 2026-05-06 결정의 출처
- 메모리 `project_multimodal_max_pivot.md` — Phase D baseline 측정 결과 (회귀 가드 ground truth)
- 메모리 `reference_phase_d_vision_smoke.md` — 회귀 측정 재현 명령
- 메모리 `project_session_20260506_recall_recovery.md` — aggressive prompt sweep 결과 (reflective 가 회복해야 하는 누락 패턴 #8/#12/#15)
- 메모리 `feedback_test_before_pr.md` — PR 분할 시 외부 검증 선행 의무
- 메모리 `feedback_no_auto_merge.md` — 본 PLAN 의 모든 PR 은 명시 승인 전까지 머지 X

---

## 11. Agent loop refactor (ADR-024, 2026-05-09)

### 11.1 동기

§1-§10 은 **결정론적 langgraph workflow** — `extract → self_eval → reflect` 의 노드 순서를 hand-coded conditional edge (`decide_after_eval`, `decide_after_reflect`) 가 정한다. Anthropic "Building effective agents" (2024-12) 의 분류로는 **workflow 이지 agent 아님** — LLM 이 다음 행동을 결정하지 않는다.

해커톤 narrative ("**시스템이 학습한다**") 는 두 축에서 agent 자격을 요구:

1. **Storytelling 30%** — 심사위원 청중에게 "agent" 는 강한 단어다. workflow 라고 부르면 narrative 가 절반 톤 다운.
2. **Impact & Vision 40%** — PLAN_14 (HITL → personal_skill 회수) 의 자율 학습 narrative 가 "사용자 행동을 보고 시스템이 자기 출력을 조정한다" 인데, 도구 (retrieval) 를 LLM 이 자기 결정으로 부르는 모델이 있어야 그 narrative 가 일관됨. workflow 위에 persona-skill retrieval 을 hardcode 하면 "조건만 추가된 같은 자동화" 로 들린다.

### 11.2 결정

**`extract_policies` / `evaluate_coverage` / `finalize` 를 tool 로 노출하고, ReAct-style agent loop 가 LLM 의 `<tool_call>` / `<finish>` 결정으로 흐름을 운영하도록 전환**. 4 PR (α/β/γ/δ/ε/ζ) 로 분할:

| PR | 내용 | 검증 |
|---|---|---|
| **α** (본 PR) | Tool dataclass + ReAct loop + `<tool_call>`/`<finish>` parser. 기존 langgraph 미건드림 (병행) | 22 신규 unit / 223 회귀 0 |
| **β** | 기존 extract / judge / finalize → Tool 정의. `policy_extract_agent.py` 의 langgraph 제거, agent loop 으로 교체 | 라이브 smoke +3 cand recall 재현 |
| **γ** | `personal_skills` 테이블 + BGE-M3 indexing + `search_personal_skills(user_id, query)` tool | DB 마이그레이션 + tool unit |
| **δ** | `search_industry_baselines(domain, query)` tool — 시드 YAML 정책 BGE-M3 indexing | tool unit + agent 통합 |
| **ε** | `validate_skill_schema(draft)` + `cite_source_url(draft, domain)` 결정론적 tool | tool unit |
| **ζ** | D3 evidence 재캡처 (PR #168 supersede). 새 NDJSON + 새 스크린샷 + 새 README narrative | 라이브 smoke green |

### 11.3 Tool 카탈로그 (β-ε 합산 후 최종)

```
extract_policies(chunk, hint?, domain, images?)
    → list[SkillDraft]
    LLM 추출 호출. hint 비면 iter 1, 채워지면 iter 2+ retry.

evaluate_coverage(drafts, chunk)
    → {decision: "converge"|"retry", coverage_concerns: list[str], rationale: str}
    PLAN_13 §4.3 결정론적 룰 (eval.py) + 선택적 LLM judge (judge.py).
    agent 가 직접 부른다는 점만 다르고 내부 구현 그대로.

search_personal_skills(user_id, query, k=3)
    → list[SkillDraft]
    이 사용자의 과거 승인된 skill 중 BGE-M3 cosine top-k. PLAN_14 가
    채울 personal_skills 테이블 조회. 빈 결과 가능 (cold-start).

search_industry_baselines(domain, query, k=3)
    → list[{policy_id, name, sources}]
    시드 YAML 정책의 BGE-M3 indexing 위 retrieval. 도메인 표준 정책의
    grounding hint 로 prompt 주입 의도.

validate_skill_schema(draft)
    → {valid: bool, issues: list[str]}
    결정론적: condition+action 비어있지 않은지 / 길이 한계 / 형식 등.

cite_source_url(draft, domain)
    → {sources: list[{title, url}], source_kind: ...}
    YAML seed match → ADR-022 §8.4 source_kind 분류 + URL 회수.

finalize(drafts)
    → 종료 시그널. <finish> 와 동등하지만 "drafts 가 명시적 ouptut" 임을
    드러내기 위해 분리.
```

### 11.4 ReAct 와이어 포맷 (ADR-024 §3)

매 assistant turn 의 마지막 블록 정확히 1개:

```
<tool_call name="TOOL_NAME">
{...JSON args...}
</tool_call>
```
또는
```
<finish>
{...JSON 최종 결과...}
</finish>
```

Observation 은 다음 user turn 에:
```
<tool_result tool="TOOL_NAME">
{JSON serialized return value}
</tool_result>
```

Gemma 4 native tool calling 미사용 — `judge.py` 의 prompt-engineered JSON 출력 패턴과 동일 posture. 모델 의존성 추가 X.

### 11.5 Agent loop 종료 사유

| reason | 의미 |
|---|---|
| `finish` | 모델이 `<finish>` 를 emit 했다 |
| `parse_error` | 모델 출력이 `<tool_call>` / `<finish>` 어느 쪽으로도 파싱 불가 |
| `tool_not_found` | 등록되지 않은 tool 호출 (1회는 obs error 로 회복 시도, 반복 시 no_progress) |
| `max_iter_exhausted` | budget 초과. 기본 8 (search × 2 + extract × 2 + eval × 2 + finish + slack) |
| `no_progress` | 같은 (tool, args) 가 연속 2회. 모델이 stuck — max_iter 까지 안 기다리고 끊는다 |

### 11.6 회귀 가드 (PR-β 검증 전제)

PR-α 이후 agent loop 인프라만 추가됐고 langgraph 그대로. **PR-β 에서 langgraph 제거 + agent loop 로 교체 시점에** D3 라이브 smoke 의 결과 재현이 회귀 가드:

- GitLab handbook 5-chunk sample, max_iter=2 (β) 또는 8 (γ-ε 가 retrieval 추가한 후) 기준
- single-shot 1 cand vs reflective 4 cand → **+3 recall delta 유지**
- 회귀 시 prompt 튜닝 (system_goal 의 도구 사용 가이드 보강) 또는 scope 축소

`tests/fixtures/gitlab_handbook_excerpt.pdf` 의 23-chunk 분해 결과는 `services.document_parser` 결정론적 → recall 측정 ground truth 가 모델 nondeterminism 에 갇히지 않음.

### 11.7 PLAN_14 와의 연결

PR-γ (`search_personal_skills` tool + `personal_skills` 테이블) 가 PLAN_14 의 closed-loop 인프라 절반:

- PLAN_14 가 사용자 편집 diff 로 personal_skill 후보 회수 → `personal_skills` 테이블에 저장
- 다음 추출 호출 시 agent 가 자기 결정으로 `search_personal_skills` 호출 → 이 사용자의 과거 패턴이 추출 컨텍스트에 자연스럽게 포함
- 사용자는 "어 내가 보통 추가하던 게 미리 들어가있네" 자각 → ADR-023 §6 의 narrative invisibility 충족

PLAN_14 가 `personal_skills` 테이블만 채우면 본 PLAN 의 retrieval tool 이 자동으로 그 데이터를 활용. **PLAN_14 PR 수가 9 → 7-8 압축 가능** (검색 인프라 sunk cost 됐음).
