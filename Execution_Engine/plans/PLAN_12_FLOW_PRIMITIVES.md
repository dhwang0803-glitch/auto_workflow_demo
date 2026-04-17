# PLAN_12 — Flow Primitives 4종 (loop/transform/merge/filter)

> 선행: ADR-017 (노드 카탈로그 최소 사양) — 21 노드 달성의 Flow/Transform
> 카테고리 공백 해소. 본 PLAN 은 그 중 4개 (PR A).

## 목적

현 Flow primitive 3개 (`condition`, `code`, `delay`) 는 *분기* 만 지원. 실제
워크플로우는 *분기 + 반복 + 합치기 + 드롭 + 변환* 의 조합 — ADR-017 §2 근거.
본 PR 은 네 축을 한 번에 채운다.

## 설계 결정 — executor 수정 회피

현 executor (`src/runtime/executor.py`) 는:
- Kahn 위상 정렬 + level 별 `asyncio.gather` 병렬 실행
- predecessor 출력을 dict merge 하여 input_data 로 전달 (line 60~64)
- skip / subgraph / loop semantic 없음

본 PLAN 은 executor 를 **건드리지 않고** 네 primitive 를 구현한다. 이유:

1. **Frontend 부재** — loop body 를 visual subgraph 로 그릴 에디터가 아직 없음. visual representation 이 없는 지금 subgraph semantic 도입은 투자 대비 이득 없음.
2. **리스크 격리** — executor 수정은 기존 노드/디스패처/Agent 전 경로의 regression 위험. 본 PLAN 스코프 내 한정.
3. **충분성** — 4 primitive 모두 데이터 연산 또는 "노드가 노드를 호출" 패턴으로 표현 가능.

향후 실제 visual subgraph 가 필요해지면 별도 PLAN 에서 executor 리팩터 + `loop_items` 재구현 (본 PLAN 은 그때 deprecated 될 수 있음).

## 스코프

4개 노드:

| node_type | 유형 | 책임 |
|---|---|---|
| `merge` | Flow | 다중 predecessor 수렴점. input_data 를 그대로 반환. 그래프 가독성 용. |
| `transform` | Data | 선언적 필드 매핑. `{input.foo}` 템플릿 치환. |
| `filter` | Data | `items` 배열 필터링. condition 표현식 평가. |
| `loop_items` | Flow | worker 노드를 N 회 호출. 각 iteration 에 item 전달. |

## 파일 변경

### 신규
| 파일 | 역할 |
|------|------|
| `src/nodes/merge.py` | MergeNode — no-op passthrough |
| `src/nodes/transform.py` | TransformNode — 템플릿 치환 |
| `src/nodes/filter.py` | FilterNode — 배열 필터 |
| `src/nodes/loop_items.py` | LoopItemsNode — worker 반복 실행 |
| `tests/test_merge_node.py` | 단위 테스트 |
| `tests/test_transform_node.py` | 단위 테스트 |
| `tests/test_filter_node.py` | 단위 테스트 |
| `tests/test_loop_items_node.py` | 단위 테스트 |

수정: 없음 (executor / registry 변경 없음).

## 노드 스펙

### 1. MergeNode

```
config: (없음)
input_data: predecessor outputs 의 dict merge (executor 가 이미 처리)
output: input_data 를 그대로 반환
```

그래프 상 명시적 수렴점. executor 가 이미 predecessor merge 를 함 (line 60~64) 이라 실질 no-op 이지만, UI 상 분기 후 합치기 의도를 표현하는 용도.

### 2. TransformNode

```
config:
  mapping: dict[str, str|int|bool|None]
    # 값이 str 이고 "{...}" 패턴이면 input_data 에서 키 조회 후 치환
    # 중첩 키는 "{input.foo.bar}" 로 점 경로 지원
  defaults?: dict  # 치환 실패 시 대체값

output: mapping 을 템플릿 치환한 dict
```

예:
```json
{
  "mapping": {
    "name": "{input.user.name}",
    "age": "{input.user.age}",
    "source": "airtable"
  }
}
```

### 3. FilterNode

```
config:
  items_key: str (default "items")  # input_data 에서 배열 추출 키
  condition:
    field: str            # item 의 필드명 (점 경로)
    operator: str         # eq, ne, gt, lt, gte, lte, contains, in, truthy
    value?: any           # operator 가 truthy 면 불필요

input_data: { items_key: list[dict] }
output: { items: [filtered], count: int }
```

### 4. LoopItemsNode

```
config:
  items_key?: str (default "items")  # input_data 에서 배열 추출
  items?: list                       # 또는 정적 items
  worker_type: str                   # registry 에 등록된 노드 타입
  worker_config: dict                # 템플릿. "{item}" 또는 "{item.field}" 치환
  max_concurrency?: int (default 5)  # asyncio.gather 제한

동작:
  - items 각 원소에 대해 worker_config 템플릿 치환
  - registry.get(worker_type)() 로 worker 인스턴스
  - await worker.execute({"item": item}, interpolated_config)
  - asyncio.Semaphore(max_concurrency) 로 제한된 병렬

output: { results: list[dict], count: int, failures: int }
```

**실패 정책**: worker 하나가 실패해도 전체 계속. 실패 결과는 `{_error: str}` 로 results 에 포함. failures 카운트로 요약. (all-or-nothing 은 `transaction` 패턴으로 별도 노드 후속.)

## 공통 구현 유틸 — 템플릿 치환

`transform` 과 `loop_items` 양쪽에서 `{input.foo.bar}` / `{item.field}` 치환 필요. 노드별 중복 방지 위해 **각 노드 파일 내에 단순 `_interpolate(value, ctx)` 함수** 로 보유 (3줄 원칙 — 2곳만 쓰므로 공통 util 추출 안 함).

로직: 문자열이 정확히 `{path}` 패턴이면 점 경로 resolve, 아니면 `str.format_map` 유사 치환. 누락 키는 defaults 또는 원문 유지.

## 보안 불변식

- `loop_items` 가 worker 로 허용하는 노드는 registry 제약만 — 재귀 loop_items 방지는 **depth=1 하드 캡** (worker 가 또 loop_items 면 KeyError 발생시킴)
- worker_config 템플릿 치환은 repr/eval 미사용 — 문자열 대체만

## 테스트 전략 (각 노드 3~5개, 총 17개)

### test_merge_node.py (2)
- `test_merge_returns_input_as_output`
- `test_merge_with_empty_input`

### test_transform_node.py (4)
- `test_simple_mapping` — 평문 치환
- `test_nested_path_substitution` — `{input.user.name}` 점 경로
- `test_static_values_preserved` — 템플릿 아닌 값 그대로
- `test_missing_key_uses_default`

### test_filter_node.py (5)
- `test_filter_eq_operator`
- `test_filter_gt_operator`
- `test_filter_contains_operator`
- `test_filter_truthy_operator` (value 생략)
- `test_filter_empty_list_returns_empty`

### test_loop_items_node.py (6)
- `test_loop_calls_worker_per_item` — mock worker, N 회 호출 검증
- `test_loop_interpolates_item_in_config` — `{item.name}` 치환
- `test_loop_aggregates_results`
- `test_loop_respects_concurrency_limit` — Semaphore 동작
- `test_loop_failure_does_not_abort_siblings` — 1개 실패 시 나머지 성공
- `test_loop_recursive_loop_items_rejected` — depth=1 cap

## 체크리스트

- [ ] `src/nodes/merge.py` + 테스트 2
- [ ] `src/nodes/transform.py` + 테스트 4
- [ ] `src/nodes/filter.py` + 테스트 5
- [ ] `src/nodes/loop_items.py` + 테스트 6
- [ ] 전체 테스트 79 → 96 pass
- [ ] 커밋 → push → PR A

## Out of scope

- Visual subgraph loop (loop body 를 그래프로 그리기) — Frontend + executor 리팩터 필요
- Batch size / pagination (n8n SplitInBatches 대응) — 후속 `batch_split` 노드로 분리
- All-or-nothing transactional loop — 후속 `transaction` 노드 또는 config 플래그
- 복잡 조건 표현식 (AND/OR 조합) — 현 filter 는 단일 operator 만. 복합 조건은 체이닝 또는 code 노드
- JSONata / Jq 식 복잡 template engine — 본 PLAN 은 점 경로 치환만
