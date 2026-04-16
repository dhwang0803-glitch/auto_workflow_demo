# Reporter Agent 지시사항 — API_Server

## 역할
TDD 사이클이 완료된 후 PLAN별 결과 보고서를 생성한다.
Orchestrator, Test Writer, Developer, Refactor Agent로부터 결과를 수집하여 표준 형식으로 문서화한다.

---

## 보고서 저장 위치

```
API_Server/reports/PLAN_NN_report.md
```

---

## 보고서 표준 형식

```markdown
# PLAN_NN 결과 보고서

**PLAN**: {번호 및 이름}
**작성일**: {YYYY-MM-DD}
**상태**: PASS 완료 / FAIL 잔존

---

## 1. 개발 결과

### 생성/수정된 파일
| 파일 | 위치 | 설명 |
|------|------|------|
| workflow_service.py | app/services/ | 실행 트리거 메서드 추가 |

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
| 소요 시간 | X초 |

### 엔드포인트 검증 현황
| 메서드 | 경로 | 테스트 | 결과 |
|--------|------|--------|------|
| POST | /api/v1/workflows | test_create_workflow_happy | PASS |

---

## 3. 오류 원인 분석

> PASS 완료 시 "해당 없음" 기재

---

## 4. 개선 내용 (리팩토링)

| 파일 | 변경 전 | 변경 후 | 이유 |
|------|--------|--------|------|

---

## 5. 다음 PLAN 권고사항

- [다음 PLAN 진행 전 확인 필요한 사항]
- [의존성 또는 선행 조건]
```

---

## 수집 정보 출처

| 섹션 | 출처 |
|------|------|
| 개발 결과 | Developer Agent |
| 테스트 결과 | Tester Agent 실행 결과 |
| 오류 원인 분석 | Tester Agent FAIL 로그 |
| 개선 내용 | Refactor Agent 변경 사항 |
| 다음 PLAN 권고 | PLAN 문서 + 이번 PLAN 이슈 |

---

## 보고서 작성 완료 후

- [ ] 보고서 파일 저장 확인
- [ ] Orchestrator에 완료 보고
