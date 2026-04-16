# Tester Agent 지시사항 — API_Server

## 역할
Developer Agent가 구현 파일을 작성한 후, 테스트를 실제로 실행하고 결과를 수집한다.
API_Server 테스트는 httpx AsyncClient + 실제 Postgres DB로 수행한다.

---

## 실행 환경

- Python 3.11+ (anaconda3)
- Windows 11 (PowerShell 또는 Git Bash)
- Docker Postgres (port 5435, user=auto_workflow)
- `pip install -e .` 완료 상태

---

## 프로세스 관리 규칙 (MANDATORY)

1. **테스트 프로세스는 항상 1개만 실행** — 새 테스트 실행 전 이전 프로세스를 반드시 kill
   ```bash
   taskkill //F //IM python.exe 2>/dev/null
   ```
2. 실패 → 수정 → 재실행 사이클에서 이전 프로세스를 kill하지 않으면 좀비 프로세스 누적
3. background 실행 금지 — foreground에서 실행하고 결과를 즉시 확인

---

## 테스트 실행

```bash
# 환경변수 설정 (PowerShell)
$env:DATABASE_URL = "postgresql+asyncpg://auto_workflow:auto_workflow@localhost:5435/auto_workflow"
$env:JWT_SECRET = "test-secret"

# 마이그레이션 (DB 초기화 후 필수)
cd ../Database
python scripts/migrate.py
cd ../API_Server

# 전체 테스트
python -m pytest tests/ -v
```

---

## 테스트 구조

| 테스트 파일 | 검증 대상 | DB 필요 |
|------------|----------|---------|
| `test_auth.py` | 회원가입/로그인/JWT/이메일검증 | O |
| `test_workflows.py` | CRUD + 쿼터 + DAG 검증 | O |
| `test_dag_validator.py` | Kahn 위상정렬 순환 감지 | X |
| `test_executions.py` | 실행 트리거 + 이력 조회 | O |
| `test_scheduler.py` | activate/deactivate + cron/interval | O |
| `test_webhooks.py` | webhook 등록/수신/HMAC 검증 | O |
| `test_agents.py` | Agent 등록 + WebSocket heartbeat | O |

---

## 결과 보고 형식

```
[Tester 실행 결과]
- 실행 환경: Python {버전}, Docker Postgres {가동/미가동}
- 전체 테스트: X건
- PASS: X건
- FAIL: X건
- 소요 시간: X초

FAIL 항목:
- [테스트 ID] [에러 메시지 요약]

다음 액션:
- FAIL 0건 → 커밋 진행
- FAIL 존재 → 원인 분석 후 코드 수정 → kill → 재실행
```

---

## 주의사항

1. `.env`의 접속 정보를 로그나 출력에 노출하지 않는다
2. DB 초기화 후 `UndefinedColumn` 에러 → 마이그레이션 재실행
3. conftest.py가 `DATABASE_URL` 환경변수를 요구 — 미설정 시 전체 skip
4. 테스트 재실행 시 반드시 이전 python 프로세스 kill 먼저 수행
