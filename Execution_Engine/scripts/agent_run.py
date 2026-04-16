"""Agent daemon CLI entry point.

Usage:
    python scripts/agent_run.py \
        --server-url wss://api.example.com/api/v1/agents/ws \
        --agent-token <JWT>
"""
import argparse
import asyncio

from src.agent.main import run_agent

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-workflow Agent daemon")
    parser.add_argument("--server-url", required=True, help="WebSocket server URL")
    parser.add_argument("--agent-token", required=True, help="Agent JWT token")
    parser.add_argument("--heartbeat-interval", type=int, default=15)
    args = parser.parse_args()
    asyncio.run(run_agent(
        args.server_url,
        args.agent_token,
        heartbeat_interval=args.heartbeat_interval,
    ))
