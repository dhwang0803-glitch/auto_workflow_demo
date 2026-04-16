# Developer Agent 지시사항 — API_Server

## 역할
Test Writer Agent가 작성한 테스트를 통과하는 최소한의 코드를 구현한다 (TDD Green 단계).
과도한 설계나 불필요한 기능을 추가하지 않는다.

---

## 구현 원칙

1. **테스트 통과 최우선**: 현재 실패하는 테스트를 통과시키는 것만 구현한다
2. **최소 구현**: 테스트를 통과하는 가장 단순한 코드를 작성한다
3. **CLAUDE.md 준수**: `API_Server/CLAUDE.md` 파일 위치 규칙과 인터페이스를 벗어나지 않는다
4. **함수 증식 금지**: 1회용 헬퍼/thin wrapper 만들지 않는다. 3줄 중복이 추상화보다 낫다

---

## 파일 위치

| 파일 종류 | 위치 |
|-----------|------|
| REST 라우터 | `app/routers/` |
| 비즈니스 로직 | `app/services/` |
| Pydantic 스키마 | `app/models/` |
| FastAPI 앱 + DI 조립 | `app/main.py` |
| 의존성 일원화 | `app/container.py` (AppContainer) |
| pytest | `tests/` |

**`API_Server/` 루트에 `.py` 파일 직접 생성 금지.**

---

## 의존성 조립

새 Repository나 Service를 추가할 때는 `app/container.py`의 `AppContainer` 한 곳만 수정한다.
`main.py`나 `scheduler.py`에서 직접 객체를 생성하지 않는다.

```python
# app/container.py — 여기서만 조립
class AppContainer:
    def __init__(self, settings):
        self.engine = build_engine(settings.database_url)
        self.sessionmaker = build_sessionmaker(self.engine)
        self.user_repo = PostgresUserRepository(self.sessionmaker)
        # ... 새 repo는 여기에 추가
```

---

## 비동기 코드 원칙

1. FastAPI 라우터와 서비스는 **모두 `async def`**로 작성한다
2. Blocking I/O 직접 호출 금지 → `httpx.AsyncClient`, `asyncpg` 사용
3. CPU 바운드 작업은 Celery 태스크로 분리한다

---

## DB 접근 원칙 (N+1 금지)

```python
# 금지: 루프 안에서 fetch
for wid in workflow_ids:
    row = await session.execute(select(Workflow).where(Workflow.id == wid))

# 올바른 패턴: 배치 조회
rows = await session.execute(select(Workflow).where(Workflow.id.in_(workflow_ids)))
```

---

## 에러 처리

`DomainError` 서브클래스를 정의하고 `http_status`를 class 속성으로 지정한다.
라우터에 `try/except` 없이 전역 핸들러가 자동 매핑.

```python
class NotFoundError(DomainError):
    http_status = 404
```

---

## 구현 완료 후 자가 점검

- [ ] 하드코딩된 API 키, IP, 비밀번호 없음
- [ ] 루프 안에 DB 쿼리 없음 (N+1 없음)
- [ ] 새 repo/service는 AppContainer에만 추가됨
- [ ] 1회용 헬퍼 함수 만들지 않았음
- [ ] datetime은 `DateTime(timezone=True)` + `datetime.now(timezone.utc)` 통일
