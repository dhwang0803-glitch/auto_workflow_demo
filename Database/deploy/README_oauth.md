# Google OAuth2 Deploy Runbook — ADR-019 Phase 6

> ADR-019 의 구현을 실제 GCP 프로젝트에 세팅하는 작업 절차. Terraform 이 시크릿 3종 + IAM + Cloud Run env 주입을 찍어주지만, **OAuth Client ID / Secret / redirect URI 의 실값은 GCP Console 의 수동 등록 후에만 얻을 수 있다**. 본 문서는 그 수동 단계와 Secret Manager 주입을 안전하게 연결하는 절차를 다룬다.
>
> 선행 ADR: ADR-018 (Secret Manager) · ADR-019 (OAuth 설계) · ADR-020 (Cloud Run 배포). 시크릿 R/W 규칙 일반론은 [`README.md` 의 "시크릿 R/W 패턴"](README.md#시크릿-rw-패턴) 참조.

## 0. 전제

- `terraform apply` 가 최소 1회 완료되어 시크릿 placeholder 3종이 이미 생성돼 있을 것.
  - `google-oauth-client-id-<env>`
  - `google-oauth-client-secret-<env>`
  - `google-oauth-redirect-uri-<env>`
- Cloud Run 서비스 `auto-workflow-api-<env>` 가 배포돼 `/health` 200 을 반환 중일 것 (redirect URI 확정에 서비스 URL 이 필요).
- ADR-019 §7: **testing mode 유지**. verification submission 은 수요 발생 시 별도로.

## 1. GCP Console — OAuth consent screen (1회, 프로젝트 단위)

콘솔: **APIs & Services → OAuth consent screen**

1. **User Type = External**, **Publishing status = Testing** 으로 둔다. ADR-019 §7 에서 결정한 대로 verification 생략. testing mode 는 test user 목록에 등록된 계정만 동의 가능.
2. App name 은 식별 가능한 값 (`auto-workflow-<env>`), support email 은 본인 gmail.
3. **Test users** 에 시연/개발용 Google 계정을 등록. 최대 100명. prod 전환 전까지는 개발자 + 시연 대상자 정도.
4. **Scopes** 단계에서 다음을 선택 (ADR-019 §3, 최소 권한):
   - `https://www.googleapis.com/auth/gmail.send` — `gmail_send` 노드
   - `https://www.googleapis.com/auth/drive.file` — `google_drive_upload_file` 노드 (앱이 생성/업로드한 파일만 접근)
   - `https://www.googleapis.com/auth/spreadsheets` — `google_sheets_append_row` 노드
   - `https://www.googleapis.com/auth/documents` — `google_docs_append_text` 노드
   - `https://www.googleapis.com/auth/presentations` — `google_slides_create_presentation` 노드
   - `https://www.googleapis.com/auth/calendar.events` — `google_calendar_create_event` 노드

   `drive` 나 `gmail.readonly` 같은 광범위 scope 는 **의도적으로 제외** — testing mode 를 벗어나 verification 이 필요해지는 시점을 늦추기 위함.

## 2. GCP Console — OAuth 2.0 Client ID 발급

콘솔: **APIs & Services → Credentials → + CREATE CREDENTIALS → OAuth client ID**

1. **Application type = Web application**.
2. **Authorized redirect URIs** 에 Cloud Run URL 기반 callback 1개만 추가:
   ```
   https://auto-workflow-api-<env>-<hash>-an.a.run.app/api/v1/oauth/google/callback
   ```
   Cloud Run 이 발급한 `run.app` URL 을 `gcloud run services describe` 로 확인:
   ```bash
   BASE_URL=$(gcloud run services describe auto-workflow-api-<env> \
     --region=asia-northeast3 --format='value(status.url)')
   echo "${BASE_URL}/api/v1/oauth/google/callback"
   ```
   **정확한 문자열이 아니면 Google 은 `redirect_uri_mismatch` 로 거부한다** — trailing slash, 대소문자, path 까지 Cloud Run URL 과 1글자도 달라선 안 됨.

   ADR-019 §7: 커스텀 도메인 전환 시에는 이 목록에 **새 URI 를 추가** (기존 제거 X) → Frontend 트래픽 스위치 → 구 URI 제거. 동시 등록이 허용되므로 downtime 0.
3. Create 를 누르면 **Client ID** 와 **Client Secret** 이 1회 노출되는 다이얼로그가 뜬다. **닫는 순간 Client Secret 재조회 불가 → rotate 만 가능**. 아래 3단계 (Secret Manager 주입) 를 같은 터미널에서 바로 이어 진행한다.

## 3. Secret Manager 주입 — stdin pipe 필수

**규칙**: OAuth Client Secret 은 **클립보드 경유도, echo 도 금지**. Console 다이얼로그에서 값을 선택→복사→터미널 붙여넣기 하는 과정에서 복사 버퍼·스크롤백·셸 히스토리에 잔존한다. `gcloud` 의 `--data-file=-` stdin pipe 로 **눈에 보이지 않게** 주입한다.

### 3-1. Client ID (준-공개, 하지만 관례상 시크릿과 같은 파이프로)

```bash
ENV=staging    # 또는 prod
PROJECT=$(gcloud config get-value project)

# Console 다이얼로그에서 "Client ID" 를 복사 → 아래 한 줄의 인자 로 넘긴다.
# heredoc 방식이 가장 안전 (argv 에 안 남음, shell history 에만 남음 → 주의)
gcloud secrets versions add "google-oauth-client-id-${ENV}" \
  --project="$PROJECT" --data-file=- <<< "PASTE_CLIENT_ID_HERE"
```

### 3-2. Client Secret — 절대 echo/print 금지

터미널에서 **`read -s`** 로 입력을 받아 변수에만 담고 바로 파이프한다. 이 방식은 입력 중 화면 표시 0, argv 에 값이 안 실리고, 변수는 다음 `unset` 에서 즉시 파기된다.

```bash
# ❌ 나쁜 패턴 — stdout / argv / history 에 평문 잔존
echo "GOCSPX-abc123..." | gcloud secrets versions add "google-oauth-client-secret-${ENV}" --data-file=-

# ✅ 좋은 패턴 — tty 입력만 받고 바로 소비
read -rs -p "Paste Google OAuth Client Secret: " CSEC; echo
printf '%s' "$CSEC" | gcloud secrets versions add "google-oauth-client-secret-${ENV}" \
  --project="$PROJECT" --data-file=-
unset CSEC
```

입력 후 Console 다이얼로그를 닫고 **클립보드를 덮어쓰기** (다른 아무 텍스트나 복사) 할 것.

### 3-3. Redirect URI

값이 공개 URL 이지만 일관성을 위해 동일 파이프로:

```bash
BASE_URL=$(gcloud run services describe "auto-workflow-api-${ENV}" \
  --region=asia-northeast3 --project="$PROJECT" --format='value(status.url)')
REDIRECT="${BASE_URL}/api/v1/oauth/google/callback"

printf '%s' "$REDIRECT" | gcloud secrets versions add "google-oauth-redirect-uri-${ENV}" \
  --project="$PROJECT" --data-file=-
```

## 4. Cloud Run 재배포 — 새 시크릿 버전 픽업

Terraform 이 작성한 Cloud Run env 는 `secret_key_ref.version = "latest"` 이지만, **이미 실행 중인 revision 은 기동 시점의 값을 캐시한다**. 새 placeholder→실값 전환을 반영하려면 새 revision 을 찍어야 한다.

```bash
# 방법 1: 배포 파이프라인을 다시 돌린다 (release 브랜치 push)
#   .github/workflows/deploy-prod.yml 이 새 revision 배포

# 방법 2: 이미지 갱신 없이 "update" 만 쳐서 revision 강제 생성
gcloud run services update "auto-workflow-api-${ENV}" \
  --project="$PROJECT" --region=asia-northeast3 \
  --update-env-vars=_OAUTH_REFRESH=$(date +%s)
```

`_OAUTH_REFRESH` 는 애플리케이션이 무시하는 더미 키. Cloud Run 이 env 변화를 감지해 새 revision 을 띄우는 역할만 한다.

## 5. 검증

### 5-1. 시크릿이 placeholder 가 아닌지 확인

placeholder 문자열 프리픽스 (`PLACEHOLDER_GOOGLE_OAUTH_`) 가 남아있지 않은지만 1-bit 체크. **값 자체를 stdout 으로 뿌리지 않는다**.

```bash
for NAME in google-oauth-client-id google-oauth-client-secret google-oauth-redirect-uri; do
  VAL=$(gcloud secrets versions access latest \
    --secret="${NAME}-${ENV}" --project="$PROJECT")
  case "$VAL" in
    PLACEHOLDER_*) echo "$NAME = ⚠  PLACEHOLDER (주입 안 됨)";;
    *)             echo "$NAME = ✅ 실값 (길이 ${#VAL})";;
  esac
  unset VAL
done
```

### 5-2. authorize 엔드포인트 호출

API_Server 가 시크릿 3종을 제대로 로드했는지는 `/api/v1/oauth/google/authorize` 로 확인. 로그인 상태의 JWT 가 필요하므로 기존 유저 계정으로 먼저 토큰을 받는다.

```bash
TOKEN="<기존 JWT>"
curl -sS -X POST "${BASE_URL}/api/v1/oauth/google/authorize" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"credential_name":"gmail-test","scopes":["https://www.googleapis.com/auth/gmail.send"]}' | jq .
```

기대 응답:
```json
{ "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=...&scope=...&state=..." }
```

- `503` 응답이 오면 `GoogleOAuthClient` 가 `None` 이라는 뜻 → 시크릿 placeholder 또는 revision 미갱신. §4 로 돌아가 재배포.
- `authorize_url` 을 브라우저에 붙여넣고 test user 로 로그인 → consent 승인 → Cloud Run 서비스의 `/api/v1/oauth/google/callback` 으로 리다이렉션 → credential row 가 `credentials` 테이블에 생성돼야 함.

### 5-3. 노드 실행 드라이 테스트

consent 완료 후 생성된 `credential_id` 로 `gmail_send` 를 실행해 refresh gate 가 제대로 도는지 확인. Workflow JSON 예:

```json
{
  "nodes": [{
    "id": "n1", "type": "gmail_send",
    "config": {
      "credential_id": "<credential_id>",
      "to": "self@example.com", "subject": "adr-019 phase6 smoke", "body": "ok"
    }
  }],
  "connections": []
}
```

Cloud Run 로그에 `POST https://oauth2.googleapis.com/token` (refresh) + `POST gmail.googleapis.com/gmail/v1/users/me/messages/send` 가 보이면 성공.

## 6. 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| `invalid_grant` (refresh 시) | testing mode 에서 refresh_token 이 6개월 미사용으로 만료됨 / 유저가 Google 계정에서 권한 해제 | `credential.status = needs_reauth` 으로 마킹되므로 UI / API 로 재동의 유도. 해당 credential 로 `/oauth/google/authorize` 재호출 |
| `redirect_uri_mismatch` | Console 의 Authorized redirect URIs 와 Secret Manager 의 `google-oauth-redirect-uri-<env>` 값이 1글자라도 불일치 | 양쪽을 diff. 대개 trailing slash / `run.app` hash 차이. §2-2 재확인 |
| `invalid_scope` | consent screen 의 Scopes 단계에서 해당 scope 가 등록 안 됨 | §1-4 돌아가서 scope 추가 후 재저장. 이후 기존 credential 은 scope 확장이 적용 안 되므로 **재동의 필요** |
| `/authorize` 가 503 | `GOOGLE_OAUTH_CLIENT_ID` env 가 비었거나 placeholder — Settings 에서 `GoogleOAuthClient = None` | §3 시크릿 주입 + §4 revision 재배포 |
| `access_denied` (consent 화면) | test user 목록에 없는 Google 계정으로 로그인 시도 | §1-3 test users 에 해당 계정 추가 (최대 100) |
| Cloud Run 로그에 `Permission 'secretmanager.versions.access' denied` | Terraform IAM 바인딩 (`google_secret_manager_secret_iam_member.api_google_oauth_*`) 이 아직 propagate 안 됨 — 첫 apply 직후 수 분 race | `gcloud run services update` 로 revision 재생성하면 재시도 붙음. 여전하면 IAM 상태 확인 |

## 7. Rotate (Client Secret 교체)

의심이 갈 때 / 주기 rotate:

1. Console: **Credentials → 해당 Client ID → RESET SECRET** — 새 Client Secret 발급, 다이얼로그 1회 노출.
2. §3-2 방식으로 `google-oauth-client-secret-<env>` 에 새 버전 추가.
3. §4 revision 재배포.
4. Console 에서 **old secret 을 DISABLE** (Google 에 기록되는 grace period 동안 inflight 요청 보호).
5. 모니터링 후 DISABLE → DELETE.

Client ID 자체를 교체하려는 경우는 사실상 새 OAuth client 발급에 해당 → credential row 재발급 필요. 실행 중인 workflow 수명 고려해서 계획.

## 8. Teardown

시연 종료 후 환경 제거 순서:

1. API_Server revision drain (Cloud Run 트래픽 0%).
2. Secret Manager 의 OAuth 시크릿 3종은 `terraform destroy` 가 함께 제거 — 별도 `gcloud secrets delete` 불필요.
3. GCP Console → Credentials → 해당 OAuth Client ID **DELETE** (Terraform 관리 밖이라 수동). test user 목록은 함께 사라짐.
4. `terraform destroy` — 나머지 GCP 리소스 정리. destroy 소요 시간 주의는 [`README.md` 의 "Destroy 소요 시간 예산"](README.md#destroy-소요-시간-예산).

## 관련 문서

- `docs/context/decisions.md` ADR-019 §3 (scope) · §7 (testing mode) · §9 (Client secret 관리) · §10 (테스트)
- `docs/context/decisions.md` ADR-018 — Secret Manager 기반 설계
- `Database/deploy/README.md` — Cloud SQL + 일반 시크릿 R/W 규칙
- `Database/deploy/terraform/main.tf` — OAuth 시크릿 3종 + placeholder
- `Database/deploy/terraform/cloud_run.tf` — IAM accessor + `secret_key_ref` env 주입
