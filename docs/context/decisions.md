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

**상태**: Accepted · **날짜**: 2026-04-14

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

**Related**
- Refines: ADR-006 (Repository 계약 확장)
- Interacts with: ADR-005 (코드 노드 30초 타임아웃은 Approval/LLM 수명주기와 분리)
- Affects branches: `Execution_Engine` (런타임/LlmNode/ApprovalNode), `Database` (스키마), `API_Server` (승인 엔드포인트, 재개 디스패치, 알림 디스패치), `Frontend` (스키마 편집 UI, 승인 인박스)

---

## 관련 문서

- 전체 아키텍처: [`architecture.md`](./architecture.md)
- 파일 맵: [`MAP.md`](./MAP.md)
