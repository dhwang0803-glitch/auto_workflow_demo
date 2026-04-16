# PLAN_01 — 패키지 셋업 + BaseNode + NodeRegistry + HttpRequestNode

> **브랜치**: `Execution_Engine` · **작성일**: 2026-04-16 · **상태**: Draft
>
> Execution_Engine 의 첫 PLAN. 패키지 인프라를 세우고 노드 플러그인
> 시스템의 뼈대 (BaseNode ABC + NodeRegistry) 와 첫 번째 구현체
> (HttpRequestNode) 를 만든다.

## 1. 범위

**In**
- `pyproject.toml` — 패키지 메타데이터 + 의존성 (httpx, auto-workflow-database)
- `pytest.ini` — asyncio_mode=auto
- `src/nodes/__init__.py`
- `src/nodes/base.py` — `BaseNode` ABC (`node_type`, `execute`)
- `src/nodes/registry.py` — `NodeRegistry` (dict 기반 type→class 매핑)
- `src/nodes/http_request.py` — `HttpRequestNode` (httpx 로 외부 API 호출)
- `tests/test_http_request_node.py` — 4 테스트

**Out**
- DAG executor (runtime/) — PLAN_02
- Celery dispatcher — PLAN_03
- Agent 데몬 — PLAN_04
- ConditionNode, CodeNode — PLAN_05

## 2. BaseNode ABC

```python
class BaseNode(ABC):
    @property
    @abstractmethod
    def node_type(self) -> str: ...

    @abstractmethod
    async def execute(self, input_data: dict, config: dict) -> dict: ...
```

- `input_data`: 이전 노드의 output (첫 노드는 빈 dict)
- `config`: workflow graph 의 노드별 config
- return: output dict (다음 노드의 input_data 로 전달)
- 실패 시 예외 raise — executor 가 잡아서 처리

## 3. NodeRegistry

```python
class NodeRegistry:
    def register(self, node_class: type[BaseNode]) -> None
    def get(self, node_type: str) -> type[BaseNode]
    def list_types(self) -> list[str]

registry = NodeRegistry()  # 모듈 레벨 싱글턴
```

## 4. HttpRequestNode

- `node_type = "http_request"`
- config: `{"method": "GET", "url": "...", "headers": {}, "body": {}}`
- `httpx.AsyncClient` 로 요청, 응답 `{"status_code": N, "body": ..., "headers": ...}` 반환
- timeout: config 에서 `timeout_seconds` (기본 30)

## 5. 테스트

1. `test_http_request_get_happy` — mock 서버 GET → 200
2. `test_http_request_post_with_body` — POST + JSON body
3. `test_http_request_timeout` — timeout 초과 시 예외
4. `test_registry_register_and_get` — 등록 + 조회 + list_types

## 6. 함수 증식 방지

- `BaseNode.execute` 한 메서드만. `validate_config`, `pre_execute`, `post_execute` 훅 금지.
- `HttpRequestNode.execute` 본문에서 httpx 호출 + 응답 변환 직선 처리.
- `NodeRegistry` 는 dict 래퍼 — `_validate`, `_auto_discover` 같은 매직 금지.
