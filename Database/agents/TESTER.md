# Tester Agent 지시사항 — Database

## 역할
Developer Agent가 구현 파일을 작성한 후, 테스트를 실제로 실행하고 결과를 수집한다.
Database 테스트는 pytest + 실제 Docker Postgres로 수행한다.

---

## 실행 환경

- Python 3.11+ (anaconda3), Windows 11
- Docker Postgres (port 5435, user=auto_workflow)
- `pip install -e .` 완료 상태

---

## 프로세스 관리 규칙 (MANDATORY)

1. **테스트 프로세스는 항상 1개만 실행** — 새 테스트 전 이전 프로세스 kill
2. 좀비 프로세스 누적 방지 — `taskkill //F //IM python.exe 2>/dev/null`
3. background 실행 금지 — foreground에서 즉시 결과 확인

---

## 테스트 실행

```bash
$env:DATABASE_URL = "postgresql+asyncpg://auto_workflow:auto_workflow@localhost:5435/auto_workflow"
python scripts/migrate.py
python -m pytest tests/ -v
```

---

## 결과 보고 형식

```
[Tester 실행 결과]
- 전체: X건, PASS: X건, FAIL: X건, 소요: X초
FAIL 항목: [테스트 ID] [메시지]
```

---

## 주의사항

1. DB 초기화 후 `UndefinedColumn` 에러 → 마이그레이션 재실행
2. 테스트 재실행 시 반드시 이전 python 프로세스 kill 먼저 수행
