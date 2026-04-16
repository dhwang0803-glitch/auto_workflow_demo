# PLAN_05 — ConditionNode + CodeNode (RestrictedPython 샌드박스)

> 상태: DRAFT  
> 브랜치: `Execution_Engine`  
> 선행: PLAN_01 (BaseNode/Registry), PLAN_02 (DAG executor)

## 목적

워크플로우에서 분기 로직(ConditionNode)과 사용자 정의 코드 실행(CodeNode)을
지원. CodeNode는 **절대 eval()/exec() 직접 사용 금지** — RestrictedPython
AST 검사 + 내장 함수 화이트리스트로 안전한 실행 보장.

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/nodes/condition.py` | ConditionNode — 조건 분기 |
| `src/nodes/code.py` | CodeNode — RestrictedPython 샌드박스 실행 |
| `src/runtime/sandbox.py` | RestrictedPython 컴파일 + 실행 헬퍼 |
| `tests/test_condition_node.py` | ConditionNode 테스트 |
| `tests/test_code_node.py` | CodeNode + 샌드박스 테스트 |

### 수정
| 파일 | 변경 |
|------|------|
| `pyproject.toml` | `RestrictedPython` 의존성 추가 |

## 구현 상세

### 1. ConditionNode (`src/nodes/condition.py`)

input_data에서 조건 평가 → `"result": true/false` 반환.
executor의 edge 시스템이 output을 후속 노드에 전달하므로,
후속 노드가 `input_data["result"]`로 분기 판단 가능.

지원 연산자: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `contains`

```python
class ConditionNode(BaseNode):
    node_type = "condition"

    async def execute(self, input_data, config):
        left = input_data.get(config["left_field"])
        op = config["operator"]
        right = config["right_value"]
        # 연산자별 비교 → {"result": bool}
```

### 2. sandbox.py (`src/runtime/sandbox.py`)

RestrictedPython으로 사용자 코드를 컴파일 + 실행하는 단일 함수.
타임아웃은 `asyncio.wait_for`로 제한.

```python
def run_restricted(code: str, inputs: dict, *, timeout_seconds: int = 30) -> dict:
    byte_code = compile_restricted(code, '<user_code>', 'exec')
    safe_globals = {
        "__builtins__": safe_builtins,
        "_getiter_": default_guarded_getiter,
        "_getattr_": default_guarded_getattr,
        "inputs": dict(inputs),
        "result": {},
    }
    exec(byte_code, safe_globals)
    return safe_globals["result"]
```

**허용**: 기본 연산, 반복, 조건, 문자열 조작, math  
**차단**: import, open, eval, exec, __import__, os, sys, subprocess

### 3. CodeNode (`src/nodes/code.py`)

```python
class CodeNode(BaseNode):
    node_type = "code"

    async def execute(self, input_data, config):
        code = config["source"]
        timeout = config.get("timeout_seconds", 30)
        return await asyncio.wait_for(
            asyncio.to_thread(run_restricted, code, input_data, timeout_seconds=timeout),
            timeout=timeout,
        )
```

`asyncio.to_thread`로 별도 스레드 실행 → 메인 이벤트루프 블로킹 방지.
`asyncio.wait_for`로 전체 타임아웃 보장.

## 테스트 전략

### test_condition_node.py
1. `test_eq_true` — 같은 값 → result=True
2. `test_eq_false` — 다른 값 → result=False
3. `test_gt_operator` — 숫자 비교
4. `test_contains_operator` — 문자열 포함 검사
5. `test_missing_field_returns_false` — input에 필드 없음 → False

### test_code_node.py
1. `test_simple_computation` — `result["sum"] = inputs["a"] + inputs["b"]`
2. `test_loop_and_list` — 반복문 사용
3. `test_import_blocked` — `import os` → CompileError
4. `test_open_blocked` — `open("/etc/passwd")` → 차단
5. `test_timeout_exceeded` — 무한루프 → TimeoutError

## 의존성 추가

```toml
dependencies = [
    "httpx>=0.27",
    "celery[redis]>=5.3",
    "websockets>=12.0",
    "RestrictedPython>=7.0",
    "auto-workflow-database",
]
```

## 체크리스트

- [ ] `src/runtime/sandbox.py` — RestrictedPython 실행 함수
- [ ] `src/nodes/condition.py` — ConditionNode + registry 등록
- [ ] `src/nodes/code.py` — CodeNode + registry 등록
- [ ] `pyproject.toml` — RestrictedPython 추가
- [ ] 테스트 10개 작성 + pass
- [ ] 커밋 → push → PR

## 후속 작업

- PLAN_05 완료 후 Container 리팩터링 (API_Server + Execution_Engine)
- 추가 노드 타입 확장 (Slack, Email, DB Query 등)
