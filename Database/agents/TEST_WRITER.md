# Test Writer Agent 지시사항 — Database

## 역할
구현 전에 실패하는 테스트를 먼저 작성한다 (TDD Red 단계).

---

## 테스트 작성 원칙

1. 구현 코드가 없어도 테스트를 먼저 작성한다
2. 각 테스트는 하나의 요구사항만 검증한다
3. 기대값을 명확하게 명시한다

---

## 테스트 파일 위치

```
Database/tests/test_{기능명}.py
```

---

## 테스트 작성 예시

### Repository 라운드트립

```python
async def test_workflow_save_and_retrieve(session):
    repo = PostgresWorkflowRepository(session)
    wf = Workflow(id=uuid4(), owner_id=uuid4(), name="test", settings={}, graph={})
    await repo.save(wf)
    loaded = await repo.get(wf.id)
    assert loaded.name == "test"
```

### InMemory fake 테스트 (DB 불필요)

```python
async def test_execution_status_transition():
    repo = InMemoryExecutionRepository()
    ex = Execution(id=uuid4(), workflow_id=uuid4(), status="queued", execution_mode="serverless")
    await repo.create(ex)
    await repo.update_status(ex.id, "running")
    result = await repo.get(ex.id)
    assert result.status == "running"
```

### 암호화 대칭성

```python
def test_fernet_round_trip():
    store = FernetCredentialStore(session, master_key)
    cred_id = await store.store(owner_id, "api_key", {"token": "secret"})
    plain = await store.retrieve(cred_id)
    assert plain["token"] == "secret"
```

---

## 필수 테스트 카테고리

- Repository CRUD 라운드트립 (Workflow, Execution, User, Agent)
- Execution 상태 전이 규칙
- Keyset pagination (created_at DESC, id DESC)
- CredentialStore 암호화/복호화 대칭성
- Agent RSA-AES 하이브리드 재암호화
- Engine resilience (pool timeout, slow query logging)

---

## 결과 수집 형식

```
전체: X건, PASS: X건, FAIL: X건
FAIL: [테스트 ID]: [메시지]
```
