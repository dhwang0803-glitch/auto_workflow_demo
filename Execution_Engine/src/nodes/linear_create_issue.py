"""LinearCreateIssueNode — Linear GraphQL `issueCreate` mutation.

Linear API 는 `Authorization: <api_key>` 형태로 Bearer prefix 를 요구하지 않는다
(lin_api_... Personal API Key). OAuth 토큰을 쓰는 경우만 Bearer 를 붙이는데,
현 credential_type `http_bearer` 는 Personal API Key 전제.
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
