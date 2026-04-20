"""GoogleSheetsAppendRowNode — ADR-019 Phase 5.

Appends one or more rows to a spreadsheet via the `values.append` API.
Uses `valueInputOption=USER_ENTERED` so formulas and date strings get
interpreted the same way the Sheets UI would — matches user expectation
for "write a log row from a workflow" use cases.

Required scope: `https://www.googleapis.com/auth/spreadsheets`.

`range` may be given with or without a sheet-name prefix. When the prefix
is omitted (e.g. `"A:Z"` rather than `"Sheet1!A:Z"`), we resolve the
spreadsheet's first sheet name dynamically — necessary because Google
names the default sheet per user locale (`Sheet1` in en-US, `시트1` in
ko-KR, `Hoja1` in es-ES, …), so hard-coding `"Sheet1!"` breaks for any
spreadsheet created by a non-English user.
"""
from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

import httpx

from src.nodes.google_workspace import GoogleWorkspaceNode
from src.nodes.registry import registry


class GoogleSheetsAppendRowNode(GoogleWorkspaceNode):
    @property
    def node_type(self) -> str:
        return "google_sheets_append_row"

    async def execute(self, input_data: dict, config: dict) -> dict:
        credential_id = UUID(config["credential_id"])
        spreadsheet_id = config["spreadsheet_id"]
        range_a1 = config["range"]  # e.g. "Sheet1!A:Z" or "A:Z"
        raw_values = config["values"]
        # Accept both a single row and a list of rows so workflows can
        # pass `[...]` (1 row) or `[[...], [...]]` (N rows) without a
        # dedicated "bulk" node variant.
        values = raw_values if (raw_values and isinstance(raw_values[0], list)) else [raw_values]

        token = await self._ensure_fresh_token(credential_id)
        timeout = config.get("timeout_seconds", 30)

        if "!" not in range_a1:
            sheet_name = await _fetch_first_sheet_name(
                spreadsheet_id, token, timeout=timeout,
            )
            range_a1 = f"{_quote_sheet_name(sheet_name)}!{range_a1}"

        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{quote(spreadsheet_id)}"
            f"/values/{quote(range_a1)}:append"
        )
        async with httpx.AsyncClient(timeout=timeout) as http:
            resp = await http.post(
                url,
                params={
                    "valueInputOption": "USER_ENTERED",
                    "insertDataOption": "INSERT_ROWS",
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                json={"values": values},
            )
            resp.raise_for_status()
            data = resp.json()
            upd = data.get("updates", {})
            return {
                "spreadsheet_id": data.get("spreadsheetId", spreadsheet_id),
                "updated_range": upd.get("updatedRange", ""),
                "updated_rows": upd.get("updatedRows", 0),
                "updated_cells": upd.get("updatedCells", 0),
            }


async def _fetch_first_sheet_name(
    spreadsheet_id: str, access_token: str, *, timeout: int
) -> str:
    """Return the title of the first sheet/tab in `spreadsheet_id`.

    `fields=sheets.properties.title` keeps the response tiny — a few
    hundred bytes vs. the megabyte-range default that includes every cell
    in the spreadsheet. First sheet is always at index 0 of the array.
    """
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{quote(spreadsheet_id)}"
    async with httpx.AsyncClient(timeout=timeout) as http:
        resp = await http.get(
            url,
            params={"fields": "sheets.properties.title"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        sheets = resp.json().get("sheets") or []
        if not sheets:
            raise RuntimeError(
                f"spreadsheet {spreadsheet_id} has no sheets — cannot resolve default"
            )
        return sheets[0]["properties"]["title"]


def _quote_sheet_name(name: str) -> str:
    """Wrap a sheet name in single-quotes when A1 notation requires it.

    Google requires quoting when the name contains spaces, punctuation,
    or non-ASCII. Single quotes inside the name are doubled per Google's
    A1 grammar. Cheap to always-quote but it clutters logs, so only quote
    when necessary.
    """
    if name.isalnum() and name.isascii():
        return name
    return "'" + name.replace("'", "''") + "'"


registry.register(GoogleSheetsAppendRowNode)
