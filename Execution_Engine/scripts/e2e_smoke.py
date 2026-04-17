"""E2E smoke test — full stack: API_Server + Celery Worker + Postgres + Redis.

Runs a real HTTP workflow through the whole pipeline:
    register -> DB-flip is_verified -> login -> create workflow -> execute
    -> poll -> assert success + node_results shape.

Prerequisites (see memory/project_session_20260418 for the exact commands):
    - Postgres on 5435 with auto_workflow DB migrated
    - Redis on 6380
    - API_Server running on 127.0.0.1:8001 (uvicorn app.main:app)
    - Celery worker running with Execution_Engine/src on PYTHONPATH

Run:
    python Execution_Engine/scripts/e2e_smoke.py

Exit code 0 on success, 1 on failure.
"""
from __future__ import annotations

import asyncio
import sys
import time

import httpx
import psycopg

API = "http://127.0.0.1:8001"
DB_DSN = "postgresql://auto_workflow:auto_workflow@localhost:5435/auto_workflow"

EMAIL = "e2e@example.com"
PASSWORD = "smoke-correct-horse-8"


def reset_db() -> None:
    with psycopg.connect(DB_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE users CASCADE")


def verify_user_in_db(email: str) -> None:
    with psycopg.connect(DB_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET is_verified = true WHERE email = %s", (email,))


def fetch_execution_from_db(execution_id: str) -> dict:
    with psycopg.connect(DB_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, node_results, duration_ms, error FROM executions WHERE id = %s",
            (execution_id,),
        )
        row = cur.fetchone()
        return {
            "status": row[0],
            "node_results": row[1],
            "duration_ms": row[2],
            "error": row[3],
        }


async def main() -> int:
    reset_db()
    async with httpx.AsyncClient(base_url=API, timeout=30.0) as c:
        r = await c.post(
            "/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD}
        )
        assert r.status_code == 201, f"register failed: {r.status_code} {r.text}"
        print("[OK] register")

        verify_user_in_db(EMAIL)
        print("[OK] db-flip is_verified")

        r = await c.post(
            "/api/v1/auth/login",
            data={"username": EMAIL, "password": PASSWORD},
        )
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
        access = r.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {access}"
        print("[OK] login")

        # delay -> transform -> merge chain (no external deps)
        graph = {
            "nodes": [
                {"id": "n1", "type": "delay", "config": {"seconds": 0.3}},
                {
                    "id": "n2",
                    "type": "transform",
                    "config": {
                        "mapping": {
                            "status": "done",
                            "waited": "{input.waited_seconds}",
                        }
                    },
                },
                {"id": "n3", "type": "merge", "config": {}},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3"},
            ],
        }
        r = await c.post(
            "/api/v1/workflows",
            json={
                "name": "e2e-smoke",
                "settings": {"execution_mode": "serverless"},
                "graph": graph,
            },
        )
        assert r.status_code == 201, (
            f"create workflow failed: {r.status_code} {r.text}"
        )
        wf_id = r.json()["id"]
        print(f"[OK] workflow created: {wf_id}")

        r = await c.post(f"/api/v1/workflows/{wf_id}/execute")
        assert r.status_code in (200, 201, 202), (
            f"execute failed: {r.status_code} {r.text}"
        )
        ex_id = r.json()["id"]
        print(f"[OK] execution dispatched: {ex_id}")

        deadline = time.time() + 20
        final = None
        while time.time() < deadline:
            r = await c.get(f"/api/v1/executions/{ex_id}")
            if r.status_code == 200:
                status = r.json()["status"]
                if status in ("success", "failed", "cancelled"):
                    final = r.json()
                    break
            await asyncio.sleep(0.5)

        if final is None:
            print("[FAIL] execution did not terminate within 20s")
            return 1

        print(f"[INFO] final status: {final['status']}")

        if final["status"] != "success":
            print(f"[FAIL] expected success, got {final['status']}")
            print(f"[FAIL] error: {final.get('error')}")
            return 1

        # ExecutionResponse doesn't expose node_results; read DB directly.
        db_row = fetch_execution_from_db(ex_id)
        results = db_row["node_results"]
        print(f"[INFO] DB node_results: {results}")
        print(f"[INFO] duration_ms: {db_row['duration_ms']}")
        for nid in ("n1", "n2", "n3"):
            assert nid in results, f"missing result for {nid}: {results}"
        assert results["n2"]["status"] == "done", (
            f"n2 transform bad: {results['n2']}"
        )
        assert results["n1"]["waited_seconds"] == 0.3, (
            f"n1 delay bad: {results['n1']}"
        )
        print("[OK] node_results verified")

        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
