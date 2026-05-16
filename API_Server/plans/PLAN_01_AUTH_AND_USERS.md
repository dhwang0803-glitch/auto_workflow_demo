# PLAN_01 — Auth + User Management (API_Server)

> **Branch**: `API_Server` · **Drafted**: 2026-04-15 · **Completed**: 2026-04-15 · **Status**: Done
>
> Stands up the FastAPI skeleton and implements **local password auth +
> email verification + JWT** only. Workflow CRUD is PLAN_02. Once this PLAN
> lands, every other endpoint PLAN can share auth through a single
> `Depends(get_current_user)`.

## 1. Goals

1. FastAPI app skeleton (`create_app()` + DI wiring + lifespan)
2. Env-var loading via Pydantic Settings
3. `auto-workflow-database` editable dependency + `UserRepository` DI injection
4. `/auth/register` → `/auth/verify` → `/auth/login` → `/auth/me` / `/auth/refresh` flow
5. bcrypt hashing, JWT issuance / verification (access token 1h, self-refresh allowed)
6. `EmailSender` ABC + `ConsoleEmailSender` (dev: log the link to stdout)
7. Real-Postgres-backed E2E tests (`DATABASE_URL` env var)

## 2. Scope

**In**
- `pyproject.toml` — fastapi, uvicorn, pydantic[email], pydantic-settings,
  pyjwt, bcrypt, httpx, pytest/pytest-asyncio, `auto-workflow-database`
- `app/config.py` — `Settings` (Pydantic BaseSettings)
- `app/main.py` — `create_app()` + lifespan (engine create / dispose)
- `app/dependencies.py` — DI providers + `get_current_user` + `get_settings`
- `app/models/auth.py` — `UserRegister`, `UserLogin` (form-only), `TokenResponse`,
  `UserResponse`, `VerifyResponse`, `MessageResponse`
- `app/services/email_sender.py` — `EmailSender` ABC + `ConsoleEmailSender`
  + `NoopEmailSender` (for test injection)
- `app/services/auth_service.py` — bcrypt hashing, JWT issuance / verification,
  register/login/verify/refresh business logic
- `app/routers/auth.py` — 7 endpoints (see §4)
- `tests/conftest.py` — real Postgres engine + httpx `AsyncClient` fixture,
  `TRUNCATE users CASCADE` between tests
- `tests/test_auth.py` — register/verify/login/me/refresh/unverified-block/
  wrong-password/expired-token/invalid-token coverage

**Out (follow-up PLANs)**
- Workflow / Executions / Webhook / Agent endpoints (PLAN_02+)
- Password-reset flow
- OAuth social login (Google / GitHub)
- Real SMTP send (`SmtpEmailSender` is a `NotImplementedError` stub only)
- RBAC / teams / orgs
- Rate limiting
- CORS detail settings (MVP is allow-all dev mode)

## 3. Security spec (to be documented as ADR-015)

| Item | Value | Rationale |
|------|-------|-----------|
| Password hash | **bcrypt** (cost=12) | OWASP-recommended, industry standard |
| JWT algorithm | **HS256** | Symmetric key; sufficient for a single service. Room to migrate to RS256 in Phase 2 |
| Access token TTL | **60 minutes** | MVP agreement |
| Verify-email token TTL | **24 hours** | Allows click latency |
| Refresh strategy | **self-refresh** | `POST /auth/refresh` exchanges a currently-valid token for a new 1h token. No separate refresh token |
| JWT `sub` | `user_id` (UUID str) | |
| JWT `purpose` | `"access"` or `"verify_email"` | Prevents token mixing |
| `password_hash` isolation | Dedicated `UserRepository.get_password_hash` | Hash bytes never leak into DTOs / responses |
| Email-verification gate | Block login (`is_verified=false` → 403) | |

## 4. Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/auth/register` | `{email, password}` → 201 + verification email sent (created with `is_verified=false`) |
| `GET`  | `/api/v1/auth/verify` | `?token=<jwt>` → 200 `{status: "verified"}`. Idempotent |
| `POST` | `/api/v1/auth/login` | OAuth2PasswordRequestForm (`username`=email, `password`) → 200 `{access_token, token_type}`. Rejects `is_verified=false` (403) |
| `GET`  | `/api/v1/auth/me` | `Bearer` verification → 200 `UserResponse` |
| `POST` | `/api/v1/auth/refresh` | `Bearer` verification → 200 new access token (fresh 1h) |
| `GET`  | `/health` | Liveness check (no DB-connection check, lightweight) |
| `GET`  | `/` | 200 `{"service": "api_server", "version": ...}` |

## 5. `create_app()` DI wiring

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

Tests can inject DB-less unit tests with
`dependency_overrides[get_user_repo] = lambda: InMemoryUserRepository()`.
This PLAN's tests use a real DB (Q4) — Postgres E2E — instead of overrides.

## 6. `EmailSender` ABC

```python
class EmailSender(ABC):
    @abstractmethod
    async def send_verification_email(self, to: str, link: str) -> None: ...

class ConsoleEmailSender(EmailSender):
    async def send_verification_email(self, to: str, link: str) -> None:
        logger.info("VERIFY EMAIL to=%s link=%s", to, link)

class NoopEmailSender(EmailSender):
    """For tests — only records whether it was called."""
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
    async def send_verification_email(self, to, link):
        self.sent.append((to, link))
```

`make_email_sender(settings) → EmailSender` picks console / smtp
(`NotImplementedError`) based on `settings.email_sender`.

## 7. Primary error mapping

| Condition | HTTP | Response |
|-----------|------|----------|
| Bad email format / password < 8 chars | 422 | Pydantic validation failure |
| Duplicate email registration | 409 | `{"detail": "email already registered"}` |
| Wrong credentials on login | 401 | `{"detail": "invalid credentials"}` |
| Login with unverified email | 403 | `{"detail": "email not verified"}` |
| Bad / expired / wrong-purpose verify token | 400 | `{"detail": "invalid verification token"}` |
| Bad / expired access token | 401 | `{"detail": "invalid or expired token"}` + `WWW-Authenticate: Bearer` |

## 8. Test coverage

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

## 9. Acceptance criteria

- [x] `pip install -e Database/ && pip install -e API_Server/` succeeds *(2026-04-15)*
- [x] All 14 tests pass *(test_auth.py, real Postgres)*
- [x] Database's 28 tests **still pass** — total 42/42 *(2026-04-15)*
- [x] `User` DTO / `TokenResponse` / `UserResponse` never expose `password_hash` anywhere — `test_me_returns_current_user_profile` explicitly checks `"password_hash" not in body`

## 10. Downstream impact

- **PLAN_02 (Workflow CRUD)** — reuses `Depends(get_current_user)`, can plug straight into `owner_id = current_user.id`
- **docs branch** — ADR-015 (full local-auth spec) lands as a separate PR from the code PR. Combine PR #16 audit findings with this PLAN as a single docs record
