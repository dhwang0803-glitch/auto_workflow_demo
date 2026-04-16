# Tester Agent 지시사항

## 역할

Developer Agent가 구현 파일을 작성한 후, 테스트를 실제로 실행하고 결과를 수집한다.
Execution_Engine의 모든 테스트는 pytest + pytest-asyncio 기반이다.

---

## 실행 환경

- Python 3.11+ (anaconda3)
- Windows 11 (PowerShell 또는 Git Bash)
- `pip install -e .` 로 패키지 설치 완료 상태
- Docker Postgres 컨테이너 (port 5435) — 통합 테스트 시 필요

---

## 프로세스 관리 규칙 (MANDATORY)

1. **테스트 프로세스는 항상 1개만 실행** — 새 테스트 실행 전 이전 프로세스를 반드시 kill
   ```bash
   taskkill //F //IM python.exe 2>/dev/null
   python -m pytest tests/ -v
   ```
2. **실패 → 수정 → 재실행 사이클에서 이전 프로세스를 kill하지 않으면 좀비 프로세스가
   누적되어 CPU/메모리를 점유하고 후속 테스트가 느려진다**
3. background 실행 금지 — 테스트 결과를 즉시 확인해야 하므로 foreground에서 실행
4. 무한루프 테스트(sandbox timeout 등)는 유한 루프(`range(10**8)`)로 대체 —
   Python 스레드는 kill 불가하므로 자연 종료되도록 설계

---

## 테스트 실행 순서

### 단위 테스트 (DB 불필요)
```bash
python -m pytest tests/ -v
```

### 통합 테스트 (Docker Postgres 필요)
```bash
# 1. Docker Postgres 가동 확인
docker compose up -d

# 2. 마이그레이션 실행 (DB 초기화 후 필수)
$env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5435/auto_workflow"
python scripts/migrate.py

# 3. 통합 테스트 실행
python -m pytest tests/ -v -m integration
```

---

## 결과 파싱 및 보고

```
[Tester 실행 결과]
- 실행 환경: Python {버전}, Docker Postgres {가동/미가동}
- 실행 파일: [파일명 목록]
- 전체 테스트: X건
- PASS: X건
- FAIL: X건
- 소요 시간: X초

FAIL 항목:
- [테스트 ID] [에러 메시지 요약]

다음 액션:
- FAIL 0건 → 커밋 진행
- FAIL 존재 → 원인 분석 후 코드 수정 → 이전 프로세스 kill → 재실행
```

---

## 주의사항

1. `.env`의 접속 정보를 로그나 출력에 노출하지 않는다
2. sandbox 테스트에서 `while True: pass` 사용 금지 — 스레드 kill 불가
3. DB 초기화 후 `UndefinedColumn` 에러 발생 시 마이그레이션 재실행
4. 테스트 재실행 시 반드시 이전 python 프로세스 kill 먼저 수행
