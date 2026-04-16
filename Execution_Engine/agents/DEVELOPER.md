# Developer Agent 지시사항 — Execution_Engine

## 역할
Test Writer Agent가 작성한 테스트를 통과하는 최소한의 코드를 구현한다 (TDD Green 단계).

---

## 구현 원칙

1. **테스트 통과 최우선**: 현재 실패하는 테스트를 통과시키는 것만 구현한다
2. **최소 구현**: 테스트를 통과하는 가장 단순한 코드를 작성한다
3. **CLAUDE.md 준수**: `Execution_Engine/CLAUDE.md` 파일 위치 규칙을 벗어나지 않는다
4. **함수 증식 금지**: 1회용 헬퍼/thin wrapper 만들지 않는다

---

## 파일 위치

| 파일 종류 | 위치 |
|-----------|------|
| 노드 구현 (BaseNode 상속) | `src/nodes/` |
| Celery 태스크 | `src/dispatcher/serverless.py` |
| DAG 실행 런타임 | `src/runtime/executor.py` |
| RestrictedPython 샌드박스 | `src/runtime/sandbox.py` |
| Agent 데몬 | `src/agent/` |
| 의존성 일원화 | `src/container.py` (WorkerContainer) |
| Celery Worker 실행 | `scripts/worker.py` |
| Agent 실행 | `scripts/agent_run.py` |
| pytest | `tests/` |

**`Execution_Engine/` 루트에 `.py` 파일 직접 생성 금지.**

---

## 의존성 조립

새 Repository를 추가할 때는 `src/container.py`의 `WorkerContainer` 한 곳만 수정한다.

---

## NodeRegistry 패턴

Registry는 **클래스**를 저장한다. `registry.get(type)()`로 매 호출마다 새 인스턴스 생성.
병렬 실행 시 독립 인스턴스 보장.

```python
class MyNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "my_node"
    async def execute(self, input_data: dict, config: dict) -> dict:
        ...

registry.register(MyNode)
```

---

## 샌드박스 규칙

**절대 `eval()`/`exec()` 직접 사용 금지.**
CodeNode는 `RestrictedPython` → `compile_restricted()` → 별도 스레드 실행.

---

## 비동기 원칙

1. 노드 `execute()`는 `async def`
2. DAG 실행: `asyncio.gather`로 같은 레벨 노드 병렬 실행
3. CPU 바운드 → `asyncio.to_thread`로 분리

---

## 구현 완료 후 자가 점검

- [ ] 하드코딩된 URL, 비밀번호 없음
- [ ] 새 노드는 `registry.register()` 호출 포함
- [ ] 새 repo는 WorkerContainer에만 추가
- [ ] 1회용 헬퍼 없음
- [ ] 무한루프 테스트 금지 (유한 루프로 대체)
