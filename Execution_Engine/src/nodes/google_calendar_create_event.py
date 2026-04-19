"""GoogleCalendarCreateEventNode — ADR-019 Phase 5.

Creates a single event on a Google Calendar. Supports both timed events
(`start_datetime`/`end_datetime`, RFC3339) and all-day events when the
caller passes date-only strings via `start_date`/`end_date`.

Required scope: `https://www.googleapis.com/auth/calendar.events`.
"""
from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

import httpx

from src.nodes.google_workspace import GoogleWorkspaceNode
from src.nodes.registry import registry


class GoogleCalendarCreateEventNode(GoogleWorkspaceNode):
    @property
    def node_type(self) -> str:
        return "google_calendar_create_event"

    async def execute(self, input_data: dict, config: dict) -> dict:
        credential_id = UUID(config["credential_id"])
        calendar_id = config.get("calendar_id", "primary")
        summary = config["summary"]
        description = config.get("description")
        location = config.get("location")
        attendees = config.get("attendees")  # list of emails
        timezone = config.get("timezone", "UTC")

        body: dict = {"summary": summary}
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": e} for e in attendees]

        # Timed vs all-day: Calendar API wants either dateTime+timeZone
        # or date (YYYY-MM-DD). The workflow author picks by which config
        # pair they provide.
        if "start_datetime" in config:
            body["start"] = {
                "dateTime": config["start_datetime"],
                "timeZone": timezone,
            }
            body["end"] = {
                "dateTime": config["end_datetime"],
                "timeZone": timezone,
            }
        else:
            body["start"] = {"date": config["start_date"]}
            body["end"] = {"date": config["end_date"]}

        token = await self._ensure_fresh_token(credential_id)
        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id)}/events"
        )
        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "event_id": data["id"],
                "html_link": data.get("htmlLink", ""),
                "status": data.get("status", ""),
            }


registry.register(GoogleCalendarCreateEventNode)
