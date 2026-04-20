"""ADR-019 Phase 5 — unit tests for the 6 concrete Google Workspace nodes.

All nodes share `GoogleWorkspaceNode._ensure_fresh_token` (Phase 4 tests
cover the refresh path itself). Here we assume a still-fresh access token
in the fake store so execute() takes the fast path and we can focus on:
  - correct request shape (URL, query params, headers, JSON body)
  - response parsing into the node's output dict
  - error surfacing (httpx HTTPStatusError on non-2xx)

`httpx_mock` intercepts calls made via any `httpx.AsyncClient`, which is
what each node creates fresh inside its execute() method. The refresh
client wired via configure() has its own MockTransport that would fail
if contacted (we never do — token is fresh).
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from src.nodes.gmail_send import GmailSendNode
from src.nodes.google_calendar_create_event import GoogleCalendarCreateEventNode
from src.nodes.google_docs_append_text import GoogleDocsAppendTextNode
from src.nodes.google_drive_upload_file import GoogleDriveUploadFileNode
from src.nodes.google_sheets_append_row import GoogleSheetsAppendRowNode
from src.nodes.google_slides_create_presentation import GoogleSlidesCreatePresentationNode
from src.nodes.google_workspace import GoogleWorkspaceNode
from src.services.google_oauth_client import GoogleOAuthClient
from tests.fakes import InMemoryCredentialStore


def _refuse_refresh_handler(_req):
    # Any refresh request here indicates a bug — the token should still
    # be fresh (1h ahead) so _ensure_fresh_token must take the fast path.
    raise AssertionError("unexpected token-refresh call during node test")


@pytest.fixture
async def credential_id():
    store = InMemoryCredentialStore()
    owner = uuid4()
    cid = await store.store_google_oauth(
        owner, "gm", refresh_token="rt",
        oauth_metadata={
            "access_token": "at-fresh",
            "token_expires_at": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
            "scopes": ["https://www.googleapis.com/auth/gmail.send"],
            "account_email": "u@example.com",
        },
    )
    refresh_http = httpx.AsyncClient(transport=httpx.MockTransport(_refuse_refresh_handler))
    GoogleWorkspaceNode.configure(
        credential_store=store,
        oauth_client=GoogleOAuthClient(
            client_id="c", client_secret="s", http_client=refresh_http,
        ),
        http_client=refresh_http,
    )
    yield cid
    GoogleWorkspaceNode.reset()


# ==============================================================================
# Gmail
# ==============================================================================


async def test_gmail_send_success(credential_id, httpx_mock):
    httpx_mock.add_response(
        url="https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        status_code=200,
        json={"id": "msg-1", "threadId": "thr-1", "labelIds": ["SENT"]},
    )
    result = await GmailSendNode().execute(
        {},
        {
            "credential_id": str(credential_id),
            "to": "dest@example.com",
            "subject": "hi",
            "body": "hello world",
        },
    )
    assert result == {
        "message_id": "msg-1",
        "thread_id": "thr-1",
        "label_ids": ["SENT"],
    }

    req = httpx_mock.get_request()
    assert req.headers["authorization"] == "Bearer at-fresh"
    body = json.loads(req.content)
    # raw field is base64url(RFC822) — decode and sanity check.
    decoded = base64.urlsafe_b64decode(body["raw"]).decode("utf-8")
    assert "To: dest@example.com" in decoded
    assert "Subject: hi" in decoded
    assert "hello world" in decoded


async def test_gmail_send_html_alternative(credential_id, httpx_mock):
    httpx_mock.add_response(
        url="https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        status_code=200,
        json={"id": "m", "threadId": "t", "labelIds": []},
    )
    await GmailSendNode().execute(
        {},
        {
            "credential_id": str(credential_id),
            "to": "x@example.com", "subject": "s",
            "body": "plain", "body_html": "<p>rich</p>",
        },
    )
    raw = json.loads(httpx_mock.get_request().content)["raw"]
    decoded = base64.urlsafe_b64decode(raw).decode("utf-8")
    assert "plain" in decoded
    assert "<p>rich</p>" in decoded
    assert "multipart/alternative" in decoded


async def test_gmail_send_http_error_raises(credential_id, httpx_mock):
    httpx_mock.add_response(
        url="https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        status_code=403,
        json={"error": {"message": "insufficient scope"}},
    )
    with pytest.raises(httpx.HTTPStatusError):
        await GmailSendNode().execute(
            {},
            {"credential_id": str(credential_id), "to": "x", "subject": "s", "body": "b"},
        )


# ==============================================================================
# Drive
# ==============================================================================


async def test_drive_upload_file_success(credential_id, httpx_mock):
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        status_code=200,
        json={"id": "file-1", "name": "log.txt", "mimeType": "text/plain"},
    )
    result = await GoogleDriveUploadFileNode().execute(
        {},
        {
            "credential_id": str(credential_id),
            "name": "log.txt",
            "content": "hello",
            "mime_type": "text/plain",
            "parent_folder_id": "folder-1",
        },
    )
    assert result == {"file_id": "file-1", "name": "log.txt", "mime_type": "text/plain"}

    req = httpx_mock.get_request()
    ct = req.headers["content-type"]
    assert ct.startswith("multipart/related; boundary=")
    body = req.content.decode("utf-8")
    assert '"name": "log.txt"' in body
    assert '"parents": ["folder-1"]' in body
    assert "hello" in body


async def test_drive_upload_file_no_parent(credential_id, httpx_mock):
    httpx_mock.add_response(
        url="https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        status_code=200,
        json={"id": "f", "name": "n", "mimeType": "text/plain"},
    )
    await GoogleDriveUploadFileNode().execute(
        {}, {"credential_id": str(credential_id), "name": "n", "content": "x"},
    )
    # Root-of-drive upload — omitting parent_folder_id should produce no
    # "parents" key rather than parents: [null].
    body = httpx_mock.get_request().content.decode("utf-8")
    assert "parents" not in body


# ==============================================================================
# Sheets
# ==============================================================================


async def test_sheets_append_row_single_row(credential_id, httpx_mock):
    httpx_mock.add_response(
        url="https://sheets.googleapis.com/v4/spreadsheets/ss-1/values/Sheet1%21A%3AZ:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
        status_code=200,
        json={
            "spreadsheetId": "ss-1",
            "updates": {"updatedRange": "Sheet1!A5:C5", "updatedRows": 1, "updatedCells": 3},
        },
    )
    result = await GoogleSheetsAppendRowNode().execute(
        {},
        {
            "credential_id": str(credential_id),
            "spreadsheet_id": "ss-1",
            "range": "Sheet1!A:Z",
            "values": ["a", "b", "c"],
        },
    )
    assert result["updated_rows"] == 1
    assert result["updated_cells"] == 3

    body = json.loads(httpx_mock.get_request().content)
    # Single row wrapped into [[...]] — the API always expects 2D.
    assert body["values"] == [["a", "b", "c"]]


async def test_sheets_append_row_multiple_rows(credential_id, httpx_mock):
    httpx_mock.add_response(
        url="https://sheets.googleapis.com/v4/spreadsheets/ss/values/S%211:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
        status_code=200,
        json={"spreadsheetId": "ss", "updates": {"updatedRows": 2, "updatedCells": 4}},
    )
    await GoogleSheetsAppendRowNode().execute(
        {},
        {
            "credential_id": str(credential_id),
            "spreadsheet_id": "ss",
            "range": "S!1",
            "values": [["a", "b"], ["c", "d"]],
        },
    )
    body = json.loads(httpx_mock.get_request().content)
    assert body["values"] == [["a", "b"], ["c", "d"]]


async def test_sheets_append_row_resolves_first_sheet_when_range_has_no_prefix(
    credential_id, httpx_mock
):
    # Simulates a ko-KR user's brand-new spreadsheet whose first sheet is
    # "시트1", not "Sheet1". Node must look up the actual title (needs
    # quoting because non-ASCII) and use it in the append URL.
    httpx_mock.add_response(
        method="GET",
        url="https://sheets.googleapis.com/v4/spreadsheets/ss-kr?fields=sheets.properties.title",
        status_code=200,
        json={"sheets": [{"properties": {"title": "시트1"}}]},
    )
    # Expected range after resolution: '시트1'!A:Z — quoted + url-encoded.
    httpx_mock.add_response(
        method="POST",
        url=(
            "https://sheets.googleapis.com/v4/spreadsheets/ss-kr/values/"
            "%27%EC%8B%9C%ED%8A%B81%27%21A%3AZ:append"
            "?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
        ),
        status_code=200,
        json={"spreadsheetId": "ss-kr", "updates": {"updatedRows": 1, "updatedCells": 1}},
    )
    result = await GoogleSheetsAppendRowNode().execute(
        {},
        {
            "credential_id": str(credential_id),
            "spreadsheet_id": "ss-kr",
            "range": "A:Z",
            "values": ["x"],
        },
    )
    assert result["updated_rows"] == 1


# ==============================================================================
# Docs
# ==============================================================================


async def test_docs_append_text_success(credential_id, httpx_mock):
    httpx_mock.add_response(
        url="https://docs.googleapis.com/v1/documents/doc-1:batchUpdate",
        status_code=200,
        json={"documentId": "doc-1", "replies": [{}]},
    )
    result = await GoogleDocsAppendTextNode().execute(
        {},
        {
            "credential_id": str(credential_id),
            "document_id": "doc-1",
            "text": "new paragraph\n",
        },
    )
    assert result == {"document_id": "doc-1", "replies_count": 1}

    body = json.loads(httpx_mock.get_request().content)
    assert body == {
        "requests": [
            {
                "insertText": {
                    "endOfSegmentLocation": {},
                    "text": "new paragraph\n",
                }
            }
        ]
    }


# ==============================================================================
# Slides
# ==============================================================================


async def test_slides_create_presentation_success(credential_id, httpx_mock):
    httpx_mock.add_response(
        url="https://slides.googleapis.com/v1/presentations",
        status_code=200,
        json={"presentationId": "p-1", "title": "Q2 Review", "revisionId": "rev-1"},
    )
    result = await GoogleSlidesCreatePresentationNode().execute(
        {}, {"credential_id": str(credential_id), "title": "Q2 Review"},
    )
    assert result == {
        "presentation_id": "p-1",
        "title": "Q2 Review",
        "revision_id": "rev-1",
    }

    body = json.loads(httpx_mock.get_request().content)
    assert body == {"title": "Q2 Review"}


# ==============================================================================
# Calendar
# ==============================================================================


async def test_calendar_create_event_timed(credential_id, httpx_mock):
    httpx_mock.add_response(
        url="https://www.googleapis.com/calendar/v3/calendars/primary/events",
        status_code=200,
        json={
            "id": "ev-1",
            "htmlLink": "https://calendar.google.com/event?eid=...",
            "status": "confirmed",
        },
    )
    result = await GoogleCalendarCreateEventNode().execute(
        {},
        {
            "credential_id": str(credential_id),
            "summary": "Team sync",
            "description": "weekly",
            "start_datetime": "2026-04-20T10:00:00+00:00",
            "end_datetime": "2026-04-20T11:00:00+00:00",
            "timezone": "UTC",
            "attendees": ["a@example.com", "b@example.com"],
        },
    )
    assert result["event_id"] == "ev-1"
    assert result["status"] == "confirmed"

    body = json.loads(httpx_mock.get_request().content)
    assert body["summary"] == "Team sync"
    assert body["description"] == "weekly"
    assert body["start"] == {"dateTime": "2026-04-20T10:00:00+00:00", "timeZone": "UTC"}
    assert body["end"] == {"dateTime": "2026-04-20T11:00:00+00:00", "timeZone": "UTC"}
    assert body["attendees"] == [{"email": "a@example.com"}, {"email": "b@example.com"}]


async def test_calendar_create_event_all_day(credential_id, httpx_mock):
    httpx_mock.add_response(
        url="https://www.googleapis.com/calendar/v3/calendars/c%40group.calendar.google.com/events",
        status_code=200,
        json={"id": "ev", "htmlLink": "", "status": "confirmed"},
    )
    await GoogleCalendarCreateEventNode().execute(
        {},
        {
            "credential_id": str(credential_id),
            "calendar_id": "c@group.calendar.google.com",
            "summary": "Holiday",
            "start_date": "2026-04-20",
            "end_date": "2026-04-21",
        },
    )
    body = json.loads(httpx_mock.get_request().content)
    # All-day events use {"date": "YYYY-MM-DD"} not {"dateTime": ...}.
    assert body["start"] == {"date": "2026-04-20"}
    assert body["end"] == {"date": "2026-04-21"}


# ==============================================================================
# Registry sanity — all 6 types reachable via registry.get()
# ==============================================================================


def test_all_workspace_nodes_registered():
    from src.nodes.registry import registry
    for t in (
        "gmail_send",
        "google_drive_upload_file",
        "google_sheets_append_row",
        "google_docs_append_text",
        "google_slides_create_presentation",
        "google_calendar_create_event",
    ):
        assert registry.get(t) is not None
