# PLAN_01 — Auth + User Management (API_Server)

> **브랜치**: `API_Server` · **작성일**: 2026-04-15 · **완료일**: 2026-04-15 · **상태**: Done
>
> FastAPI 골격을 세우고 **로컬 패스워드 인증 + 이메일 검증 + JWT** 만을
> 구현한다. Workflow CRUD 는 PLAN_02. 이 PLAN 이 끝나면 다른 모든 엔드포인트
> PLAN 이 `Depends(get_current_user)` 하나로 인증을 공유할 수 있게 된다.

## 1. 목표

1. FastAPI 앱 골격 (`create_app()` + DI 조립 + lifespan)
2. Pydantic Settings 기반 환경변수 로딩
3. `auto-workflow-database` editable 의존 + `UserRepository` DI 주입
4. `/auth/register` → `/auth/verify` → `/auth/login` → `/auth/me` / `/auth/refresh` 플로우
5. bcrypt 해싱, JWT 발급/검증 (access token 1h, self-refresh 가능)
6. `EmailSender` ABC + `ConsoleEmailSender` (dev 용, 링크 로그 출력)
7. 실제 Postgres 기반 E2E 테스트 (`DATABASE_URL` 환경변수)

## 2. 범위

**In**
- `pyproject.toml` — fastapi, uvicorn, pydantic[email], pydantic-settings,
  pyjwt, bcrypt, httpx, pytest/pytest-asyncio, `auto-workflow-database`
- `app/config.py` — `Settings` (Pydantic BaseSettings)
- `app/main.py` — `create_app()` + lifespan (엔진 생성/해제)
- `app/dependencies.py` — DI 프로바이더 + `get_current_user` + `get_settings`
- `app/models/auth.py` — `UserRegister`, `UserLogin` (폼 전용), `TokenResponse`,
  `UserResponse`, `VerifyResponse`, `MessageResponse`
- `app/services/email_sender.py` — `EmailSender` ABC + `ConsoleEmailSender`
  + `NoopEmailSender` (테스트 주입용)
- `app/services/auth_service.py` — bcrypt 해싱, JWT 발급/검증,
  register/login/verify/refresh 비즈니스 로직
- `app/routers/auth.py` — 7개 엔드포인트 (아래 §4)
- `tests/conftest.py` — 실제 Postgres 엔진 + httpx `AsyncClient` fixture,
  매 테스트마다 `TRUNCATE users CASCADE`
- `tests/test_auth.py` — register/verify/login/me/refresh/unverified-block/
  wrong-password/expired-token/invalid-token 커버리지

**Out (후속 PLAN)**
- Workflow / Executions / Webhook / Agent 엔드포인트 (PLAN_02+)
- 비밀번호 리셋 플로우
- OAuth 소셜 로그인 (Google/GitHub)
- 실제 SMTP 발송 (`SmtpEmailSender` 는 `NotImplementedError` 스텁만)
- RBAC / 팀 / 조직
- Rate limiting
- CORS 세부 설정 (MVP 는 allow-all dev 모드)

## 3. 보안 사양 (ADR-015 로 문서화 예정)

| 항목 | 값 | 근거 |
|------|----|------|
| 패스워드 해시 | **bcrypt** (cost=12) | OWASP 권고, 산업 표준 |
| JWT 알고리즘 | **HS256** | 대칭키, 단일 서비스에 충분. Phase 2 에서 RS256 으로 이전 여지 |
| Access token TTL | **60분** | MVP 합의 |
| Verify email token TTL | **24시간** | 클릭 지연 허용 |
| Refresh 전략 | **self-refresh** | `POST /auth/refresh` 가 현재 유효 토큰을 새 1h 토큰으로 교환. 별도 refresh token 없음 |
| JWT `sub` | `user_id` (UUID str) | |
| JWT `purpose` | `"access"` 또는 `"verify_email"` | 토큰 혼용 방지 |
| `password_hash` 격리 | DB `UserRepository.get_password_hash` 전용 | hash 바이트가 DTO/응답에 절대 노출되지 않음 |
| 이메일 검증 게이트 | 로그인 차단 (`is_verified=false` → 403) | |

## 4. 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/v1/auth/register` | `{email, password}` → 201 + 검증 메일 발송 (`is_verified=false` 상태로 생성) |
| `GET`  | `/api/v1/auth/verify` | `?token=<jwt>` → 200 `{status: "verified"}`. 멱등 |
| `POST` | `/api/v1/auth/login` | OAuth2PasswordRequestForm (`username`=email, `password`) → 200 `{access_token, token_type}`. `is_verified=false` 거부 (403) |
| `GET`  | `/api/v1/auth/me` | `Bearer` 검증 → 200 `UserResponse` |
| `POST` | `/api/v1/auth/refresh` | `Bearer` 검증 → 200 새 access token (새 1h) |
| `GET`  | `/health` | 라이브니스 체크 (DB 연결 확인 없음, 가볍게) |
| `GET`  | `/` | 200 `{"service": "api_server", "version": ...}` |

## 5. `create_app()` DI 조립

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings()
    app = FastAPI(title="auto_workflow API")

    @app.on_event("startup")
    async def _startup():
        app.state.engine = build_engine(s.database_url)
        app.state.sessionmaker = build_sessionmaker(app.state.engine)
        app.state.user_repo = PostgresUserRepository(app.state.sessionmaker)
        app.state.email_sender = make_email_sender(s)
        app.state.auth_service = AuthService(
            user_repo=app.state.user_repo,
            email_sender=app.state.email_sender,
            settings=s,
        )

    @app.on_event("shutdown")
    async def _shutdown():
        await app.state.engine.dispose()

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    return app
```

테스트는 `dependency_overrides[get_user_repo] = lambda: InMemoryUserRepository()`
로 DB 없는 단위 테스트 주입 가능. 본 PLAN 테스트는 Postgres E2E (Q4) 이므로
override 대신 실제 DB 를 사용.

## 6. `EmailSender` ABC

```python
class EmailSender(ABC):
    @abstractmethod
    async def send_verification_email(self, to: str, link: str) -> None: ...

class ConsoleEmailSender(EmailSender):
    async def send_verification_email(self, to: str, link: str) -> None:
        logger.info("VERIFY EMAIL to=%s link=%s", to, link)

class NoopEmailSender(EmailSender):
    """테스트용 — 호출 여부만 기록."""
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
    async def send_verification_email(self, to, link):
        self.sent.append((to, link))
```

`make_email_sender(settings) → EmailSender` 가 `settings.email_sender` 값에
따라 console / smtp (NotImplementedError) 선택.

## 7. 주요 에러 매핑

| 상황 | HTTP | 응답 |
|------|------|------|
| 이메일 형식 불량 / 비밀번호 8자 미만 | 422 | Pydantic 검증 실패 |
| 이메일 중복 등록 | 409 | `{"detail": "email already registered"}` |
| 로그인 잘못된 자격증명 | 401 | `{"detail": "invalid credentials"}` |
| 로그인 미검증 이메일 | 403 | `{"detail": "email not verified"}` |
| Verify 토큰 불량/만료/purpose 불일치 | 400 | `{"detail": "invalid verification token"}` |
| Access 토큰 불량/만료 | 401 | `{"detail": "invalid or expired token"}` + `WWW-Authenticate: Bearer` |

## 8. 테스트 커버리지

- `test_register_creates_unverified_user_and_sends_email`
- `test_register_duplicate_email_rejected`
- `test_register_weak_password_rejected`
- `test_verify_flips_is_verified`
- `test_verify_idempotent`
- `test_verify_invalid_token_rejected`
- `test_verify_wrong_purpose_rejected`
- `test_login_blocked_when_unverified`
- `test_login_success_returns_access_token`
- `test_login_wrong_password_rejected`
- `test_me_returns_current_user_profile`
- `test_me_missing_auth_header_rejected`
- `test_refresh_returns_new_token_with_fresh_expiry`
- `test_expired_access_token_rejected`

## 9. 수용 기준

- [x] `pip install -e Database/ && pip install -e API_Server/` 성공 *(2026-04-15)*
- [x] 14개 테스트 전부 통과 *(test_auth.py, real Postgres)*
- [x] Database 의 28개 테스트는 **여전히 통과** — 합계 42/42 *(2026-04-15)*
- [x] `User` DTO / `TokenResponse` / `UserResponse` 어디에도 `password_hash` 노출 없음 — `test_me_returns_current_user_profile` 가 `"password_hash" not in body` 를 명시 검증

## 10. 후속 영향

- **PLAN_02 (Workflow CRUD)** — `Depends(get_current_user)` 재사용, `owner_id = current_user.id` 로 바로 접속 가능
- **docs 브랜치** — ADR-015 (로컬 auth 사양 전체) 코드 PR 과 별도 PR 로 작성. PR #16 감사 결과와 본 PLAN 을 묶어 한번에 기록
