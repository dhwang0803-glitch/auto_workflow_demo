# PLAN_04 — Approval 알림 발송 이력

> **브랜치**: `Database` · **상태**: Planned (스코프 미확정)
>
> ApprovalNode(ADR-007) 가 대기 상태로 진입할 때 누구에게 / 언제 / 어떤
> 채널로 알림이 발송됐는지 감사 추적(audit trail)을 영속화한다. 발송 채널
> 구현 자체는 `API_Server` 또는 별도 워커의 책임이며, 이 PLAN 은 "발송
> 시도/결과를 어떻게 저장할지" 만 다룬다.

## 선결 질문 (스코프 확정 전)

1. MVP 지원 채널 범위 — 이메일만? 앱 내 인박스? Slack?
2. 재시도 정책이 있을 경우 같은 (execution_id, recipient) 로 여러 행이 쌓이는가,
   아니면 `attempts` 카운터 + `last_status` 로 한 행에 압축하는가?
3. 알림 전달 실패가 Approval 상태머신에 영향을 주는가 (예: 모든 발송 실패 시
   `cancelled`)?

스코프 확정 후 본문 작성.
