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

## 관련 문서

- 전체 아키텍처: [`architecture.md`](./architecture.md)
- 파일 맵: [`MAP.md`](./MAP.md)
