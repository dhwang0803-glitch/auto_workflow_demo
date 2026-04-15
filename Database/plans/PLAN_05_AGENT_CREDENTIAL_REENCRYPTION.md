# PLAN_05 — Agent 공개키 기반 자격증명 재암호화 전송

> **브랜치**: `Database` · **상태**: Planned (스코프 미확정)
>
> ADR-004 는 자격증명 저장은 Fernet 대칭키, Agent 모드 전송은 Agent 공개키
> (RSA) 재암호화로 규정한다. PLAN_02 의 `FernetCredentialStore` 는 저장/
> 복호화 경로까지만 구현했고, "Agent 에게 안전하게 전달" 경로는 이 PLAN 에서
> 마무리한다. DB 스키마 변경은 거의 없을 것으로 예상 (Agent 공개키는 이미
> `agents.public_key` 에 저장됨).

## 선결 질문 (스코프 확정 전)

1. 재암호화 트리거 시점 — `Execution_Engine` 이 노드 실행 직전에 요청하는가,
   아니면 Agent 쪽에서 pull 하는가?
2. Agent 의 개인키 회전 시 기존 재암호화 페이로드는 폐기되는가?
3. 재암호화 결과 페이로드를 DB 에 캐시하는가, 매 실행마다 즉석 계산하는가?
4. RSA 키 크기 / 패딩(OAEP-SHA256?) / 라이브러리 선택(cryptography 의
   `rsa` 모듈) — 보안 결정이 ADR 수준에서 필요한가?

스코프 확정 후 본문 작성.
