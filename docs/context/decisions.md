# Architecture Decision Records (ADR)

> "왜 이 선택을 했는가"의 단일 출처. 결정이 바뀌면 새 항목을 추가하고 이전 항목은 *Superseded* 표시.

형식: `ADR-###` / 상태 / 날짜 / Context → Decision → Consequences.

---

## ADR-001 — 하이브리드 SaaS (Serverless + Agent)

**상태**: Accepted · **날짜**: 2026-04-14

**Context**
n8n 유사 워크플로우 자동화 SaaS를 구축. 유저층이 갈린다:
- Light/Middle: 월 몇 백~몇 천 건 실행. 비용 민감.
- Heavy: 고객 VPC 내부 데이터(PII, 매출 DB 등)를 외부로 반출할 수 없음. 규제 대상.

**Decision**
단일 실행 경로 대신 `workflow.settings.execution_mode`로 두 경로 분기:
1. `serverless` — Celery + Redis + Cloud Run. 멀티테넌트.
2. `agent` — 고객 VPC에 경량 Agent 데몬 설치, 중앙 서버와 WebSocket 상시 연결. 서버는 `execute` 명령만 push, 데이터는 VPC에 잔류.

두 경로가 동일한 `BaseNode` 플러그인 인터페이스를 공유해 노드 구현은 하나로 유지.

**Consequences**
- (+) Heavy 유저의 데이터 반출 우려 해소, 규제 대응 가능
- (+) Light 유저는 서버리스 단가(월 $0.04~$0.81 수준)로 커버
- (−) Agent 빌드/배포/버전 관리 파이프라인 추가 필요
- (−) 두 경로의 통합 테스트 커버리지 부담

**Update (2026-04-15)** — API_Server PLAN_02 에서 **플랜별 워크플로우 쿼터**
를 아래와 같이 확정:

| 플랜 | 활성 워크플로우 상한 | 경고 시점 (`approaching_limit`) |
|------|---------------------|-------------------------------|
| light | **100** | 90 이상 (90%) |
| middle | **200** | 180 이상 (90%) |
| heavy | **500** | 450 이상 (90%) |

- 쿼터는 `is_active=true` 행만 카운트. soft delete 된 워크플로우는 불산입
  → 유저가 생성/삭제를 반복해도 누적되지 않음 (DB bloat 는 별도 retention
  정책에서 다룸)
- 상한 도달 시 `POST /workflows` → **403 Forbidden**:
  `"workflow limit reached: N workflows for <tier> tier (plan upgrade available)"`
- `approaching_limit=true` 는 `GET /workflows` 응답에 포함되어 프론트가
  경고 배너를 UI 로 띄우는 용도 (추가 API 호출 불요)
- 값은 `API_Server/app/config.py` 의 `Settings` 에서 환경변수로 override
  가능 (`WORKFLOW_LIMIT_LIGHT=150` 등) → 운영이 코드 재배포 없이 비즈니스
  의사결정 반영 가능
- **결정 근거**: 무한 생성 허용 시 운영 DB 부담 + 실행 스케줄러/트리거
  매니저 구동 비용이 상한 없이 증가. 플랜별 차등은 가격 차등화 구조를
  기술 계층에서 강제하는 장치
- Phase 2 에서 조직(Organization) 단위 쿼터를 추가할 때 본 값을 **유저당
  기본값** 으로 유지하고 조직 쿼터를 상위 레이어에 추가 예정

---

## ADR-002 — 백엔드: Python/FastAPI (not Node.js)

**상태**: Accepted · **날짜**: 2026-04-14

**Context**
n8n은 Node.js 기반. 동일 스택을 그대로 가져올지, 다른 스택을 선택할지 결정 필요.

**Decision**
FastAPI(async) + SQLAlchemy 2.0 async + asyncpg + Celery.

**Consequences**
- (+) 팀 역량과 정합 (Python 기반)
- (+) 샌드박스(RestrictedPython), 데이터 처리, ML 연동 시 Python 생태계 활용
- (+) FastAPI async 가 WebSocket(Agent 연결)과 자연스럽게 맞물림
- (−) n8n 커뮤니티 노드를 직접 가져올 수 없음 (재구현 필요)

---

## ADR-003 — 태스크 큐: Celery (not BullMQ/Dramatiq)

**상태**: Accepted · **날짜**: 2026-04-14

**Context**
Python 기반으로 확정(ADR-002) 후, 분산 태스크 큐 선택.

**Decision**
Celery + Redis. Cloud Run 워커로 수평 확장.

**Consequences**
- (+) 성숙도, 재시도/주기/라우팅 등 기능 풍부
- (+) Redis를 결과 백엔드 + 큐로 겸용 가능
- (−) 설정 복잡도. Celery eager mode로 테스트 단순화 필요

---

## ADR-004 — 자격증명 암호화: Fernet(AES-256) + RSA 재암호화

**상태**: Accepted · **날짜**: 2026-04-14

**Context**
Credentials는 DB 저장 + Agent 전송의 두 수명주기를 가진다. 저장과 전송의 위협 모델이 다르다.

**Decision**
- **저장**: `cryptography.fernet.Fernet` (AES-256-CBC + HMAC). 마스터키는 환경변수 `CREDENTIAL_MASTER_KEY`.
- **Agent 전송**: Agent 등록 시 받은 RSA 공개키로 *재암호화*. Agent만 복호화 가능.
- **실행 시점**: Worker/Agent 메모리에서만 복호화, 노드 파라미터로 주입 후 즉시 폐기. 로그/DB/응답에 평문 절대 금지.

**Consequences**
- (+) 저장/전송 두 레이어에서 독립된 키 위협 격리
- (+) 중앙 서버가 탈취되어도 Agent로 전달 중인 자격증명은 공개키로만 보호 → 영향 축소
- (−) Agent별 키페어 생성/회전 정책 필요 (아직 미정 — 후속 ADR 대상)

---

## ADR-005 — 사용자 코드 실행: RestrictedPython + Docker 2단 격리

**상태**: Accepted · **날짜**: 2026-04-14

**Context**
`CodeExecutionNode`는 유저가 작성한 Python 코드를 실행. `eval/exec` 직접 사용은 명백한 RCE.

**Decision**
1차 AST 방어: `RestrictedPython.compile_restricted` + 내장 함수 화이트리스트
2차 프로세스 방어: 격리된 Docker 컨테이너(네트워크/FS 제한) 내부 실행
타임아웃: 기본 30초 하드 리밋

**Consequences**
- (+) 단일 방어선 우회 시에도 두번째 방어선 잔존
- (−) Docker 기동 오버헤드 (~수백ms). 빈번 호출 노드에는 경로 최적화 필요

---

## ADR-006 — Repository 패턴 + ABC

**상태**: Accepted · **날짜**: 2026-04-14

**Context**
`API_Server`와 `Execution_Engine`이 DB에 직접 의존하면 테스트 시 실제 DB 필요, 결합도 상승.

**Decision**
`Database/` 브랜치가 ABC (`WorkflowRepository`, `ExecutionRepository`, `CredentialStore`) + Postgres 구현체 제공. 상위 레이어는 ABC에만 의존. 테스트는 `InMemoryXxxRepository`로 대체.

**Consequences**
- (+) 단위 테스트에서 DB 기동 불필요
- (+) 나중에 저장소 교체(예: CockroachDB) 시 구현체만 갈아끼움
- (−) 인터페이스 설계/관리 오버헤드

---

## ADR-007 — LLM 노드 1급 추상화 + 출력 스키마 강제 + Human-in-the-loop 내장

**상태**: Accepted · **날짜**: 2026-04-14 · *Refined by ADR-008 (structured output 어댑터 범위 축소), ADR-011 (실행 로그 분리 저장)*

**Context**

현업에서 n8n 등 기존 워크플로우 자동화 도구의 AI 활용에 대해 세 가지 한계가 반복 지적된다:

1. **출력 불안정성** — LLM 노드가 같은 입력에도 매번 다른 형태(JSON/평문/키명 변이)를 반환해 후속 노드 파싱이 깨진다. 자동화의 본질인 "예측 가능한 반복"과 LLM의 비결정성이 근본 충돌한다.
2. **복합 판단 정확도 급락** — "계약서에서 위험 조항을 법무팀 기준으로 등급화" 같은 도메인 복합 판단을 단일 노드에 맡기면 할루시네이션 빈도가 현업 허용치를 초과한다. 결과적으로 "자동화했는데 검수 때문에 일이 늘었다".
3. **비용/지연** — LLM 호출 노드를 3~4개 체이닝하면 실행당 수십 초 + 토큰 비용이 누적되어, 일 수천 건 규모 업무에 경제성이 무너진다.

현재 우리 설계에는 LLM 전용 추상화가 없다. `HttpRequestNode`로 API를 호출하는 수준이면 지금 비판받는 n8n 구조와 동일하므로, 동일한 한계에 그대로 노출된다.

**Decision**

Execution Layer에 다음을 **엔진 기본 내장**으로 도입한다.

1. **`BaseNode.output_schema` (공통)**
   모든 노드가 선택적으로 **JSON Schema 문자열**을 선언하고, 런타임이 `execute()` 결과를 검증한다. LLM 노드에는 **필수**로 강제한다. 검증 실패 시 `NodeRetryPolicy`(기본: 최대 2회, 지수 백오프)에 따라 재시도하며, 모든 재시도 실패는 구조적 실패로 기록한다.
   JSON Schema 문자열로 고정한 이유는 워크플로우가 JSON으로 직렬화되어 `workflows` 테이블에 저장·복원되는 전체 수명주기에서 스키마가 **데이터와 함께 이동**해야 하기 때문이다. Pydantic 모델 객체는 Python 런타임에 묶여 직렬화가 불리하고, Frontend 스키마 편집 UI와도 어긋난다. 시스템 안정성(직렬화 라운드트립 손실 없음)을 타입 편의보다 우선한다.

2. **`LlmNode` 1급 서브클래스**
   `HttpRequestNode` 파생이 아닌 별도 추상화. 속성: `prompt_template`, `output_schema`(필수), `model`, `temperature`, `max_retries`.
   모델이 structured output(JSON mode, tool use)을 지원하면 그 경로를 우선 사용, 미지원 모델은 "JSON 강제 프롬프트 + 검증 루프" 폴백. 모델별 규격 차이(OpenAI tool use / Anthropic tool use / Gemini responseSchema)는 어댑터 계층에서 흡수한다.

3. **`ApprovalNode` — 웹 승인 + 알림 2-track 기본**
   특정 노드에서 실행을 일시정지하고 사람의 승인을 기다린다. **MVP부터 두 경로를 동시 제공**한다:
   - **웹 경로**: 프론트엔드 "승인 인박스(Approval Inbox)"에 항목 표시, `POST /api/v1/executions/{id}/approve | reject` 엔드포인트로 재개.
   - **알림 경로**: 승인 대기 시 사용자에게 이메일/Slack(추후 모바일 푸시) 발송, 메시지 내 액션 링크로 동일 엔드포인트 호출.

   두 경로 병행이 MVP 범위인 이유는 실서비스 UX 전제가 "사용자가 워크플로우 UI를 항시 띄우고 있지 않다"는 것이기 때문이다. 알림 없이 웹 인박스만 제공하면 승인 지연으로 전체 실행이 무기한 대기하게 되어 ApprovalNode 자체가 사용되지 않는다. 알림 채널은 `NotificationChannel` 인터페이스로 추상화하고 초기 구현은 이메일 + Slack 2종으로 한정한다.

   DAG 런타임은 상태 머신(`running` → `paused` → `resumed`/`rejected`)을 저장하고 재개 명령을 멱등하게 처리한다. 승인 대기 시간은 분~일 단위로 길 수 있으므로 기존 30초 하드 타임아웃(ADR-005)과는 **별도 수명주기**로 관리한다.

4. **노드 역할 축소 가이드 (정책)**
   LLM 노드의 권장 역할을 "추출 / 분류 / 요약 / 변환" 단일 작업으로 한정. 복합 판단은 여러 `LlmNode` + `ConditionNode` 조합으로 분해하도록 UI 가이드 및 템플릿에 반영. 기술적 강제는 없고 규범적 가이드라인.

관측성 보강(부수):
- `executions` 테이블에 `token_usage`, `cost_usd`, `duration_ms`, `paused_at_node` 컬럼 추가.
- `ExecutionRepository.save_result`에서 LLM 호출 메타데이터 누적.

**Consequences**

- (+) 현업 3대 불만(불안정성 / 정확도 / 비용 가시성)에 **구조적 응답**. "LLM은 믿을 수 없다"는 전제를 엔진 수준에서 보정.
- (+) `output_schema` 검증은 LLM뿐 아니라 HTTP/DB 응답 검증에도 재사용 가능 → 공통 신뢰성 향상.
- (+) 워크플로우 JSON 직렬화에 스키마가 포함되어 DB 라운드트립/버전 이행/외부 Export 모두 손실 없음.
- (+) Approval 2-track이 "95% 자동 + 5% 검수" 배포 모델을 현실적으로 지원해 자동화 도입 초기 저항을 낮춘다.
- (−) **런타임 복잡도 급증**. DAG 실행기가 상태 머신을 관리해야 하며, Celery/Agent 양쪽에서 멱등 재개를 보장해야 한다.
- (−) **DB 스키마 변경** 필요(마이그레이션 1건). ADR-006의 Repository 계약이 확장된다.
- (−) `LlmNode` 구현 난이도 높음: 모델별 structured output 규격 어댑터 계층 필요.
- (−) **알림 인프라 의존성** 추가. SMTP/Slack 발송 실패 시 승인 지연이 곧 서비스 장애로 인식될 수 있어 재시도/대체 경로가 필요하다.
- (−) JSON Schema 문자열은 개발자가 직접 타입으로 다루기 불편 → 내부적으로 `jsonschema` 라이브러리 검증 + 런타임 Pydantic 변환 헬퍼 제공으로 완화.

**Update (2026-04-15) — 노드 `running` 상태의 DB 영속화**

원본 Decision 은 `executions.paused_at_node` 를 통해 "승인 대기 중 어느 노드에
멈췄는가" 만 드러냈다. 이후 Frontend UX 검토에서 "일반 실행 중에도 사용자가
어느 단계까지 왔는지 보고 싶다 (로딩 아이콘이 아니라 진행 애니메이션)" 요구가
올라와 이를 충족하도록 관측 계층을 보강한다:

- 노드가 **시작되는 순간** `execution_node_logs` 에 `status='running'` 행을
  INSERT 한다. 완료 시 같은 행을 `'success'|'failed'|'skipped'` 로 UPDATE.
- 이 2-phase write 경로의 구체 스키마와 파티셔닝은 **ADR-011** 에서 정의.
- Frontend 는 이 테이블을 폴링/스트리밍으로 읽어 노드별 진행 상태를 렌더링.
- `ApprovalNode` 의 `paused` 전이도 같은 테이블을 경유한다 — Approval 은
  "running 노드 중 하나가 사람 입력을 기다리는 특수 케이스" 로 일관된다.

**Related**
- Refines: ADR-006 (Repository 계약 확장)
- Refined by: ADR-011 (실행 로그 분리 저장 / 파티셔닝 / 2-phase write)
- Interacts with: ADR-005 (코드 노드 30초 타임아웃은 Approval/LLM 수명주기와 분리)
- Affects branches: `Execution_Engine` (런타임/LlmNode/ApprovalNode), `Database` (스키마), `API_Server` (승인 엔드포인트, 재개 디스패치, 알림 디스패치), `Frontend` (스키마 편집 UI, 승인 인박스, 실행 진행 애니메이션)

---

## ADR-008 — 로컬 LLM 서빙: Gemma 4 + vLLM, 플랜별 라우팅, 별도 Inference_Service 브랜치

**상태**: Accepted · **날짜**: 2026-04-14 · *Refined by ADR-009 (Agent 모드 CPU-only 경로)*

![Gemma 4 + vLLM 배포 전략](./images/gemma4_vllm_deployment_strategy.svg)

**Context**

ADR-007은 LLM 노드의 출력 안정성과 Human-in-the-loop 문제는 구조적으로 해결했지만, **비용·지연**이라는 세 번째 한계는 여전히 외부 API에 의존한다. 워크플로우당 AI 노드 3~4회 호출 × 일 수천 건을 가정하면:

- API 방식: Heavy 유저 월 $50~200 가변 비용. 호출 수에 비례.
- 추가 부담: 네트워크 RTT, API 레이트리밋, 고객 데이터가 외부로 나가는 것에 대한 B2B 저항.

같은 시점에 Google이 **Gemma 4**를 Apache 2.0으로 공개했다. 모델 라인업이 우리 3-tier 유저 세그먼트와 정확히 매칭된다:

- **26B MoE (활성 4B)**: 4B급 지연 + 26B급 품질. 일반 AI 노드(분류/요약/추출)의 주력.
- **31B Dense**: Heavy 추론.
- **E4B**: Agent/엣지 GPU용.

Gemma 4는 **네이티브 function-calling + structured output**을 지원한다. vLLM이 `--tool-call-parser gemma4` 옵션으로 즉시 활용 가능. vLLM 벤치마크에서 Ollama 대비 TTFT 3배·처리량 3배, 26B MoE가 131 tok/s로 E4B(124 tok/s)보다 빠름(MoE 효과).

**Decision**

1. **로컬 LLM 서빙 도입** — vLLM + Gemma 4를 워크플로우 엔진의 기본 LLM 백엔드로 채택.
   - 기본 모델: **26B MoE** (일반 노드)
   - 중량 모델: **31B Dense** (복잡 추론, 초기엔 수요 시 별도 인스턴스 추가)
   - 양자화: **fp8** (GCP RTX 6000 Pro 24~48GB 클래스 전제)
   - 서빙 옵션: `--enable-auto-tool-choice --tool-call-parser gemma4`

2. **라우팅 정책: 플랜별 고정 (Option C)**
   ADR-001의 3-tier 유저 세그먼트와 일관되게 **유저 플랜으로 백엔드를 결정**한다. 런타임 복잡도 판정이나 임계치 자동 전환은 도입하지 않는다.

   | 플랜 | LLM 백엔드 | 비고 |
   |------|-----------|------|
   | Light | 외부 API (Claude/Gemini) | 호출량 적음. 고정비 기피. |
   | Middle | 외부 API (공유 풀) | 사용량 증가 시 로컬 이관은 Phase 2 재검토 |
   | Heavy (Serverless) | **중앙 vLLM 서빙** (Gemma 4 26B MoE / 31B) | 손익분기 초과 구간 |
   | Heavy (Agent) | Agent 내 vLLM (E4B) — **Phase 2** | MVP는 중앙 vLLM 경유 또는 API |

   **실패 시 폴백 규칙** (모든 플랜 공통):
   - 로컬 모델이 `output_schema` 검증 N회 연속 실패 → 동일 플랜 내에서 더 큰 로컬 모델(26B → 31B)로 재시도
   - 로컬 인프라 장애(전체) → 외부 API로 플랜과 무관하게 폴백 + 장애 알람
   - 폴백은 운영 안전망이지 라우팅 정책이 아니다.

3. **`Inference_Service` 브랜치 신설**
   vLLM 서빙을 `Execution_Engine` 내부 서비스가 아닌 **별도 레이어**로 분리한다.
   - 배포/스케일 수명주기가 다름 (GPU 인스턴스, 모델 로드 수분 소요 vs 워커는 초 단위 기동)
   - GPU pre-allocate ~90GB 이슈로 독립 인스턴스가 자연스러움
   - 책임 분리: `Execution_Engine`은 노드 실행, `Inference_Service`는 모델 서빙. 교체·업그레이드 독립.
   - 인터페이스: `Execution_Engine`의 `LlmNode`가 HTTP로 `Inference_Service`를 호출. OpenAI 호환 엔드포인트(vLLM 기본)로 단순화.

4. **Agent 내 vLLM (E4B)는 Phase 2로 분리**
   Heavy 유저 VPC Agent에 E4B 서빙을 내장하면 "데이터 + 추론 둘 다 VPC 잔류"가 가능해 ADR-001 하이브리드 SaaS의 장점이 극대화되지만, MVP 범위에는 포함하지 않는다:
   - Agent 이미지에 GPU 런타임 + vLLM + 모델 가중치(수십 GB) 번들 시 배포 복잡도 급증
   - Heavy 유저라도 GPU 보유를 전제할 수 없음
   - Phase 1에서는 Agent가 중앙 `Inference_Service`를 호출(VPC → 중앙) 또는 외부 API 폴백. 데이터 민감도가 높은 고객은 Phase 2까지 API 폴백 금지 옵션으로 대응.

5. **ADR-007 어댑터 범위 축소**
   Gemma 4 네이티브 structured output이 제공되는 경로에서는 "JSON 강제 프롬프트 + 검증 루프" 폴백이 불필요. ADR-007 Decision 2의 어댑터 계층 범위를 "structured output 미지원 경로만"으로 축소. ADR-007 본문은 수정하지 않고 상태 행에 *Refined by ADR-008* 주석만 추가(ADR 불변성 원칙).

**Consequences**

- (+) **Heavy 유저 단가 급감**: 월 5만 호출 이상 구간에서 호출당 단가가 API 대비 수십 배 낮아짐
- (+) **데이터 경계 강화**: 중앙 vLLM 서빙으로 B2B 데이터가 외부 API 제공자를 경유하지 않음 (VPC 잔류는 Phase 2)
- (+) **네이티브 function-calling**: ADR-007의 `output_schema` 강제가 모델 레벨에서 직접 지원됨 → 어댑터 부담 감소
- (+) **책임 분리**: `Inference_Service` 독립으로 GPU 스케일·모델 업그레이드를 Execution 레이어와 무관하게 수행
- (−) **고정비 전환**: 얼리 유저 단계에서 GPU 인스턴스 월 $300~500은 매몰비용. Light/Middle API 매출로 상쇄 안 되는 초기 구간 존재
- (−) **운영 복잡도**: GPU 인스턴스 헬스체크, 모델 로드 타임, OOM, pre-allocate ~90GB 이슈 관리 필요
- (−) **폴백 경로 테스트 부담**: 로컬 장애 → API 폴백 경로는 드물게만 발동되므로 정기 카나리아 필요
- (−) **후속 작업 필요**: post-checkout 훅 case 분기 + `_claude_templates/CLAUDE_Inference_Service.md` 템플릿 작성 (이 ADR 범위 밖, main 브랜치에서 처리)

**Related**
- Extends: ADR-007 (LLM 노드 1급 추상화) — Gemma 4 네이티브 structured output으로 어댑터 범위 축소
- Interacts with: ADR-001 (하이브리드 SaaS) — Agent + vLLM 시너지는 Phase 2
- Affects branches: `Inference_Service` (신규), `Execution_Engine` (`LlmNode`가 HTTP 클라이언트 의존), `API_Server` (플랜 기반 라우팅 결정), `Database` (유저 플랜 필드)

---

## ADR-009 — Agent 모드 CPU-only 고객 대응: KTransformers를 Inference_Service의 두 번째 백엔드로

**상태**: Proposed · **날짜**: 2026-04-14

**Context**

ADR-008은 Heavy 유저의 비용·지연·데이터 경계 문제를 중앙 `Inference_Service`(vLLM + Gemma 4)로 풀었지만, **Agent 모드 = 고객 VPC 내 실행** 시나리오에서 한 가지 전제가 깨진다: *고객이 GPU를 보유하고 있을 것*. 실제 B2B 영업 과정에서 확인된 패턴은 다음과 같다.

- Heavy 유저라도 사내에 **CPU 서버만 있는** 경우가 적지 않다(특히 금융/공공/제조 온프레).
- 데이터 민감도 때문에 외부 API 폴백을 **조직 정책으로 금지**한 고객이 존재한다.
- 즉 ADR-008의 Agent 폴백 경로(중앙 vLLM 호출 또는 외부 API)가 **둘 다 막히는** 고객 세그먼트가 있다.

같은 시기에 **KTransformers**(MADSys @ Tsinghua)가 주목받기 시작했다. vLLM과 자주 비교되지만 **푸는 문제가 다르다**:

| 축 | vLLM | KTransformers |
|---|---|---|
| 최적화 목표 | GPU 충분할 때 **동시 요청 처리량** (PagedAttention + continuous batching) | GPU 부족할 때 **CPU-GPU 이기종** 활용으로 거대 모델 구동 |
| 강점 시나리오 | 멀티유저 SaaS 서빙 | 단일/소수 유저, GPU 없음 또는 ≤24GB |
| 보고된 성능 | A100 기준 단순 Transformer 대비 50~200× 동시성 | prefill 4.62~19.74×, decode 1.25~4.09× (대비: 기존 CPU offloading); 24GB VRAM 단일 GPU로 671B 파라미터 구동, prefill 최대 286 tok/s |
| 하드웨어 전제 | NVIDIA GPU (멀티 GPU 텐서/데이터 병렬 우수) | AMD EPYC + AMX 지원 CPU + 최소 16GB CUDA GPU에서 최적 |
| API 표면 | OpenAI 호환 (즉시 사용 가능) | 연구 프로젝트 성격, OpenAI 호환 미제공 (SGLang 통합 PR 진행 중) |
| 성숙도 | 프로덕션 검증 다수 | `kt-kernel` / `kt-sft`로 최근 리팩토링, 프로덕션 사례 적음 |

**핵심 통찰**: KTransformers는 vLLM의 **대체재가 아니라 보완재**다. SaaS 모드(중앙 서빙)에서는 vLLM이 정답이고, KTransformers는 **"GPU 없는 Agent 고객"이라는 빈 칸**을 채운다.

**Decision**

1. **`Inference_Service`에 두 번째 백엔드로 KTransformers를 추가**한다.
   - vLLM은 그대로 1순위(중앙 서빙 + GPU 있는 Agent).
   - KTransformers는 Agent 모드에서 **GPU가 없거나 부족한 고객 전용** 경로.
   - 두 백엔드 모두 `LlmNode` 입장에서는 동일한 OpenAI 호환 인터페이스로 보여야 한다. KTransformers는 OpenAI 호환 엔드포인트가 아직 없으므로 **`Inference_Service` 내부 어댑터**로 감싼다.

2. **라우팅은 LLMRouter 한 곳에 집중**한다 (런타임 분기, 플랜 분기 위에 얹는 한 단계).

   ```python
   class LLMRouter:
       async def route(self, execution_mode: str, gpu_info: dict) -> LLMProvider:
           if execution_mode == "serverless":
               # SaaS 모드 → 무조건 vLLM (동시성이 핵심)
               return self._vllm_central

           # Agent 모드 → 고객 하드웨어에 따라 분기
           if gpu_info["vram_gb"] >= 24:
               return self._agent_vllm           # GPU 충분 → vLLM
           elif gpu_info["cpu_supports_amx"]:
               return self._agent_ktransformers  # GPU 부족 + AMX CPU → KTransformers
           else:
               return self._api_fallback         # 둘 다 안 되면 → 외부 API (조직 정책 허용 시)
   ```

   - `gpu_info`는 Agent 부팅 시 1회 수집해 `API_Server`에 등록. 런타임마다 재탐지하지 않는다.
   - 외부 API 폴백이 조직 정책으로 금지된 고객은 라우팅 결과가 비면 **노드 실행 실패**로 처리(폴백 무한 루프 금지).

3. **MVP에서는 도입하지 않는다 — Phase 2 로드맵으로 분리**.
   ADR-008과 동일한 보수적 자세를 유지한다. 이유:
   - KTransformers는 OpenAI 호환 API가 없어 어댑터 작성 비용이 든다.
   - 하드웨어 호환성 범위가 좁다(AMD EPYC + AMX). 고객 환경 사전조사 프로세스 필요.
   - 프로덕션 안정성 검증이 vLLM보다 부족 → MVP 신뢰성 리스크.
   - MVP 단계의 Agent 모드는 ADR-008대로 *중앙 `Inference_Service` 경유 또는 외부 API*로 충분하다.

4. **Phase 2 진입 트리거**: 다음 중 하나가 충족되면 KTransformers 백엔드 구현을 착수한다.
   - "GPU 없음 + 외부 API 금지" 조합의 Heavy 고객 후보가 **2건 이상** 영업 파이프라인에 등장
   - KTransformers 측의 SGLang 통합 PR이 머지되어 OpenAI 호환 표면이 안정화

**Consequences**

- (+) **빈 칸 메우기**: ADR-008의 사각지대(GPU 없는 Agent 고객 + 외부 API 금지)에 대한 명시적 대응 경로 확보. 영업 시 "그 고객은 못 받습니다"가 아니라 "Phase 2에서 지원됩니다"라고 답할 수 있음.
- (+) **라우팅 일관성**: LLMRouter가 백엔드 다양성을 흡수하므로 `LlmNode` 코드는 손대지 않아도 됨. ADR-007의 1급 추상화 원칙과 충돌 없음.
- (+) **MVP 범위 보호**: Phase 2 분리로 MVP 일정·신뢰성에 영향 없음. ADR-008의 보수적 폴백 전략과 동일한 패턴.
- (−) **사전조사 부담**: Agent 고객마다 `gpu_info` + AMX 지원 여부 + 조직의 외부 API 정책 3종 세트를 영업/온보딩 단계에서 수집해야 함. CRM 또는 온보딩 체크리스트 필드 추가 필요.
- (−) **두 번째 어댑터 유지비**: KTransformers의 OpenAI 호환 표면이 안정화되기 전에는 `Inference_Service` 내부 어댑터를 직접 유지해야 함. SGLang 통합이 머지되면 어댑터를 제거하거나 얇게 만들 수 있음.
- (−) **운영 매트릭스 확장**: vLLM + KTransformers 두 백엔드의 헬스체크/모델 로드/버전 호환성을 각각 관리해야 함. 단, Agent 측에 한정되므로 중앙 서빙 운영 부담은 늘지 않음.

**Update (2026-04-15) — `external_api_policy` 구현 계약**

Decision §2 의 "외부 API 폴백이 조직 정책으로 금지된 고객은 라우팅 결과가
비면 노드 실행 실패로 처리" 조항이 `API_Server` 의 라우팅 코드에서 읽어야
할 구체 데이터 형상을 정의한다.

- 저장 위치: `users.external_api_policy` (JSONB, PLAN_01 §3.1)
- **유일한 계약 키**: `allow_outbound: boolean`
  - `true` — 외부 API 폴백 허용
  - `false` (기본값, 누락 시) — 외부 API 폴백 금지 → 라우팅 결과가 비면 노드 실행 실패
- **포워드 호환 규칙**: 미정의 키는 저장 허용, 읽기 시 무시 + `WARN` 로그.
  현재 이 ADR 가 확정한 키는 `allow_outbound` **단 하나**이며, 도메인 allow/
  deny 리스트 등 확장 키는 차단 로직이 실제로 필요해지는 시점에 별도 PLAN 에서
  합의 후 추가한다.
- 변경 이력: 이 키를 추가/제거할 때는 본 ADR 의 Update 섹션에 표로 기록.

이 계약은 `Inference_Service` 의 `LLMRouter` 폴백 분기(§2 의 `_api_fallback`)
가 활성화될지 말지를 결정하는 단일 소스가 된다.

**Related**
- Refines: ADR-008 (Gemma 4 + vLLM 로컬 서빙) — Agent 모드 백엔드 경로 보강
- Interacts with: ADR-001 (하이브리드 SaaS) — "데이터 + 추론 둘 다 VPC 잔류" 시나리오의 GPU 없는 변종을 커버
- Affects branches: `Inference_Service` (KTransformers 어댑터, Phase 2), `API_Server` (Agent `gpu_info`/정책 필드, `external_api_policy` 읽기 경로), `Database` (고객 환경 메타데이터, `users.external_api_policy`, `agents.gpu_info`)
- 미해결 질문: KTransformers의 Gemma 4 26B MoE 지원 검증, AMX 미지원 CPU에서의 성능 하한선, 라이선스 재검토(Apache 2.0 호환)

---

## ADR-010 — pgvector 확장 MVP 선탑재

**상태**: Accepted · **날짜**: 2026-04-15

**Context**

Database 브랜치 MVP 부트스트랩 중 "지금은 벡터 컬럼이 필요한 유스케이스가
없지만, 장래 RAG(자연어 → 노드 생성, 템플릿/과거 워크플로우 검색)가 들어올
가능성" 이 논의됐다. 선택지는 둘:

1. 순정 `postgres:16` 으로 시작, RAG 가 필요해지는 시점에
   `CREATE EXTENSION vector` 와 pgvector 도커 이미지로 교체
2. `pgvector/pgvector:pg16` 을 처음부터 사용하고 확장을 기본 설치

**Decision**

**옵션 2 채택**. `Database/docker-compose.yml` 이미지는
`pgvector/pgvector:pg16`, `schemas/001_core.sql` 에 `CREATE EXTENSION IF NOT
EXISTS "vector"` 를 포함. MVP 스키마에는 아직 벡터 컬럼이 없다 — 확장만
설치하고 사용 시점을 기다린다.

**Rationale**

- **교체 비용의 비대칭성**: 장래 교체 시 도커 이미지 변경 + 재시작 + 확장
  설치 마이그레이션이 필요하고, 운영 중 DB 에서는 이게 non-trivial. 지금
  설치해 두면 RAG 도입 시 "마이그레이션 한 줄 + 컬럼 추가" 로 끝난다.
- **선탑재 비용**: 이미지 크기 차이는 수십 MB. 설치된 미사용 확장의 런타임
  오버헤드는 0. 즉 현재 비용이 **거의 0** 이고 미래 비용 회피는 크다.
- **YAGNI 원칙의 예외 기준**: 비용이 0 인 옵션을 YAGNI 로 거부하면 오히려
  기술부채가 된다. YAGNI 는 "복잡도를 키우는 기능" 에 적용되는 것이지
  "0 비용의 기반 인프라" 에는 적용되지 않는다.

**Consequences**

- (+) 후속 PLAN(예: PLAN_06 RAG)에서 확장 설치 단계 불필요 → 단일 마이그레이션
  으로 `ALTER TABLE ... ADD COLUMN embedding vector(N)` 만 하면 됨.
- (+) 데이터 마이그레이션 리스크 축소 — 운영 DB 의 확장 설치는 재시작/락
  이슈가 있어 별도 운영 창구가 필요한데, 이걸 MVP 단계에 흡수.
- (−) 도커 이미지가 `pgvector` 변종으로 고정됨. 순정 이미지로 되돌릴 때
  도커 재설정 필요 (역방향 비용도 크지 않음).
- (−) 확장이 "설치만 돼 있고 미사용" 인 상태가 장기화되면 팀원이 "쓰는 줄
  알았는데 왜 없지?" 같은 혼동을 할 수 있음 — 이 ADR 로 상태를 명시해
  완화.

**Related**
- Enables: 후속 RAG PLAN (템플릿 갤러리 / 사용자 워크플로우 임베딩 검색)
- Affects branches: `Database` (DDL/도커 이미지)

---

## ADR-011 — 실행 로그 분리 저장 + 월별 파티셔닝 + GCS 원문 오프로드

**상태**: Accepted · **날짜**: 2026-04-15

**Context**

ADR-006/007 이 도입한 `executions.node_results jsonb` 는 "노드별 결과 요약"
용도로 시작했지만 실제로 쌓일 데이터는 세 가지 압력을 받는다:

1. **ADR-007 관측성 요구** — 노드별 `token_usage`/`cost_usd`/`duration_ms` +
   Approval 상태머신. LLM 사용량을 모델별로 집계하려면 JSONB 안을 스캔해야
   하고, 이건 성능이 나오지 않는다.
2. **Retry 이력** — 같은 노드가 N 회 재시도되면 JSONB 키 충돌. "최신 결과만
   보이고 과거 시도는 유실" 되거나 깊은 중첩 구조가 된다. 둘 다 UX/디버깅에
   해롭다.
3. **UI 애니메이션 요구 (ADR-007 Update 2026-04-15)** — 사용자는 "로딩 아이콘"
   이 아니라 "노드 N 까지 진행, 노드 N+1 실행 중" 을 보고 싶다. 이는 노드가
   `running` 상태일 때도 DB 에 레코드가 존재해야 한다는 뜻이다.
4. **stdout/stderr 원문 크기** — 커스텀 스크립트 노드가 MB 급 출력을 낼 수
   있다. 이걸 JSONB 에 넣으면 row 가 비대화되고 전체 테이블 I/O 에 악영향.
5. **파티셔닝 기술부채** — "나중에 로그 테이블에 파티션 붙이자" 는 잘 알려진
   부채 함정. 운영 중 파티션 도입은 lock/재작성 리스크가 크다.

**Decision**

PLAN_03 에서 구현한 분리 저장 구조를 설계 결정으로 승격:

1. **새 테이블 `execution_node_logs` — 월별 RANGE 파티션**
   - 파티션 키: `started_at` (timestamptz)
   - PK: `(id, started_at)` — Postgres 네이티브 파티셔닝은 UNIQUE 제약에 파티션
     키 포함을 요구
   - 초기 12 개 월 파티션을 DDL `DO` 블록으로 부트스트랩
   - `scripts/roll_partitions.py` 가 월별 롤포워드 (외부 스케줄러 책임)
   - 보존 삭제 정책은 **별도 운영 PLAN** 에서 결정 — 본 ADR 범위 밖

2. **`executions.node_results` 와의 역할 분리 (옵션 A)**
   - `executions.node_results` = **최신 attempt 요약만**. `API_Server` 의
     기존 계약(`append_node_result`) 유지.
   - `execution_node_logs` = **상세 로그의 단독 소스**. retry/running/완료
     모두 여기에 N 행으로 쌓인다.
   - `Execution_Engine` 은 노드 실행 시 **두 Repository 에 모두 기록**.

3. **2-phase write (`record_start` / `record_finish`)**
   - 노드 시작 → `record_start` 가 `status='running'` 행 INSERT
   - 노드 종료 → 같은 행 UPDATE (`success|failed|skipped`)
   - 파티션 키 `started_at` 는 **불변** → UPDATE 가 파티션 간 row 이동을
     일으키지 않음
   - UPDATE WHERE 절은 반드시 `(id, started_at)` 둘 다 지정 (id 단독이면
     Postgres 가 모든 파티션 스캔 — 파티션 프루닝 실패)

4. **`attempt` 는 호출자 명시 전달**
   - 1-based 정수, DEFAULT 1 은 해피패스 편의 only
   - `Execution_Engine` 의 리트라이 루프가 자기 카운터로 관리하고 매
     `record_start` 에 명시 전달
   - DB 측 auto-increment 는 사용 안 함(레이스 방어 복잡도 회피)

5. **LLM 관측 4필드 컬럼 선승격**
   - `model text`, `tokens_prompt int`, `tokens_completion int`,
     `cost_usd numeric(10,6)` — JSONB 가 아니라 정규 컬럼
   - 부분 인덱스 `(model) WHERE model IS NOT NULL` 로 모델별 집계 쿼리 경로
   - 그 외 노드별 상세 메타데이터는 `input/output/error jsonb` 필드 유지

6. **stdout/stderr 는 GCS 오프로드 — DB 에는 URI 포인터만**
   - `stdout_uri text NULL`, `stderr_uri text NULL`
   - 형식 권장: `gs://{bucket}/executions/{execution_id}/{node_id}/{attempt}/stdout.log`
   - GCS 업로더 구현은 `Execution_Engine` 책임. DB 브랜치는 URI 형식 검증 안 함.
   - 보안 효과: 민감 페이로드가 DB 백업/덤프에 섞일 위험 축소.

**Consequences**

- (+) LLM 사용량 집계가 파셜 인덱스 + 정규 컬럼으로 O(파티션 프루닝) 성능.
- (+) 파티셔닝을 **처음부터** 적용해 장래 무파티션 → 파티션 전환 리스크 제거.
- (+) 2-phase write 로 Frontend 실시간 진행 애니메이션 가능. `ApprovalNode` 와
  일관된 상태 모델 (running 의 특수 케이스가 paused).
- (+) stdout/stderr 가 DB row 를 부풀리지 않음. 대용량 노드 출력이 DB 성능에
  영향 주지 않음. 백업/복제 비용 감소.
- (+) `Execution_Engine` 의 리트라이 루프가 attempt 를 소유 → DB 가 "진실의
  원천" 을 두 곳에 둘 필요 없음. 레이스 방어 복잡도 없음.
- (−) `Execution_Engine` 이 두 Repository 를 모두 호출해야 함 — 호출 누락 시
  "요약은 있는데 상세는 없음" 또는 그 반대가 발생. 호출 래퍼/데코레이터로
  보완 필요.
- (−) GCS 의존성이 `Execution_Engine` 에 추가됨. 업로드 실패 시 `stdout_uri`
  는 NULL 인 채로 남아야 하며, 업로드 실패가 노드 실패를 유발해선 안 됨
  (관측 실패 ≠ 실행 실패).
- (−) 파티션 롤포워드가 **외부 스케줄러 책임** 이라 배포 측에서 크론 등록을
  잊으면 새 월 INSERT 가 "no partition of relation ... found" 로 실패한다.
  완화: `roll_partitions.py --dry-run` 을 온보딩 체크리스트에 포함, 초기
  12 개월 버퍼.
- (−) 보존 삭제 정책 미정 → 무제한 누적. 별도 운영 PLAN 에서 결정.

**Related**
- Refines: ADR-006 (Repository 패턴 — 새 ABC 추가), ADR-007 (노드 running
  상태의 DB 영속화 이유 + Approval 상태머신을 동일 테이블로 통합)
- Depends on: ADR-010 (pgvector 와 무관하지만 동일한 "기반 인프라 MVP 선탑재"
  철학)
- Affects branches:
  - `Database` — 스키마 003, Repository, `roll_partitions.py`
  - `Execution_Engine` — 노드 실행 래퍼가 `ExecutionNodeLogRepository.record_start/finish` 호출 + `ExecutionRepository.append_node_result` 이중 쓰기, GCS 업로더, 리트라이 루프의 attempt 카운터 소유
  - `API_Server` — 실행 상세 조회 엔드포인트가 두 테이블을 함께 읽음
  - `Frontend` — 실행 진행 애니메이션, 노드별 로그/토큰/비용 렌더링
  - 운영 — `scripts/roll_partitions.py` 크론 등록, GCS 버킷 프로비저닝/수명주기 정책

---

## ADR-012 — Approval 알림 감사 추적: 독립 상태, 평문 recipient, 파티셔닝 예외

**상태**: Accepted · **날짜**: 2026-04-15

**Context**

ADR-007 이 `ApprovalNode` 에 "웹 인박스 + 알림(이메일/Slack)" 2-track 을
MVP 기본으로 확정한 뒤, "발송 로직" 과 "발송 이력" 이라는 두 관심사가 남았다.
이 ADR 은 **발송 이력(audit trail)** 만 다룬다. 실제 발송 로직은 `API_Server`
또는 별도 워커의 책임이다.

세 가지 설계 결정이 필요했다:

1. **발송 실패가 실행 상태머신에 영향을 주는가?** — SMTP/Slack 일시 장애가
   `executions.status` 를 건드리면 알림 인프라 장애가 곧 자동화 장애로
   확대된다. 반대로 완전히 무시하면 극단적 영구 실패 시 사용자가 모를 수
   있다.
2. **`recipient` 를 어떻게 저장하는가?** — `users.email` 을 JOIN 할지, 평문
   이메일/Slack id 를 이 테이블에 사본 저장할지. 성능(JOIN 병목) vs GDPR
   (이메일 사본 증가) 의 트레이드오프.
3. **파티셔닝할 것인가?** — ADR-011 은 `execution_node_logs` 에 월별 파티션을
   선제 도입했다. 같은 원칙을 이 테이블에도 일괄 적용하면 오버엔지니어링인가?

**Decision**

1. **발송 실패와 Approval 상태머신은 독립** — `approval_notifications.status`
   는 `queued | sent | failed | bounced` 로 **자체 상태머신** 을 가지며,
   `executions.status` 와 결합하지 않는다. 모든 채널 영구 실패 시에도 실행은
   `paused` 로 남는다.
   - 안전망: 운영 대시보드가 `list_undelivered(older_than=24h)` 를 폴링해
     "24시간 이상 미도달" 을 에스컬레이션. 이 알람 자체는 별도 운영 PLAN 범위.
   - 근거: 알림 인프라 장애 범위를 자동화 엔진으로 확산시키지 않기 위함.
     "자동화했는데 한 통 이메일 못 보내서 워크플로우가 취소되더라" 가 더
     나쁜 사용자 경험.

2. **`recipient` 는 평문 저장** (이메일 주소 또는 Slack user id) — 성능을
   이유로 정규화/JOIN 을 거부한다.
   - 근거 1 (성능): 미도달 대시보드 쿼리 + execution 상세 조회 둘 다 hot path.
     매번 `users` JOIN 은 파이낸셜/엔터프라이즈 고객 규모에서 DB 병목이 된다.
   - 근거 2 (분리): `recipient` 는 "발송 시점의 주소록" 이지 "현재 사용자
     이메일" 이 아니다. 사용자가 이메일을 변경해도 과거 이력은 "당시에 어디로
     보냈는지" 를 그대로 보존해야 감사 가치가 있다.
   - GDPR 대응: 삭제는 `DELETE FROM approval_notifications WHERE recipient = ?`
     로 수행. 운영 PLAN 에서 삭제 워커/요청 처리 경로를 정의.

3. **파티셔닝은 도입하지 않는다** — ADR-011 의 "선제 파티셔닝" 철학을 일괄
   적용하지 않는다.
   - 볼륨 분석: 고객 100 × 워크플로우 30 × Approval 사용률 15% × 일 5회 ×
     알림 3건(2채널 + 재시도) ≈ 연 2.2M 행, 행당 300 B → **연 0.7 GB**.
   - 수천만 행~억 행 도달 시점(=10년 이상 누적) 까지 단순 테이블 + 부분
     인덱스로 충분. 쿼리 패턴(`execution_id` 기반 상세 + `status IN
     ('queued','failed')` 부분 인덱스) 이 파티션 프루닝을 거의 타지 않음.
   - **파티셔닝 도입 기준** (ADR-011 에 암묵적으로 있던 것을 여기서 명시화):
     (a) 행 수가 이벤트당 O(N>5) 로 곱해져 빠르게 커지거나, (b) hot path
     쿼리가 시간 범위 필터를 포함하거나, (c) 예상 보존 정책이 정기 삭제를
     요구할 때만 선제 파티셔닝. 본 테이블은 셋 다 해당 없음.

4. **인박스는 독립 저장소가 아니라 쿼리** — "Approval 인박스" 는 UI 개념일 뿐
   DB 저장 구조가 아니다. `SELECT ... FROM executions WHERE status='paused'
   AND owner_id=?` 의 페이지네이션 결과를 Frontend 가 렌더링. Pending 은 사람
   처리 속도로 자연 캡이 걸리고, Resolved 는 날짜 범위 필터로 페이지네이션.
   - 근거: 별도 인박스 테이블은 `executions` 와의 동기화 부담만 추가하고
     이점이 없다.

**Consequences**

- (+) 알림 인프라 장애가 워크플로우 엔진으로 확산되지 않음. SRE 경계 명확.
- (+) 평문 저장으로 대시보드 쿼리가 단일 인덱스 스캔으로 끝남.
- (+) 파티셔닝 도입 기준을 명문화 → 장래 "이 테이블도 파티셔닝해야 하나?"
  토론 때 이 ADR 을 기준으로 결정 가능. ADR-011 의 과잉 일반화 방지.
- (+) `recipient` 가 "발송 시점 스냅샷" 이 되어 감사 자료로서 완결성.
- (−) `recipient` 사본이 사용자 삭제 요청 시 별도 처리 경로 필요 (운영 PLAN).
- (−) "24시간+ 미도달 알림" 감시 알람 인프라가 **이 ADR 외부 의존성** 으로
  생김 — 이게 없으면 극단적 영구 실패 케이스가 침묵 장애가 됨.
- (−) Slack user id 와 이메일 주소가 같은 `recipient` 컬럼에 섞여 저장됨.
  쿼리 시 `channel` 로 분기. 추후 구조화가 필요하면 JSONB 로 승격.

**Related**
- Refines: ADR-007 (`ApprovalNode` 2-track 알림 경로의 저장 계층 확정)
- Complements: ADR-011 (같은 "분리 저장" 계열이되, 본 ADR 은 **파티셔닝 예외**
  라는 반대 방향 결정 — ADR-011 의 선제 파티셔닝이 일괄 규칙이 아니라 볼륨 +
  쿼리 패턴 기준임을 명시)
- Affects branches:
  - `Database` — 스키마 004, Repository (PLAN_04)
  - `API_Server` — 발송 워커(또는 엔드포인트) 가 매 시도마다 `record()` 호출
  - `Frontend` — 인박스는 `executions WHERE status='paused'` 페이지네이션.
    별도 저장소 아님
  - 운영 — 미도달 대시보드 + "24시간+" 알람, GDPR 삭제 요청 처리 경로

---

## ADR-013 — Agent 자격증명 전송 하이브리드 암호화 사양 (AES-256-GCM + RSA-OAEP-SHA256)

- **상태**: Accepted (2026-04-15). **Update (2026-04-17)**: 사용 경로가 §7
  pull 방식 (`get_credential` 프레임) 에서 **push 방식 (execute 메시지의
  `credential_payloads` 동봉)** 으로 확정됨. credential_pipeline blueprint §2.5
  참고. 3-필드 envelope 포맷은 동일. pull 방식 stub 은 당분간 유지하되 주
  경로는 push. 또한 Agent **개인키 관리 운영 절차** 를 §8 로 추가 정의.
  
- **맥락**: ADR-004 가 "Agent 모드에서는 Agent 공개키(RSA) 로 자격증명을
  재암호화하여 전달" 이라고만 규정했을 뿐 알고리즘/파라미터/프레임 스키마가
  미정이었다. PLAN_05 구현 착수 전 타 브랜치(`Execution_Engine`, Agent 측
  코드) 가 의존할 계약 형상을 고정해야 한다.
- **결정**:
  1. **라이브러리** — `pyca/cryptography`. Fernet(ADR-004) 이 이미 사용 중이라
     의존성 추가 0. PyCryptodome 은 채택하지 않는다.
  2. **하이브리드 스킴 채택** — Fernet 평문이 수 KB 에 달해 RSA-2048 OAEP-SHA256
     단일 블록 한도(190 B) 를 초과하므로 직접 RSA 암호화는 불가능. 대칭키를
     RSA 로 wrap 하고 페이로드는 AES 로 암호화하는 2-layer 방식으로 고정.
  3. **대칭층** — AES-256-GCM. AEAD 라 페이로드 변조 감지 내장. CBC+HMAC 조합은
     구현 실수 표면이 넓어 채택하지 않는다. 매 호출마다 새 random key + nonce.
  4. **RSA 파라미터** — RSA-2048, 공개지수 65537, OAEP 패딩 (hash=SHA-256,
     MGF1=SHA-256, label 없음). RSA-3072/4096 는 MVP~Phase 1 범위(2026-2028)
     에서 과잉이며 성능 손실(각 6ms/15ms vs 2ms) 대비 보안 실익이 작다.
  5. **프레임 스키마** — WebSocket 응답 프레임의 `payload` 필드가 다음 JSON
     을 base64 로 인코딩한 3-필드 객체를 담는다:
     ```json
     {
       "wrapped_key": "<base64, 256 B>",     // RSA-OAEP(SHA256) 로 wrap 된 AES-256 키
       "nonce":       "<base64, 12 B>",      // GCM nonce
       "ciphertext":  "<base64, N+16 B>"     // AES-256-GCM(평문) + 16 B tag
     }
     ```
     `wrapped_key` 길이 고정(256 B) 으로 포맷 검증 가능. Agent 측 복호 코드는
     동일 스펙을 기대한다.
  6. **캐시 금지** — 재암호화 결과는 DB/서버 메모리 어디에도 캐시하지 않고
     매 요청 즉석 계산 (PLAN_05 §Q3). Agent 프로세스 메모리에만 execution
     수명 동안 평문 보관, execution 종료 시 즉시 zeroize.
  7. **호출 트리거** — Agent 가 Agent-initiated WebSocket 위에서
     `get_credential(credential_id)` 프레임을 올리고 서버가 응답 프레임으로
     상기 payload 를 반환 (pull 방식). Heavy/사설망 고객 방화벽이 인바운드
     TCP 를 차단해도 이미 열린 아웃바운드 소켓을 재사용하므로 영향 없음.
- **결과**:
  - `Database/src/repositories/credential_store.py` 에 `retrieve_for_agent(
    credential_id, agent_public_key_pem) → bytes` 신설. 순수 함수 형태로 구현해
    향후 API_Server 인프로세스 캐시 데코레이터를 덧씌울 수 있도록 확장성 확보.
  - `Execution_Engine` / Agent 측 복호 코드는 이 프레임 스키마를 준수.
  - 타 브랜치가 의존할 계약 형상: 프레임 3-필드 구조 + 알고리즘 파라미터.
- **대안과 기각 사유**:
  - **순수 RSA (하이브리드 없이)** — 190 B 한도 초과로 기술적으로 불가능
  - **RSA-4096** — 성능 7배 손실 대비 MVP 보안 실익 없음
  - **ECDH + HKDF + AES-GCM** — 더 현대적이지만 Agent 공개키가 이미 RSA 로
    고정(PLAN_02 `agents.public_key`) 되어 있어 재설계 비용이 큼. 후속
    마이그레이션으로 보류
  - **서버 측 DB 캐시** — 키 회전 무효화 로직/테이블/TTL 추가 복잡도 대비 성능
    실익 미미. 실측 후 인프로세스 캐시로 대응
- **대체 경로**: 2030 전후 RSA-2048 이 deprecated 될 때 후속 ADR 로
  (a) RSA-3072 로 키 크기 업그레이드, 또는 (b) ECDH 기반 스킴으로 마이그레이션.
  `agents.public_key` 컬럼 교체로 invasive 하지 않다.
- **§8 Agent 개인키 관리 (Update 2026-04-17, PLAN_10 확정)**:
  1. 키페어는 **고객 VPC 내부에서 생성**. 서버는 공개키만 `agents.public_key`
     컬럼으로 받고, 개인키는 어떤 형태로도 서버에 전송·저장되지 않는다.
  2. 개인키 파일은 VPC 파일 시스템에 **권한 600** 으로 저장. Agent 데몬 실행
     명령에 `--agent-private-key <PEM path>` 로 경로 주입. 데몬은 시작 시
     1회 파일을 읽어 프로세스 메모리에만 보관. 로그/응답/재부팅 시 swap/dump
     에 노출되지 않도록 일반 파일 권한 + 프로세스 격리 운영.
  3. **회전 (Phase 2)**: 현재 자동 회전 메커니즘 없음. 키 교체 시 새 키페어
     생성 → 새 공개키로 `/agents/register` 재호출 (새 agent_id 발급) → 기존
     Agent 데몬 graceful shutdown → 신규 데몬 기동. 구 `agents` 로우는
     운영자가 수동 삭제. 자동화는 후속 ADR.
  4. **결과 (이 ADR 의 deliverable 확장)**: push 경로 end-to-end 가 PR #52
     (서버 측 `credential_payloads` 생성) + PR #53 (Agent 측 `hybrid_decrypt`
     + `PreDecryptedCredentialStore`) 로 완결됨. Agent 데몬은 개인키 없이도
     non-credential 워크플로우는 실행 가능 (CLI 인자 옵션).

---

## ADR-014 — 배포/패키징 전략: `auto-workflow-database` 파이썬 패키지 분리

- **상태**: Accepted (2026-04-15)
- **맥락**: 초기에는 monorepo 브랜치가 `from Database.src.repositories.base
  import ...` 형태로 sibling 디렉토리를 직접 import 했다. API_Server /
  Execution_Engine 이 착수되면서 이 구조가 세 가지 문제를 일으킨다:
  1. **sys.path 의존성** — 루트가 sys.path 에 있어야만 풀리므로 conftest 해킹 필요
  2. **브랜치 동기화 강제** — Database 코드 변경 시 모든 하류 브랜치가 `git pull origin main` 해야 import 가 갱신됨
  3. **경계 모호** — 내부 헬퍼(`_session.py` 등) 도 외부에서 import 가능해 공개 API 범위 불명확
- **결정**: Database 를 `auto-workflow-database` 라는 **독립 파이썬 패키지**
  로 취급한다. 두 단계로 진행:

  ### Phase 1 — editable local install (PLAN_00, 2026-04-15 완료)
  - `Database/pyproject.toml` 추가 (setuptools 백엔드, v0.1.0)
  - `Database/src/` → `Database/auto_workflow_database/` 물리 이동
  - 타 브랜치는 `pip install -e Database/` 로 설치 (repo 체크아웃 로컬 경로 참조)
  - Import 경로: `from auto_workflow_database.repositories.base import ...`
  - Database 코드 변경은 editable 덕분에 즉시 반영 (재설치 불요)
  - 하류 브랜치가 최신 Database 코드를 받으려면 여전히 `git pull origin main` 필요 (로컬 경로 참조이므로)

  ### Phase 2 — GitHub Packages wheel 게시 (시점 미정, Phase 1 안정 후)
  - Database 릴리스마다 GitHub Actions 가 wheel 빌드 → GitHub Packages 에 push
  - 타 브랜치의 `pyproject.toml` 이 버전 핀(`auto-workflow-database==0.2.1`)으로 전환
  - `git pull` 없이 `pip install -U` 만으로 업그레이드 가능 → 브랜치 동기화 비용 0
  - **Phase 1 → 2 전환 시 하류 브랜치의 `import` 문은 한 줄도 바뀌지 않음**.
    `pyproject.toml` 의존성 라인 한 줄만 교체 (local path → version spec)
- **결과**:
  - 하류 브랜치(API_Server / Execution_Engine / Inference_Service) 는
    전부 `auto_workflow_database` 네임스페이스 하나만 import
  - 공개 API 경계를 `auto_workflow_database/__init__.py` 에서 명시적으로
    export 가능 (필요 시 내부 헬퍼 은닉)
  - 버전 관리가 `pyproject.toml` 에 문자열 한 줄로 집약 → Phase 2 전환 시
    릴리스 노트/Semver 규율 도입 가능
- **대안과 기각 사유**:
  - **현상 유지 (sys.path + `Database.src.*`)** — 브랜치 늘어날수록 sync
    비용이 누적, conftest 해킹 지속 필요 → 기각
  - **Phase 2 를 지금 바로** (GitHub Packages 즉시 도입) — CI 파이프라인,
    토큰, 권한, 버전 bump 규율 필요. 초기 개발 속도 반감. Phase 1 로
    import 경계만 먼저 확립하고 CI 가 생길 때 게시 스텝 추가 → 합리적 점진
  - **Git submodule** — 현대 팀 거의 사용 안 함, UX 나쁨 → 기각
  - **코드 복사** — 재앙 → 기각
- **연관**: PLAN_00 (Database 패키지화 완료), ADR-004/013 (Fernet + 하이브리드
  암호 — 이 패키지의 공개 API 계약)
- **하류 브랜치 규칙**:
  - `API_Server` / `Execution_Engine` / `Inference_Service` 의 `pyproject.toml`
    에 `"auto-workflow-database @ file://../Database"` (Phase 1) 또는 버전 핀
    (Phase 2) 으로 선언
  - 절대로 `from Database.src...` 형태로 import 하지 않는다
  - 절대로 `Database/` 내부 파일에 직접 접근하지 않는다 (`schemas/`, `scripts/` 제외)

---

## ADR-015 — 로컬 패스워드 인증 + JWT + 이메일 검증 게이트

- **상태**: Accepted (2026-04-15)
- **맥락**: API_Server PLAN_01 이 첫 사용자-대면 엔드포인트 그룹이다. OAuth
  소셜 로그인은 Phase 2 로 미뤄졌으므로 MVP 는 **로컬 패스워드 + JWT** 하나로
  모든 인증 흐름을 감당한다. ADR-001 은 `users` 엔티티만 정의했을 뿐 auth
  방식/토큰 수명/검증 게이트/password hash 격리 규칙이 없어, 하류 브랜치가
  "어떤 토큰을 어떻게 받아 `Depends(get_current_user)` 로 풀어쓰는가" 를
  아는 단일 진실 공급원이 필요하다.
- **결정**:

  ### 1. 해시 알고리즘 — bcrypt (cost=12)
  - `bcrypt` 패키지 직접 사용 (passlib 경유 X — passlib 은 신버전 bcrypt 와
    경고 충돌이 잦고 multi-hash 스키마 기능은 MVP 에 불필요)
  - cost=12 는 OWASP 권고. 테스트에서는 cost=4 로 낮춰 속도 확보 (Settings)
  - Argon2/scrypt 는 기각 — bcrypt 로 충분하고 생태계 지원이 가장 두텁다

  ### 2. JWT — HS256, access-only, self-refresh
  - 알고리즘: **HS256** (대칭키, 단일 서비스). Phase 2 에서 다중 서비스
    확장 시 RS256 으로 이전 가능
  - Access token TTL: **60분**
  - Refresh token 없음 — 대신 **`POST /auth/refresh`** 가 *현재 유효한*
    access token 을 받아 새 60분짜리로 교환. 만료되면 재로그인
  - 표준 refresh token 방식 기각 이유: 별도 토큰 수명주기/저장소/회전 정책이
    필요해 MVP 복잡도 증가 대비 UX 이득 미미. 일반 SaaS 에서 흔한 절충안
  - 클레임: `sub` (user UUID), `iat`, `exp`, **`purpose`** (`"access"` /
    `"verify_email"`). purpose 필드가 access 토큰을 verify 엔드포인트에
    넣거나 그 반대를 차단
  - Verify email token TTL: **24시간**
  - 라이브러리: `pyjwt` (python-jose 는 유지보수 활성도 낮음)

  ### 3. 이메일 검증 게이트
  - 회원가입 즉시 `users.is_verified=false` 로 생성
  - 서버가 `purpose="verify_email"` JWT 를 만들어 `{APP_BASE_URL}/api/v1/auth/verify?token=...`
    링크를 사용자에게 발송
  - `/auth/verify` 가 토큰 검증 → `UserRepository.mark_verified` (**멱등**)
  - `/auth/login` 은 `is_verified=false` 이면 **403 email_not_verified** 거부.
    `invalid credentials` 와 구분되는 상태 코드라 UX 메시지를 명확히 낼 수 있음
- OAuth 소셜 로그인 연동 시에도 동일 `is_verified` 컬럼을 재사용 (provider
    가 이메일 검증을 이미 했다면 가입 시점에 true 로 세팅)

  ### 4. 이메일 발송 — `EmailSender` ABC
  - `ConsoleEmailSender` (MVP 기본): 링크를 로그에 출력. SMTP 의존성 없음
  - `SmtpEmailSender`: **Phase 2 스텁** (`NotImplementedError`)
  - `NoopEmailSender`: 테스트 주입용 (발송 이력 리스트 보관)
  - `make_email_sender(settings)` 가 `EMAIL_SENDER=console|smtp` 값으로 선택
  - DI 는 `create_app(email_sender=...)` 로 override 가능 → 테스트/dev/prod
    교체 비용 0

  ### 5. `password_hash` 격리 규칙 (보안 critical)
  - `User` DTO (Database 브랜치) 는 **`password_hash` 를 포함하지 않는다**
  - `UserRepository.get_password_hash(email) → bytes | None` 이 유일한
    노출 경로이며, 오직 bcrypt 검증 시점에만 호출
  - API_Server 의 `UserResponse` Pydantic 모델도 동일 원칙 — 어떤 응답
    직렬화 경로로도 해시 바이트가 누설 불가
  - 테스트 `test_me_returns_current_user_profile` 이 `"password_hash" not in body`
    를 명시 검증

  ### 6. 로그인 엔드포인트 포맷 — OAuth2PasswordRequestForm
  - `/auth/login` 은 **JSON 이 아닌 form-urlencoded**. FastAPI 의
    `OAuth2PasswordRequestForm` 을 그대로 사용 → Swagger UI 의
    "Authorize" 버튼이 즉시 동작, OpenAPI 문서 자동 생성 혜택
  - 프론트엔드는 FormData 로 전송 (약간의 번거로움은 감수)
  - JSON body 기각 이유: FastAPI 생태계 표준을 벗어나면 Swagger 연동
    설정을 직접 짜야 함

  ### 7. 에러 코드 매핑
  | 상황 | HTTP | `detail` |
  |------|------|---------|
  | 이메일 형식 불량 / 비밀번호 8자 미만 | 422 | Pydantic 검증 실패 |
  | 이메일 중복 등록 | 409 | `"email already registered"` |
  | 로그인 잘못된 자격증명 | 401 | `"invalid credentials"` |
  | 로그인 미검증 이메일 | 403 | `"email not verified"` |
  | Verify 토큰 불량/만료/purpose 불일치 | 400 | `"invalid token"` 등 |
  | Access 토큰 불량/만료 | 401 + `WWW-Authenticate: Bearer` | |
- **결과**:
  - `API_Server` 의 모든 후속 PLAN 이 `Depends(get_current_user)` 하나로 인증 획득
  - Database 쪽 `UserRepository` 는 API_Server PLAN_01 선행 PR (#16) 에서
    이미 준비 완료
  - Phase 2 OAuth 추가 시 **본 ADR 을 Update 섹션으로 확장** (새 ADR 아님) —
    `is_verified` 컬럼과 에러 코드 체계가 그대로 재사용됨
- **연관**: ADR-001 (users 엔티티), ADR-004 (Fernet 자격증명 저장 — 완전히
  별개의 암호 경로), ADR-014 (`auto-workflow-database` 패키지 분리 — 본
  ADR 의 Repository 공급 경로)

---

## ADR-016 — 노드 자격증명 주입 파이프라인: 별도 PLAN + 후속 ADR 로 설계 분리

- **상태**: Accepted (2026-04-17). 본 ADR 은 *설계 형상의 윤곽* 만 고정하고,
  **구체 파이프라인 스펙은 후속 PLAN/ADR 에서 확정**.

  **Update (2026-04-17)**: §2 의 6개 결정 축 중 **공급 모델 = BYO**,
  **복호화 스코프 = per-execution** 으로 확정. 구현 경로는
  [`PLAN_credential_pipeline.md`](./PLAN_credential_pipeline.md) 및
  - Database `PLAN_09` (PR #47, 머지) — `bulk_retrieve` + `credentials.type`
  - API_Server `PLAN_07` (PR #48, 머지) — CRUD + `execute_workflow` validation
  - Execution_Engine `PLAN_08` (TODO) — Worker 가 노드 호출 직전 평문 주입
  위 3PR 로 분할됨. 당초 "API_Server 가 execute_workflow 에서 해소" 로 쓰였던 부분은
  현 아키텍처에서 평문이 Celery broker 를 통과하게 되어 §1.6 불변식 1번을 위반하므로,
  해소 책임을 Worker (Execution_Engine) 로 옮겼다. 평문 경로는 이제 "Worker 가 DB 에서
  직접 복호화 → 노드 config 주입" 으로 broker/DB 양쪽 모두 평문이 닿지 않음.
  나머지 4개 축 (Agent 전송 / credential_type 카탈로그 / config 머지 키 규약 / 감사 로그)
  은 blueprint 에 구체화됨.
- **맥락**: PLAN_06 Slack/Delay (PR #43), PLAN_07 Email (PR #44) 로 자격증명
  기반 노드 플러그인이 본격 도입됐다. 현 구현은 노드의 `execute(input_data, config)`
  호출 시점에 `config` dict 에 **이미 평문 자격증명이 들어있다**는 전제로
  작성되어 있다 (e.g. `config["smtp_password"]`, 향후 DB Query 의 `config["connection_url"]`).
  이 전제를 채우는 파이프라인 — 즉 "누가 언제 어떤 credential_id 를 찾아
  복호화해서 어느 config 키에 머지하는가" — 은 아직 구현 공백이다.
  정책 결정 점이 다수 있어 **단일 PLAN 으로 묶기 어렵다**는 판단 하에, 현재
  노드 플러그인은 그대로 머지하되 파이프라인 자체는 별도 설계 트랙으로 분리한다.
- **결정**:

  ### 1. 노드 플러그인 계약 (변경 없음, 본 ADR 로 동결)
  - 모든 자격증명 필요 노드는 **`config` dict 에 평문 값이 이미 존재한다**는
    전제로 구현한다 (Email: `smtp_password`, DB Query(예정): `password` 또는 `connection_url`)
  - 노드는 자격증명을 **함수 지역 변수** 로만 참조하고 반환값/로그/예외에
    노출하지 않는다 (CLAUDE.md 의 "실행 시점 복호화 후 즉시 폐기" 원칙)
  - 노드는 **`credential_id` 를 직접 받지 않는다** — ID→평문 변환은 상위 계층 책임

  ### 2. 파이프라인은 후속 PLAN 으로 분리
  후속 PLAN 은 **cross-branch PLAN** 이 될 가능성이 크며 (`API_Server/` +
  `Execution_Engine/` + `Database/` 에 걸친 변경), 다음 정책 질문을 모두
  해소해야 머지 가능:

  | 결정 축 | 선택지 요약 |
  |---------|-------------|
  | **공급 모델** | (A) BYO — 고객이 자기 SMTP/DB 자격증명 등록 / (B) SaaS — 우리가 SendGrid·SES 등 제공 / (C) 하이브리드 (모드 선택) |
  | **복호화 스코프** | per-execution (workflow 실행 시작 시 전부 해제) vs per-node-call (노드 호출 직전만) — 메모리 잔존 시간 vs 호출 오버헤드 트레이드오프 |
  | **Agent 모드 전송** | ADR-013 의 AES-256-GCM+RSA-OAEP 경로 재사용. Agent 데몬이 VPC 내에서 최종 복호화 |
  | **credential_type 카탈로그** | `smtp`, `postgres_dsn`, `slack_webhook`, `http_bearer`, ... — Database 의 `credentials` 테이블 type 컬럼 값 집합 고정 |
  | **config 머지 키 규약** | workflow graph 에 `{"credential_ref": {"field": "smtp_password", "credential_id": "..."}}` 형태로 선언 → 실행 직전 파이프라인이 `config["smtp_password"]` 로 주입 (노드는 차이 못 느낌) |
  | **감사 로그** | 어떤 실행이 어떤 credential_id 를 언제 복호화했는지 audit 테이블 기록. Agent 모드는 server 측 metadata 만 |

  ### 3. 기존 ADR 와의 관계
  - **ADR-004** (Fernet AES-256 + RSA 재암호화): 자격증명 *저장/재암호화* 규약.
    본 ADR 은 그 위에서 *어떻게 꺼내 쓰는가* 를 다룸
  - **ADR-013** (Agent 자격증명 전송 AES-256-GCM + RSA-OAEP-SHA256):
    Agent 모드의 **전송** 규약. 본 파이프라인의 Agent 경로가 재사용
  - 본 ADR 은 저장 (ADR-004) → 전송/주입 (ADR-013 + 본 ADR) → 사용 (노드) 의
    중간 지점을 연결

  ### 4. 임시 상태 — 노드 운영 한계 명시
  파이프라인 PLAN 머지 전까지:
  - Email/DB Query 노드는 **unit-testable 상태** (mock 주입으로 테스트 가능)
  - **end-to-end 실행 불가** (config 에 평문을 채워주는 생산 경로 없음)
  - Frontend 의 credential 등록 UX 도 본 ADR 의 credential_type 카탈로그가
    확정되기 전에는 하드코딩 혹은 스텁으로만 가능

- **결과**:
  - PLAN_06/07 노드 PR 은 본 ADR 을 배경으로 그대로 머지 유지
  - 후속 "credential pipeline PLAN" 이 머지되면 본 ADR 의 §2 결정 축들에
    대한 구체 선택이 **Update (YYYY-MM-DD)** 섹션으로 추가되거나, 축당
    별개 ADR 로 분리
  - 그 전까지는 API_Server / Execution_Engine 팀이 **임의의 credential
    주입 구현을 선행 커밋하지 않는다** — 본 ADR 의 "노드는 credential_id 를
    받지 않는다" 계약만 준수하면 향후 파이프라인 도입 시 노드 재작성 불필요
- **연관**: ADR-004 (Fernet 저장), ADR-013 (Agent 전송), ADR-007 (LLM 노드
  추상화 — 동일하게 credential 필요), PLAN_07 EmailSendNode (PR #44)

---

## ADR-017 — 노드 카탈로그 최소 사양: 상품 출시 게이트로서의 21-노드 기준

**상태**: Accepted · **날짜**: 2026-04-18

**Context**

ADR-007/008/016 은 LLM/자격증명 등 노드 실행 *메커니즘* 을 다뤘지만, **카탈로그의 폭 (breadth) 이 상품 완결성에 미치는 영향** 은 어떤 ADR 에서도 결정된 적이 없다. 2026-04-17 PLAN_06~09 로 노드가 7 → 11 개까지 확장됐고, PLAN_11 (PR #57) 로 SaaS 4종이 추가 예정이지만, "몇 개·어떤 카테고리 확보되면 상품 출시 가능한가" 가 합의되지 않아 다음 의사결정의 기준이 없다:

- OAuth credential_type ADR 작성 시점 (Gmail/Sheets/Drive 등 노드의 블로커)
- `Inference_Service` 브랜치 신설 시점 (Heavy 유저 응대 전제)
- Frontend 브랜치 착수 시점 (credential picker + node palette 의 대상 노드 확정 필요)
- 시연회 / 체험 고객 온보딩 시점

시연회에서 체험 고객이 **기존 Zapier/n8n/Make 워크플로우를 본 시스템에 재현 가능**하려면 "기본 사용 패턴" 을 커버해야 한다. 이 "기본" 을 명시적으로 못 박지 않으면 착수·검증 범위가 계속 미뤄진다.

**Decision**

### 1. 상품 출시 게이트: 21 노드, 카테고리 8개 전부 커버

각 카테고리 최소 수량과 현 상태(PR #57 머지 후 11개 전제):

| 카테고리 | 최소 | 확보 | 확정 노드 (★는 미확보) |
|---|---|---|---|
| **Flow / Logic** | 5 | 3 | `condition`, `code`, `delay`, ★`loop_items`, ★`merge` |
| **Data Transform** | 2 | 0 | ★`transform`, ★`filter` |
| **HTTP / Webhook** | 1 | 1 | `http_request` |
| **Database** | 1 | 1 | `db_query` |
| **Messaging** | 3 | 2 | `slack_notify`, `email_send`, ★`discord_notify` |
| **LLM** | 2 | 1 | `openai_chat`, ★`anthropic_chat` |
| **CRM / PM** | 5 | 3 | `notion_create_page`, `airtable_create_record`, `linear_create_issue`, ★`notion_query_database`, ★`airtable_list_records` (+ post-MVP `github_create_issue`, `hubspot_create_contact` 권장) |
| **Dev Tools / CRM 확장** | 2 | 0 | ★`github_create_issue`, ★`hubspot_create_contact` |

**합계 21 = 상품 출시 최소**. 15 는 카테고리 커버리지가 불균형 (Flow/Transform 공백) 하여 기각.

### 2. 카테고리 "최소 수량" 의 근거

- **Flow 5개**: condition 만 있으면 "분기" 밖에 못 함. 실 워크플로우는 *분기 + 합치기 + 반복 + 지연 + 커스텀* 의 5형식 조합이 기본.
- **Data Transform 2개**: code 로 대체 가능하나 체험 고객 첫 10분의 UX 붕괴 지점 — 선언적 `transform` + 드롭 `filter` 가 표준 패턴.
- **Messaging 3개**: Slack 금지 고객군 (금융/공공 약 20%) 에 대응해 Discord (webhook 기반, 노드 복잡도 Slack 동급) 1 개 이상 필요.
- **LLM 2개**: 벤더 락인 회피 + 고객 기존 API 키 활용 — OpenAI 외 Anthropic 한 개 이상 필수.
- **CRM/PM read+write 각 1개**: 실 사용의 80% 가 read → 변환 → write 패턴. `create` 만 있고 `list/query` 없으면 Airtable/Notion 은 "쓰기 전용 블랙홀" 로 인식됨.
- **Dev Tools**: GitHub 이슈 자동화는 개발자 고객 시연의 압도적 다수 사례. HubSpot 는 영업/마케팅 체험 고객 시나리오 블로킹.

### 3. 21 초과 노드는 ADR 불필요

본 ADR 은 **출시 게이트** 만 고정한다. 21 달성 이후의 노드 추가는 PLAN → PR 단위로 진행 (각 ~50 LOC, 패턴 동일). 신규 카테고리 편입 (예: File Storage, Marketing) 은 별도 ADR.

### 4. 트랙 분리: http_bearer 먼저, OAuth 별도

- **본 트랙 (이 ADR)**: `http_bearer`, `smtp`, `postgres_dsn`, `slack_webhook` 만 사용. OAuth 전무.
- **OAuth 트랙 (별도 ADR 예정)**: `oauth2` credential_type 설계 + 토큰 갱신 플로우. 완료되면 다음 4개 노드 추가 (21 에 포함 안 됨): `gmail_send`, `google_sheets_append_row`, `google_drive_upload`, `google_calendar_create_event`.
- OAuth 트랙은 **상품 출시 후 Phase 2** — 필수 고객 요구 누적 시 착수.

### 5. 구현 분할 — 3 PR

- **PR A (Flow primitives)**: `loop_items`, `transform`, `merge`, `filter`. **executor 수정 동반 가능성** — DAG 순회 로직이 서브그래프 반복 / 다중 부모 대기 / skip signal 을 지원해야 함. PR A 를 먼저 처리하는 이유는 구조 리스크 앞에 배치.
- **PR B (Messaging/LLM)**: `discord_notify`, `anthropic_chat`. SaaS 노드 패턴 (~50 LOC) 동일.
- **PR C (SaaS 확장)**: `notion_query_database`, `airtable_list_records`, `github_create_issue`, `hubspot_create_contact`.

**Consequences**

- (+) **시연회 기준 명시** — 체험 고객이 Zapier/n8n 과 1:1 비교 가능한 기능 집합이 합의됨
- (+) **후속 ADR 착수 시점 명료** — OAuth ADR / Inference_Service / Frontend 는 21 달성 후 가동
- (+) **PR 리뷰 단위 관리** — 10 개 노드 동시 머지 대신 3 PR 분할, 구조 리스크 (executor 수정) 가 가장 작은 스코프에 집중됨
- (+) **노드 단위 PLAN 문화 유지** — 21 이후는 ADR 없이 PLAN 단위로 흘러감
- (−) **flow primitive 구현 난이도** — `loop_items` 는 executor 가 서브그래프를 반복 실행하는 패턴 지원 필요. 현 DAG 순회가 static 하므로 상당 수정 예상
- (−) **OAuth 의존 수요 지연** — Google Workspace 체험 고객은 Phase 2 까지 대기
- (−) **21 의 선형성 한계** — 카테고리별 최소는 충족하지만 각 SaaS 내 액션 수 (e.g. Notion 만 5개 작업) 까지 올라가면 다시 확장 필요 — 본 ADR 은 그것까지 커버 안 함

**Related**

- Interacts with: ADR-007 (LLM 노드 1급 추상화 — `anthropic_chat` 도입 시점에 재검토), ADR-008 (Inference_Service — 출시 게이트 후 가동), ADR-016 (credential pipeline — 21 노드 중 다수가 재사용)
- Affects branches: `Execution_Engine` (PR A/B/C), `docs` (본 ADR + PLAN_12~14), `API_Server` (추가 credential_type 없으므로 변경 없음)
- Supersedes: 없음 — 최초 결정
- Next ADR (예정): `ADR-018 — OAuth credential_type 설계 및 토큰 갱신 플로우` (Phase 2)

---

## ADR-018 — GCP Cloud SQL 관리형 Postgres + Secret Manager + Terraform IaC

**상태**: Accepted · **날짜**: 2026-04-19

**Context**

2026-04-18 E2E 스모크 테스트를 통과하며 시연 가능 수준의 MVP 에 도달 (ADR-017 21 노드 + credential pipeline + Agent 경로 전부 동작). 그러나 지금까지 모든 환경이 **로컬 Docker pgvector 컨테이너 하나에 dev / test 가 섞여 있고, 시크릿은 env 변수** 에 담겨 있다. 다음 실무 마일스톤 — 시연회 + 체험 고객 온보딩 — 을 위해서는 운영 수준으로 승격이 필요하다:

- **공유 DB 오염 문제**: PR #63 이 `test_schema_loads` 의 destructive 테스트를 패치했으나, dev ↔ 테스트 격리가 구조적으로 안 돼 있어 동류 이슈 재발 가능성 상존.
- **시크릿 누출 리스크**: Fernet 마스터 키가 env 파일 / 메모장 / 터미널 히스토리에 산재. 유출 시 credentials 테이블 전체 복호화 가능 (ADR-004).
- **체험 고객 시연 불가**: 로컬 호스트 Postgres 를 외부 유저가 볼 방법이 없음. 배포 가능한 엔드포인트 필요.
- **재현성**: 인스턴스 하나 더 만들려면 (예: staging) 수작업 반복 → 편차 발생.

**Decision**

### 1. 엔진 — Cloud SQL for PostgreSQL (AlloyDB 기각, Phase 2 재검토)

- **엔진**: Cloud SQL PostgreSQL 16 + pgvector 확장 (ADR-010 MVP 선탑재와 호환)
- **머신 타입**: `db-g1-small` 수준 (1 vCPU, 1.7 GB RAM) — MVP/시연 용도. 체험 고객 볼륨 증가 시 `db-custom` tier 로 수직 확장.
- **스토리지**: SSD 10 GB 시작, auto-resize 활성. 자동 백업 daily 7일 보존.
- **가용성**: 단일 존 (HA 비활성). 시연 단계에서 SLA 보증 대상 아님. Heavy 유저 실수요 시 regional HA 로 승격.
- **AlloyDB 기각 이유**: 최소 월 ~$400 (2 vCPU 강제), 현 시점 Heavy LLM 쿼리 수요 0. ADR-008 `Inference_Service` 가동 시 벡터 쿼리 볼륨 급증하면 그때 승격 논의 (별도 ADR).

### 2. 환경 분리 — 3-tier (dev / staging / prod)

| 환경 | Postgres | 용도 |
|---|---|---|
| **dev** | 로컬 Docker `pgvector:pg16` (포트 5435) | 개발자 로컬, 빠른 반복, 비용 0 |
| **staging** | Cloud SQL `auto-workflow-staging` | 시연회, 체험 고객 초대, CI 통합 테스트 후단 |
| **prod** | Cloud SQL `auto-workflow-prod` | 실사용 고객 (아직 없음, MVP 출시 후) |

`DATABASE_URL` 환경변수로 분기. 코드 변경 없음 — 현재 이미 env-based.

**dev 는 로컬 유지**: 클라우드 왕복 비용·지연 없이 TDD 반복이 가능해야 함. 로컬 schema/migration 은 `Database/scripts/migrate.py` 가 양쪽 모두 지원.

### 3. IaC — Terraform (gcloud CLI / 콘솔 기각)

- **위치**: `Database/deploy/terraform/`
- **이유**: staging ↔ prod 동일 모듈 재사용, diff-리뷰 가능한 변경 이력, `terraform destroy` 로 비용 즉시 정리 (시연 끝난 뒤).
- **범위**: Cloud SQL 인스턴스·DB·유저, Secret Manager 시크릿, 필요한 API 활성화 (sqladmin, secretmanager, servicenetworking). VPC / Cloud Run / IAM 정책은 본 ADR 범위 외 (후속 배포 ADR).
- **state 저장**: 초기엔 로컬 state 파일 (gitignore). 팀 단위 작업 전 GCS backend 전환은 별개 작업.
- **gcloud CLI 스크립트 기각 이유**: 수동 적용은 drift 추적 안 됨. 콘솔 조작은 재현 0.

### 4. 시크릿 — Secret Manager (env 파일 병용 안 함)

**보관 대상 (3종)**:
- `credential-master-key` — ADR-004 Fernet 키. 유출 시 파괴적.
- `jwt-secret` — ADR-015 JWT 서명 키. 유출 시 세션 탈취 가능.
- `db-password` — Cloud SQL `auto_workflow` 유저 패스워드.

**접근 경로**:
- **Terraform**: 시크릿 리소스 자체는 정의하되, **값은 placeholder** 로 생성. 실제 값은 콘솔/CLI 로 수동 주입 (Terraform state 에 secret 값이 찍히는 것 방지).
- **애플리케이션 (Cloud Run 배포 이후)**: `--set-secrets` 로 env 에 주입 또는 SDK (`google-cloud-secret-manager`) 직접 호출.
- **현 MVP 단계**: 로컬 개발은 `.env.local` (gitignore), staging/prod 만 Secret Manager 사용.

**env 파일과 병용하지 않는 이유**: 동일 시크릿이 두 곳에 있으면 어느 쪽이 진실인지 모호해지고, git 에 들어갈 위험이 실질적으로 감소하지 않음.

### 5. 연결 경로 — 개발·CI 는 Public IP + Authorized Networks, Cloud Run 은 Private IP (후속)

- **MVP**: 지정 CIDR (개발자 IP) 만 public IP 접근 허용.
- **Phase 2**: VPC Peering + Private IP + Cloud SQL Auth Proxy (Cloud Run 배포 시 필수).
- **로컬 → staging**: `cloud-sql-proxy` CLI 로 localhost 포워딩. 배포 README 에 가이드.

### 6. 마이그레이션 실행 — 기존 `migrate.py` 재사용

`Database/scripts/migrate.py` 는 `DATABASE_URL_SYNC` 만 보면 되는 구조. Terraform apply 후 사용자가:
```bash
DATABASE_URL_SYNC="postgresql://..." python Database/scripts/migrate.py
```
한 줄로 schema + 7 migrations 적용. 별도 Cloud SQL용 migration runner 불필요.

**Consequences**

- (+) **환경 격리**: dev 로컬 / staging 클라우드 → 테스트 오염 재발 불가
- (+) **시연 가능 엔드포인트**: staging 인스턴스 public IP + authorized dev IP → 시연회에서 고객 브라우저/API 호출 가능 (Frontend 붙이면)
- (+) **시크릿 중앙화**: 유출 시 즉각 rotate 가능 (Secret Manager version bump). env 파일 회수 불가능 대비 큰 개선
- (+) **재현성**: `terraform apply -var-file=staging.tfvars` 한 줄로 인스턴스 복제
- (+) **운영 비용 명확**: 시연 종료 후 `terraform destroy` → 다음날부터 $0
- (−) **월 고정비 발생**: Cloud SQL `db-g1-small` ~$25/month + 스토리지 + egress → 체감 $35~50/month (staging 한 개 기준)
- (−) **Terraform 학습 곡선**: 팀에 HCL 초심자 있으면 초기 기여 장벽. README 로 완화.
- (−) **Secret Manager 접근 IAM 설정 필요**: Cloud Run 연동 때 service account + role 바인딩 추가 단계
- (−) **AlloyDB 승격 시 비용 변동 준비**: 현 Cloud SQL 경로가 2 ~ 3 개월 내 AlloyDB 로 바뀌면 Terraform 모듈 재작성 필요 (migration 자체는 pg_dump/restore 로 가능)

**Related**

- Refines: ADR-004 (Fernet 마스터 키 — 보관 위치를 env 에서 Secret Manager 로), ADR-015 (JWT 시크릿 — 동일)
- Extends: ADR-010 (pgvector MVP 선탑재 — Cloud SQL 이 pgvector 지원해야 함)
- Defers: ADR-008 (`Inference_Service` GPU 인프라) 는 별도 Terraform 모듈 — 본 ADR 은 DB 레이어만
- Affects branches: `docs` (본 ADR), `Database` (`deploy/terraform/`, `deploy/README.md`)
- Next ADR (예정): `ADR-019 — OAuth credential_type 설계 및 토큰 갱신 플로우` (Phase 2, 기존 계획 유지. 본 ADR 이 018 슬롯을 먼저 쓴 이유는 운영 DB 승격이 시연회 블로커이기 때문)

---

## 관련 문서

- 전체 아키텍처: [`architecture.md`](./architecture.md)
- 파일 맵: [`MAP.md`](./MAP.md)
