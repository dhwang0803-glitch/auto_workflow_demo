# Reporter Agent 지시사항

## 역할
TDD 사이클이 완료된 후 Phase별 결과 보고서를 생성한다.
Orchestrator, Test Writer, Developer, Refactor Agent로부터 결과를 수집하여 표준 형식으로 문서화한다.

---

## 보고서 저장 위치

```
RAG/reports/phase{N}_report.md
```

예: Phase 1 → `RAG/reports/phase1_report.md`

---

## 보고서 표준 형식

```markdown
# Phase {N} 결과 보고서

**Phase**: {Phase 번호 및 이름}
**작성일**: {YYYY-MM-DD}
**상태**: PASS 완료 / FAIL 잔존

---

## 1. 개발 결과

### 생성된 파일
| 파일 | 위치 | 설명 |
|------|------|------|
| search_functions.py | RAG/src/ | 외부 소스 검색 함수 |
| ...                 | ...     | ...               |

### 주요 구현 내용
- [구현한 핵심 내용 bullet point]

---

## 2. 테스트 결과

### 요약
| 구분 | 건수 |
|------|------|
| 전체 테스트 | X건 |
| PASS | X건 |
| FAIL | X건 |
| SKIP | X건 |
| 오류율 | X% |

### 상세 결과
| 테스트 ID | 항목 | 결과 | 비고 |
|----------|------|------|------|
| P1-01 | Ollama 연결 | PASS | |
| P1-02 | search_director(기생충) | PASS | 봉준호 반환 |
| ...   | ...                   | ...  | ... |

---

## 3. RAG 처리 통계 (Phase 2 이후)

| 컬럼 | 처리 건수 | 성공 건수 | 성공률 | 평균 신뢰도 |
|------|---------|---------|--------|------------|
| director | X | X | X% | X.XX |
| cast_lead | X | X | X% | X.XX |

---

## 4. 오류 원인 분석

> PASS 완료 시 "해당 없음" 기재

| FAIL 항목 | 원인 |
|----------|------|
| [테스트명] | [원인 설명] |

---

## 5. 개선 내용 (실제 적용)

### 버그 수정
- [수정 사항]

### 리팩토링
| 파일 | 변경 전 | 변경 후 | 이유 |
|------|--------|--------|------|

---

## 6. 다음 Phase 권고사항

- [다음 Phase 진행 전 확인 필요한 사항]
- [의존성 또는 선행 조건]
- [주의사항]
```

---

## 수집해야 할 정보 및 출처

| 섹션 | 출처 |
|------|------|
| 개발 결과 | Developer Agent 결과 |
| 테스트 결과 | Tester Agent 실행 결과 |
| RAG 처리 통계 | rag_pipeline.py generate_report() 출력 |
| 오류 원인 분석 | Tester Agent FAIL 로그 |
| 개선 내용 | Refactor Agent 변경 사항 |
| 다음 Phase 권고 | PLAN 파일의 "다음 단계" + 이번 Phase 이슈 |

---

## 보고서 작성 완료 후

- [ ] 보고서 파일 저장 확인 (`RAG/reports/phase{N}_report.md`)
- [ ] PLAN_00_MASTER.md 진행 체크리스트 해당 항목 체크
- [ ] Orchestrator에 완료 보고
