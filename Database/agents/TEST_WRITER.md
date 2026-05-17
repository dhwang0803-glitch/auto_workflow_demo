# Test Writer Agent Instructions — Database

## Role
Writes failing tests before implementation (TDD Red step).

---

## Test-writing principles

1. Write the test before any implementation exists
2. Each test verifies exactly one requirement
3. State expected values clearly

---

## Test file location

```
Database/tests/test_{feature}.py
```

---

## Test examples

### Repository round-trip

```python
async def test_workflow_save_and_retrieve(session):
    repo = PostgresWorkflowRepository(session)
    wf = Workflow(id=uuid4(), owner_id=uuid4(), name="test", settings={}, graph={})
    await repo.save(wf)
    loaded = await repo.get(wf.id)
    assert loaded.name == "test"
```

### InMemory fake test (no DB needed)

```python
async def test_execution_status_transition():
    repo = InMemoryExecutionRepository()
    ex = Execution(id=uuid4(), workflow_id=uuid4(), status="queued", execution_mode="serverless")
    await repo.create(ex)
    await repo.update_status(ex.id, "running")
    result = await repo.get(ex.id)
    assert result.status == "running"
```

### Encryption symmetry

```python
def test_fernet_round_trip():
    store = FernetCredentialStore(session, master_key)
    cred_id = await store.store(owner_id, "api_key", {"token": "secret"})
    plain = await store.retrieve(cred_id)
    assert plain["token"] == "secret"
```

---

## Required test categories

- Repository CRUD round-trip (Workflow, Execution, User, Agent)
- Execution status transition rules
- Keyset pagination (created_at DESC, id DESC)
- CredentialStore encryption/decryption symmetry
- Agent RSA-AES hybrid re-encryption
- Engine resilience (pool timeout, slow query logging)

---

## Result-collection format

```
Total: X, PASS: X, FAIL: X
FAIL: [test ID]: [message]
```
