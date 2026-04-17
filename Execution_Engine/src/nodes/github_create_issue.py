"""GitHubCreateIssueNode — GitHub REST API 이슈 생성.

api_token 은 classic PAT 또는 fine-grained token. http_bearer 주입 전제.
X-GitHub-Api-Version 은 안정 API 계약 고정용.
"""
from __future__ import annotations

import httpx

from src.nodes.base import BaseNode
from src.nodes.registry import registry


class GitHubCreateIssueNode(BaseNode):
    @property
    def node_type(self) -> str:
        return "github_create_issue"

    async def execute(self, input_data: dict, config: dict) -> dict:
        api_token = config["api_token"]
        owner = config["owner"]
        repo = config["repo"]
        body: dict = {"title": config["title"]}
        if "body" in config:
            body["body"] = config["body"]
        if "labels" in config:
            body["labels"] = config["labels"]
        if "assignees" in config:
            body["assignees"] = config["assignees"]

        timeout = config.get("timeout_seconds", 30)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/issues",
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "issue_id": data["id"],
                "number": data["number"],
                "url": data.get("html_url", ""),
                "state": data.get("state", "open"),
            }


registry.register(GitHubCreateIssueNode)
