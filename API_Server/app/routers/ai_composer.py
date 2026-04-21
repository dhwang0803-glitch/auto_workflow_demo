"""AI Composer router (PLAN_02 §4).

Two paths behind the same POST:

- `stream=false` (default) — wait for the full LLM reply, validate into
  `ComposeResult`, return `ComposeResponse` JSON. Consumed by the Frontend's
  `composeJSON()` (PR C).
- `stream=true` — SSE stream of `rationale_delta` events while the model
  narrates, then a terminal `result` or `error` frame. Consumed by the
  Frontend's `composeSSE()` (PR D).

The stream branch emits failures as in-band `event: error` frames (with the
same status the non-stream endpoint would return, carried in the payload)
rather than HTTP error codes, because the response headers are flushed as
soon as the stream opens. Pre-stream failures (auth, parse) still raise and
surface as normal HTTP errors.
"""
from __future__ import annotations

import json
from typing import AsyncIterator
from uuid import uuid4

from auto_workflow_database.repositories.base import User
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.dependencies import get_ai_composer_service, get_current_user
from app.models.ai_composer import ComposeRequest, ComposeResponse
from app.services.ai_composer_service import (
    AIComposerService,
    RationaleDelta,
    Result,
    StreamError,
)

router = APIRouter()


def _sse(event: str, data: dict) -> bytes:
    # One SSE frame. `data:` must be on its own line; `\n\n` terminates.
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


@router.post("/compose", response_model=None)
async def compose(
    payload: ComposeRequest,
    stream: bool = Query(default=False),
    user: User = Depends(get_current_user),
    service: AIComposerService = Depends(get_ai_composer_service),
):
    current_dag = (
        payload.current_dag.model_dump() if payload.current_dag else None
    )
    session_id = payload.session_id or uuid4()

    if stream:

        async def _iter() -> AsyncIterator[bytes]:
            # Open with a session frame so the client can correlate this
            # stream with a session_id before any model tokens arrive.
            yield _sse("session", {"session_id": str(session_id)})
            async for ev in service.compose_stream(
                user_id=user.id,
                message=payload.message,
                current_dag=current_dag,
            ):
                if isinstance(ev, RationaleDelta):
                    yield _sse("rationale_delta", {"token": ev.token})
                elif isinstance(ev, Result):
                    yield _sse(
                        "result",
                        {
                            "session_id": str(session_id),
                            "result": ev.payload.model_dump(mode="json"),
                        },
                    )
                elif isinstance(ev, StreamError):
                    yield _sse(
                        "error", {"code": ev.code, "message": ev.message}
                    )

        return StreamingResponse(
            _iter(),
            media_type="text/event-stream",
            headers={
                # Proxies (Cloud Run, nginx) will otherwise buffer responses
                # and delay deltas until the whole stream closes.
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    result = await service.compose(
        user_id=user.id,
        message=payload.message,
        current_dag=current_dag,
    )
    return ComposeResponse(session_id=session_id, result=result)
