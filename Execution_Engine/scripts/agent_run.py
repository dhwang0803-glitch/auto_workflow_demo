"""Agent daemon CLI entry point.

Usage:
    python scripts/agent_run.py \
        --server-url wss://api.example.com/api/v1/agents/ws \
        --agent-token <JWT> \
        --agent-private-key /path/to/agent_rsa_private.pem
"""
import argparse
import asyncio

from src.agent.main import run_agent

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-workflow Agent daemon")
    parser.add_argument("--server-url", required=True, help="WebSocket server URL")
    parser.add_argument("--agent-token", required=True, help="Agent JWT token")
    parser.add_argument("--heartbeat-interval", type=int, default=15)
    parser.add_argument(
        "--agent-private-key", default=None,
        help="Path to Agent RSA private key PEM. Omit only if no workflow "
             "uses credential_ref — refs without a key available fail cleanly.",
    )
    args = parser.parse_args()

    private_key_pem: bytes | None = None
    if args.agent_private_key:
        with open(args.agent_private_key, "rb") as f:
            private_key_pem = f.read()

    asyncio.run(run_agent(
        args.server_url,
        args.agent_token,
        heartbeat_interval=args.heartbeat_interval,
        agent_private_key_pem=private_key_pem,
    ))
