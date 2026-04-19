"""GoogleSheetsAppendRowNode — ADR-019 Phase 5.

Appends one or more rows to a spreadsheet via the `values.append` API.
Uses `valueInputOption=USER_ENTERED` so formulas and date strings get
interpreted the same way the Sheets UI would — matches user expectation
for "write a log row from a workflow" use cases.

Required scope: `https://www.googleapis.com/auth/spreadsheets`.
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
        range_a1 = config["range"]  # e.g. "Sheet1!A:Z"
        raw_values = config["values"]
        # Accept both a single row and a list of rows so workflows can
        # pass `[...]` (1 row) or `[[...], [...]]` (N rows) without a
        # dedicated "bulk" node variant.
        values = raw_values if (raw_values and isinstance(raw_values[0], list)) else [raw_values]

        token = await self._ensure_fresh_token(credential_id)
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{quote(spreadsheet_id)}"
            f"/values/{quote(range_a1)}:append"
        )
        timeout = config.get("timeout_seconds", 30)
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


registry.register(GoogleSheetsAppendRowNode)
