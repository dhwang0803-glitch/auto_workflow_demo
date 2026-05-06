# Gemma 4 reasoning trace — 실험 노트 + 재현 가이드

PLAN_12 W3-4 후속 진단 (2026-05-05 후반 세션). max_tokens 1024→4096 으로 90% 도달 후 잔여 *2건 stochastic 실패* 와 *dense chunk 의 30-77s latency* 의 근본 원인을 추적해 도달한 결과.

> 전체 세션 narrative + 메모리 인덱스: auto-memory `reference_gemma4_reasoning_trace.md`, `project_session_20260505_reasoning_root_cause.md`. 이 문서는 codebase 내부에서 *실험을 이어서 진행* 할 때의 진입점이다.

## 1. 결과 요약

| 지표 | reasoning ON (4차/5차) | reasoning OFF (6차) | 변화 |
|---|---|---|---|
| 총 wall time | ~600s (10분) | **~55s** | **-91%** |
| 실패율 | 10% (4차) → 0% (5차 budget 4096 후) | **0%** | |
| 평균 dense chunk | 30-77s | 2.8-5.5s | -90% |
| 평균 빈 응답 chunk | ~10-30s | **0.7-1.0s** | -97% |
| candidates 수 | 14-19 (cycle 변동) | **10** | **-29% recall** |

20 chunk fixture (`tests/fixtures/gitlab_handbook_excerpt.pdf`) 기준.

## 2. 근본 원인

Gemma 4 26B-A4B 는 hidden reasoning model. `<think>...</think>` 트레이스를 emit 하고, llama-server 의 chat-template 파서가 그 트레이스를 visible `choices[0].message.content` 에서 자동 stripping. 사용자/caller 는 못 보지만 GPU 시간과 max_tokens budget 은 그대로 소비.

**결정적 증거** (Modal 로그 한 줄):

```
eval time = 76063.81 ms / 3832 tokens (50.38 tok/s)
slot release: stop processing: n_tokens = 4484, truncated = 0
```

같은 호출의 응답 본문: `len(content) = 655` (~165 tokens).

생성 3832 토큰 / visible 165 토큰 = **3667 토큰이 chat-template 에서 strip**.

## 3. 적용된 fix

`app/backends/llamacpp_gemma.py::_chat_payload`:

```python
"chat_template_kwargs": {"enable_thinking": False},
"reasoning_format": "none",
```

두 필드 동시 송신 — llama-server 빌드별로 어느 키를 인식할지 달라서. 모르는 키는 silently 무시.

## 4. 미해결 trade-off

candidates **14 → 10** (-29%). 사라진 후보:

| chunk | 5차 (reasoning ON) | 6차 (reasoning OFF) |
|---|---|---|
| #8 | "Allocate Compute Minutes by tier" | (없음) |
| #12 | "Request CustomersDot Admin Access" | (없음) |
| #15 | "format_zuora_access_requests" | (없음) |
| #18 | "exclude_out_of_scope_requests_from_queue" | (없음) |

모두 명확한 정책 — reasoning 없으면 모델이 보수적으로 해석해서 거름. interview path 가 사용자로부터 보충하지만 docs path 단독 가치는 떨어짐.

## 5. 실험 결과 (2026-05-06 종결)

> **결정: 옵션 D 의 변형이 winner — system prompt 에 "high recall over precision" bias 추가**.
> 본 §5.1 의 데이터로 옵션 D 가 docs path 단독 recall 을 10 → 16 으로 회복하면서 latency 는 default 그대로 유지함을 확인. Phase 3 burn-in PR 에서 default `_system_prompt` 에 흡수 + Phase 1 계측 surface 전부 제거.

### 5.1. Phase 0 / Phase 2 측정 데이터

3 사이클 진행:

- **Phase 0 (baseline 분산)**: smoke 3회 동일조건 → 10 candidates / 2 needs_clarif / 41s warm. 청크별 분포 + 추출 텍스트가 byte-identical → **결정적 (variance = 0)**. 누락 4건은 stochastic 이 아닌 systematic conservatism — 재시도/리트라이로 회수 불가능.
- **Phase 1 (계측 surface, PR #154)**: `/v1/policy/extract` 가 4 개 실험용 request 필드 수용 (`system_prompt_override`, `enable_thinking`, `temperature`, `include_raw`). 스모크 스크립트도 `--strictness {default,aggressive,lenient}` / `--enable-thinking` / `--temperature` flag. 1회 redeploy 로 이후 모든 sweep 가 client-side iteration.
- **Phase 2 (스윕)**: 7 셀.

| 셀 | strictness | thinking | temp | candidates | needs_clarif | wall (s) |
|---|---|---|---|---|---|---|
| S0 | default | OFF | 0 | 10 | 2 | 64 |
| **S1** | default | **ON** | 0 | **14** | 3 | **634** |
| **S2** | **aggressive** | OFF | 0 | **16** | 2 | **56** |
| S2' | aggressive | OFF | 0 | 16 | 2 | 54 |
| S3 | lenient | OFF | 0 | 16 | **9** | 77 |
| S4a/b/c | default | OFF | 0.4 | 15-16 | 2 | 50-54 |
| S5 | aggressive | OFF | 0.4 | 15 | 3 | 52 |

핵심:

- **Aggressive prompt (S2)** 가 ground truth (S1, 14) 보다 raw count 더 많고 (16) latency 는 default 와 동등. count 결정적 (S2 ↔ S2' 청크별 분포 동일).
- **Temperature 단독 (S4)** 도 ~16 도달하지만 variance 있음 + #11 같은 boundary 후보 누락. Aggressive 의 결정성이 더 유리.
- **결합 (S5)** 은 손해 — temp 가 aggressive 의 결정적 #11 finding 을 흩뜨림.
- **Lenient (S3)** 는 needs_clarification 폭증 (9) 으로 review UI 부하 큼. 비효율.
- **Reasoning ON 만 회복하는 3건** (#8 Allocate Compute Minutes, #12 CustomersDot Admin, #15 Zuora format) — 모두 dense reference table 파싱이 필요. prompt/sampling 으론 해결 불가. 해커톤 demo 에서는 충분한 다른 후보가 있어 받아들임.
- **Aggressive 가 추가로 잡는 것** (#11 Join reviewers group, #18 dense exclusion 5-way split) — reasoning ON 도 못 한 것. 정성적으로도 가치 있음.

### 5.2. 채택된 변경 (Phase 3 burn-in)

- `_system_prompt` 끝에 짧은 "## Bias" 절 추가 — "When in doubt, INCLUDE the candidate with needs_clarification=true rather than dropping it."
- Phase 1 의 4 개 request 필드 / 응답 `raw` / 스모크 flag 모두 제거 (해커톤 main 청결 유지)
- 기타 인프라 (`enable_thinking=False`, `temperature=0.0`, `reasoning_format=none`) 그대로

### 5.3. 폐기된 옵션 (참고용)

### 옵션 A: 현 fix 유지 + recall 회수 보류

해커톤 데모 우선. 인터뷰 path 가 자연스럽게 보충. 코드 변경 없음.

### 옵션 B: `--reasoning-budget N` (server-level, 균형)

`scripts/modal_app.py::start_llama_server` cmd 에 추가:

```python
cmd = [
    "/usr/local/bin/llama-server",
    ...,
    "--reasoning-budget", os.environ.get("REASONING_BUDGET", "256"),
]
```

→ 모델이 N 토큰까지만 reasoning 후 visible 출력 시작. N 튜닝:
- 0: 현 fix 와 동일 (10/10 candidates 잡지만 recall 14→10 그대로)
- 256: ~5s/chunk, recall 12-13 추정
- 512: ~10s/chunk, recall 13-14 추정
- -1 (unlimited): 원본 거동, recall 14-19 / 30-77s/chunk

`chat_template_kwargs` 와 충돌 가능 — 동시에 적용 시 시도해보고 `chat_template_kwargs` 빼야 할 수도.

**재현 명령** (Modal 재배포 + smoke):

```powershell
$env:REASONING_BUDGET = "256"  # llama-server flag 로 전달
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 modal deploy AI_Agent/scripts/modal_app.py
# Wait for "App deployed" (~4분)
cd AI_Agent
$env:AGENT_URL = "https://dhwang0803--auto-workflow-agent-agentservice-fastapi.modal.run"
$env:AGENT_BEARER_TOKEN = $(gcloud secrets versions access latest --secret=agent-bearer-token-staging --project=autoworkflowdemo)
python scripts/smoke_handbook_policy_extract.py | Tee-Object /tmp/smoke_budget_256.txt
```

비교 지표: 총 wall time, candidates 수, dense chunks (#4, #13) latency.

### 옵션 C: 2-pass (구조적)

`policy_extract` 호출 측 (API_Server) 에서:

1. 1차: reasoning OFF (현 fix). 빠름. recall 10
2. 2차 (옵션): 사용자가 "더 발견해줘" 클릭 시 reasoning ON 으로 re-run. recall 14-19

복잡도 ↑. 별도 endpoint 또는 query param 으로 reasoning toggle 노출 필요. 단점: GPU time 2x (ON 호출은 느림).

### 옵션 D (실험): 강한 system prompt 로 reasoning 자제

`enable_thinking=False` 빼고 system prompt 끝에:

> "Output ONLY the JSON object directly. Do not include any reasoning or explanation."

→ reasoning 트레이스를 짧게 emit (모델이 self-suppress). 효과 추정 50-50. 가장 가벼운 시도.

## 6. 진단 도구 (현재 코드베이스에 남아 있는 것)

### 6-1. 502 detail dict (`{error, raw_len, raw}`)

`app/main.py::policy_extract` 가 `PolicyExtractParseError` catch 시 raw payload (truncated 1500 chars) 를 detail dict 로 실어 보냄. parse 실패 라이브 발생 시 caller 가 Modal log hop 없이 raw 봄.

```
HTTP 502
{"detail": {
    "error": "no JSON object in response: ''",
    "raw_len": 0,
    "raw": ""
}}
```

`PolicyExtractParseError.raw` 는 service 단의 attribute. 운영 시 logger 에 같이 찍을 수도 (현재 안 함).

### 6-2. Modal 로그 grep (재현)

```powershell
modal app logs auto-workflow-agent --since 600s | Select-String "eval time|stop processing"
```

각 호출마다:
- `eval time = X ms / Y tokens (... tok/s)` ← 생성된 총 토큰
- `slot release: stop processing: n_tokens = Z` ← prompt + completion 합계

`Y` (생성) >> `len(content)/4` (visible) 면 reasoning trace stripping. 이번 발견의 결정 증거 제공한 단일 채널.

## 7. 추가 실험 시 재추가 instrumentation (1회용)

본 fix 가 PR 머지 후, 옵션 B/C/D 시도 시 다시 필요할 수 있는 *임시* 진단:

### 7-1. Raw response 노출 (`include_raw` flag)

PR 머지 시 회수됨 (production surface 깨끗하게 유지). 재시도 시:

```python
# app/models/skills.py PolicyExtractRequest 에 추가:
include_raw: bool = False

# PolicyExtractResponse 에 추가:
raw: str | None = None

# app/services/policy_extract.py extract_policies 가 (drafts, raw) tuple 반환하게 변경
# app/main.py policy_extract handler 에서 payload.include_raw 분기로 raw 첨부
```

### 7-2. Smoke 스크립트의 raw dump

`scripts/smoke_handbook_policy_extract.py` 에서 PR 머지 시 회수됨. 재시도 시:

```python
# request 에 "include_raw": True 추가
# response 에서 raw = body.get("raw") or "" 추출
# elapsed > 50s 인 chunk 에 대해 dump_path.write_text(raw, encoding="utf-8")
```

본 세션의 git history (revert 직전 commit) 에서 정확한 패치 회수 가능. 또는 본 문서 §3 + §5 의 변경분 참고.

## 8. 영향 범위 (다른 LLM 서비스)

`LlamaCppGemmaBackend` 의 `_chat_payload` 가 backend 단위 공통이라 chat_template_kwargs 가 모든 서비스에 적용됨:

| 서비스 | 입력 | 출력 | 영향 추정 | 검증 상태 |
|---|---|---|---|---|
| `compose_service` | 자연어 | WorkflowSchema JSON | reasoning 이득 가능성 ↑ | **미검증** |
| `domain_classifier` | 짧은 텍스트 | DomainCategory | 짧은 입출력, 영향 작음 | unit test only |
| `gap_analyze` | 정책 list | PolicyGap[] | 중간 입출력 | unit test only |
| `answers_to_skill` | parameter answers | SkillDraft | 중간 | unit test only |
| `policy_extract` | document chunk | SkillDraft[] | 본 실험에서 검증됨 | **6차 smoke OK** |

다른 서비스에서 reasoning 이 본질적으로 필요해지면 `_chat_payload` 를 service-level toggle 로 변경 (예: `complete(*, enable_thinking: bool = False)`). 현재는 모든 서비스가 JSON 출력이라 reasoning 의 이득보다 latency 손해가 큼 — 통합 fix.

## 9. 참고 메모리 / ADR

- auto-memory `reference_gemma4_reasoning_trace.md` — 일반 패턴 (진단 방법 + Fix + llama.cpp 옵션 풀)
- auto-memory `project_session_20260505_reasoning_root_cause.md` — 본 세션 narrative
- auto-memory `reference_policy_extract_smoke_findings.md` — 4차 smoke 까지의 사이클별 데이터
- ADR-022 §8.1 (조건+동작 단위), §8.2 (needs_clarification)
- PLAN_12 §6 (budget 가정), §9 W3 (docs path)
