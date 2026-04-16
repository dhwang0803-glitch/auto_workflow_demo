# Refactor Agent 지시사항 — Execution_Engine

## 역할
모든 테스트가 PASS된 이후에만 실행. 테스트 통과 상태를 유지하면서 코드 품질 개선.

---

## 핵심 원칙

1. **테스트 통과 유지**: 리팩토링 후 전체 테스트 재실행 PASS 확인
2. **기능 변경 금지**: 동작 결과가 달라지면 안 됨
3. **범위 제한**: `src/` 코드만 수정
4. **작은 단위**: 한 번에 하나씩 개선 후 테스트 확인

---

## 검토 항목

### 코드 품질
- [ ] 1회용 헬퍼 → 인라인 처리 (함수 증식 지양)
- [ ] NodeRegistry 의도 주석 유지 (클래스 저장, 인스턴스 아님)
- [ ] sandbox 가드 함수 누락 확인 (_getitem_, _write_, _inplacevar_)

### 아키텍처
- [ ] 새 repo/node가 WorkerContainer 외부에서 생성되는지
- [ ] executor와 노드 간 인터페이스 일관성

### 성능
- [ ] asyncio.gather 병렬 실행이 올바르게 적용되는지
- [ ] to_thread + wait_for 타임아웃 패턴 누락

---

## 범위 제외

- `tests/`, `plans/`, `config/`, `scripts/`

---

## 완료 후

1. `taskkill` 후 전체 테스트 재실행
2. PASS/FAIL 건수 동일 확인
3. 변경 내용 → Reporter Agent에 전달
