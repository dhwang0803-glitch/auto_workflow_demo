# PLAN_05 — Agent 자격증명 하이브리드 재암호화 전송

> **브랜치**: `Database` · **작성일**: 2026-04-15 · **완료일**: 2026-04-15 · **상태**: Done
>
> ADR-004 의 "Agent 모드 전송 시 Agent 공개키(RSA) 로 재암호화" 를 실제 사양
> 으로 구현한다. 알고리즘/파라미터/프레임 스키마는 **ADR-013** 에서 이미
> 고정됨 (AES-256-GCM + RSA-OAEP-SHA256 하이브리드, RSA-2048). 본 PLAN 은
> 그 사양을 `CredentialStore` ABC 에 얹고 Postgres/InMemory 양쪽에 구현한다.
>
> 선결 질문 Q1~Q4 는 ADR-013 및 architecture.md §2 Agent 모드 섹션에
> 반영 완료.

## 1. 목표

1. `CredentialStore.retrieve_for_agent()` 신설 — Fernet 복호 → 하이브리드
   재암호화 → `AgentCredentialPayload` 반환
2. 공통 암호 헬퍼 `src/crypto/hybrid.py` — `hybrid_encrypt` / `hybrid_decrypt`.
   InMemory 더블과 Postgres 구현이 같은 함수를 공유
3. DB 스키마 변경 없음 — `agents.public_key` 는 PLAN_02 에서 이미 존재
4. 통합 테스트: 실제 RSA 키쌍으로 round-trip 검증 (암호 → 복호 → 원문 일치)

## 2. 범위

**In**
- `AgentCredentialPayload` DTO (`wrapped_key`, `nonce`, `ciphertext`)
- `CredentialStore.retrieve_for_agent` ABC + Postgres 구현 + InMemory 구현
- `src/crypto/hybrid.py` — pyca/cryptography 기반 헬퍼
- 통합 테스트: `tests/test_agent_reencryption.py` (round-trip + 변조 감지 + 잘못된 키 거부)

**Out (후속/타 브랜치)**
- **Agent 공개키 조회** — `retrieve_for_agent` 는 공개키 PEM 을 **인자로 받음**.
  `agents.public_key` 를 DB 에서 꺼내오는 책임은 호출자(API_Server) 가 진다.
  PLAN_05 는 `AgentRepository` 에 의존하지 않는다
- **WebSocket `get_credential` 프레임 처리** — API_Server / Execution_Engine 책임
- **Agent 측 복호 구현** — Agent 브랜치 (본 구현과 같은 스펙 사용)
- **API_Server 인프로세스 캐시** — 후속 PLAN. 단, `retrieve_for_agent` 가
  순수 함수 형태로 남도록 설계해 향후 데코레이터 래핑이 가능하게 함
- **DB 스키마 변경** — 없음

## 3. 암호 사양 (ADR-013 요약)

| 항목 | 값 |
|------|-----|
| 대칭층 | AES-256-GCM (12B nonce, 16B tag) |
| RSA 키 크기 | 2048 bit, e=65537 |
| RSA 패딩 | OAEP, hash=SHA-256, MGF1=SHA-256, label=없음 |
| 라이브러리 | `cryptography` (pyca) — 이미 Fernet 에서 사용 중 |
| 매 호출마다 | 새 random AES 키 + 새 nonce (재사용 금지) |

### 3.1 프레임 구조

```python
@dataclass
class AgentCredentialPayload:
    wrapped_key: bytes   # 256 B (RSA-2048 고정)
    nonce: bytes         #  12 B
    ciphertext: bytes    # len(plaintext) + 16 B (GCM tag)
```

직렬화는 WebSocket 레이어 책임 (base64 JSON). DTO 자체는 raw bytes.

### 3.2 `hybrid_encrypt(plaintext: bytes, agent_public_key_pem: bytes)`

```
1. os.urandom(32)               → AES-256 key
2. os.urandom(12)                → GCM nonce
3. AESGCM(key).encrypt(nonce, plaintext, None)  → ciphertext (tag 포함)
4. load_pem_public_key(pem)
5. public_key.encrypt(
       key,
       padding.OAEP(MGF1=SHA256, algorithm=SHA256, label=None)
   )                             → wrapped_key
6. return AgentCredentialPayload(wrapped_key, nonce, ciphertext)
```

### 3.3 `hybrid_decrypt(payload, agent_private_key_pem)` — 테스트 전용

Agent 측 복호 흐름을 테스트 더블로 재현. 프로덕션 서버 코드는 이 함수를
호출하지 않음 (개인키는 Agent 프로세스에만 존재).

## 4. Repository 변경

### 4.1 `base.py` 추가

```python
@dataclass
class AgentCredentialPayload:
    wrapped_key: bytes
    nonce: bytes
    ciphertext: bytes


class CredentialStore(ABC):
    # ... 기존 store/retrieve/delete ...

    @abstractmethod
    async def retrieve_for_agent(
        self,
        credential_id: UUID,
        *,
        agent_public_key_pem: bytes,
    ) -> AgentCredentialPayload: ...
```

`agent_public_key_pem` 은 PEM 인코딩된 RSA 공개키 바이트. 호출자가
`AgentRepository` 등에서 조회해 전달한다.

### 4.2 `FernetCredentialStore.retrieve_for_agent`

```python
async def retrieve_for_agent(
    self, credential_id, *, agent_public_key_pem
) -> AgentCredentialPayload:
    plaintext_dict = await self.retrieve(credential_id)  # Fernet 복호
    plaintext_bytes = json.dumps(plaintext_dict).encode("utf-8")
    return hybrid_encrypt(plaintext_bytes, agent_public_key_pem)
```

- DB/캐시 변경 없음
- Fernet 복호는 기존 `retrieve()` 를 그대로 호출 → 관리 표면 단일화
- 순수 함수 형태 (인자만으로 결과 결정) → 향후 인프로세스 캐시 래핑 가능

### 4.3 `InMemoryCredentialStore.retrieve_for_agent`

동일한 `hybrid_encrypt` 를 호출. `InMemoryCredentialStore` 는 Fernet 을
쓰지 않지만 **하이브리드 암호화 경로는 실제 `cryptography` 호출** 하여
프레임 스키마/알고리즘 회귀를 테스트에서 잡을 수 있게 한다.

## 5. 산출물

| 경로 | 내용 |
|------|------|
| `src/crypto/__init__.py` | 빈 패키지 마커 |
| `src/crypto/hybrid.py` | `hybrid_encrypt` / `hybrid_decrypt` |
| `src/repositories/base.py` | `AgentCredentialPayload` + ABC 시그니처 확장 |
| `src/repositories/credential_store.py` | `FernetCredentialStore.retrieve_for_agent` |
| `tests/fakes.py` | `InMemoryCredentialStore.retrieve_for_agent` |
| `tests/test_agent_reencryption.py` | round-trip / 변조 감지 / 잘못된 키 거부 |

## 6. 수용 기준

- [x] `hybrid_encrypt` → `hybrid_decrypt` round-trip 이 원문을 복원 *(test_hybrid_roundtrip_restores_plaintext)*
- [x] `FernetCredentialStore.retrieve_for_agent` 가 저장 → 재암호화 →
      Agent 개인키 복호 경로로 원문 dict 복원 *(test_fernet_store_retrieve_for_agent_roundtrip)*
- [x] `InMemoryCredentialStore.retrieve_for_agent` 가 동일 스펙으로 동작 *(test_inmemory_retrieve_for_agent_roundtrip)*
- [x] ciphertext 1바이트 변조 시 복호가 예외 *(test_hybrid_tampered_ciphertext_rejected)*
- [x] 다른 키쌍의 개인키로 복호 시 예외 *(test_hybrid_wrong_private_key_rejected)*
- [x] `wrapped_key` 길이 = 256 B (RSA-2048 고정 검증) *(roundtrip assertion)*
- [x] 2KB 대용량 페이로드 (OAEP 단일 블록 한도 초과) 처리 *(test_hybrid_large_payload_over_oaep_block_limit)*
- [x] non-RSA 공개키 거부 *(test_hybrid_rejects_non_rsa_public_key)*
- [x] 기존 20개 테스트가 통과 상태 유지 — 전체 24/24 통과 *(2026-04-15)*

## 7. 오픈 이슈

1. **공개키 PEM 포맷 검증** — 호출자가 잘못된 PEM 을 넘기면
   `load_pem_public_key` 가 ValueError 를 던짐. 이걸 래핑해 도메인 예외로
   올릴지는 API_Server 의 에러 계약 설계에서 결정. 본 PLAN 은 그대로 전파.
2. **RSA 키 교체 시 동시 실행 중 워크플로우** — Agent 가 재접속하며 새 공개키
   를 등록하면 그 이후 `get_credential` 은 새 키로 암호화됨. 기존 실행 중인
   노드는 이전 키로 받은 payload 를 그대로 복호 (문제없음). PLAN_05 구현상
   특별 처리 불요.
3. **API_Server 인프로세스 캐시 도입 시점** — 실측 QPS > 100 도달 시. 후속
   PLAN 에서 결정.

## 8. 후속 PLAN 영향

- **API_Server** — `get_credential` WebSocket 핸들러 추가. `AgentRepository`
  로 공개키 조회 → `CredentialStore.retrieve_for_agent` 호출 → 응답 프레임
  구성
- **Execution_Engine / Agent** — 본 사양대로 복호 코드 작성. ADR-013 의
  프레임 스키마 준수
