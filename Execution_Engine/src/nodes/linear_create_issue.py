"""LinearCreateIssueNode — Linear GraphQL `issueCreate` mutation.

The Linear API does not require a Bearer prefix — `Authorization: <api_key>` form
(lin_api_... Personal API Key). Only OAuth tokens take the Bearer prefix; the current
credential_type `http_bearer` assumes a Personal API Key.
"""
from __future__ import annotations

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


_MUTATION = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier url }
  }
}
""".strip()


class LinearCreateIssueNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "linear_create_issue"

    async def execute(self, input_data: dict, config: dict) -> dict:
        api_token = config["api_token"]
        input_vars: dict = {
            "teamId": config["team_id"],
            "title": config["title"],
        }
        if "description" in config:
            input_vars["description"] = config["description"]

        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.linear.app/graphql",
                headers={
                    "Authorization": api_token,
                    "Content-Type": "application/json",
                },
                json={"query": _MUTATION, "variables": {"input": input_vars}},
            )
            resp.raise_for_status()
            data = resp.json()
            payload = data["data"]["issueCreate"]
            issue = payload["issue"]
            return {
                "success": payload["success"],
                "issue_id": issue["id"],
                "identifier": issue["identifier"],
                "url": issue["url"],
            }


registry.register(LinearCreateIssueNode)
