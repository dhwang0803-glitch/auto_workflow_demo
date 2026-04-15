"""Roll-forward month partitions for `execution_node_logs` — PLAN_03.

Ensures that the next N months (inclusive of the current month) have
partitions. Idempotent via `CREATE TABLE IF NOT EXISTS` — safe to run from
cron on any cadence.

Usage:
    python Database/scripts/roll_partitions.py                # --months 6 default
    python Database/scripts/roll_partitions.py --months 12    # wider horizon
    python Database/scripts/roll_partitions.py --dry-run      # print only

Env: reuses `DATABASE_URL_SYNC` / `DATABASE_URL` fallback from migrate.py.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import psycopg

DEFAULT_MONTHS = 6
PARENT_TABLE = "execution_node_logs"


def _dsn() -> str:
    dsn = os.getenv("DATABASE_URL_SYNC")
    if dsn:
        return dsn
    async_dsn = os.getenv("DATABASE_URL")
    if not async_dsn:
        sys.exit("DATABASE_URL_SYNC or DATABASE_URL must be set")
    return async_dsn.replace("+asyncpg", "").replace("+psycopg", "")


def _add_months(d: date, n: int) -> date:
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _partition_specs(months: int) -> list[tuple[str, date, date]]:
    first = date.today().replace(day=1)
    specs: list[tuple[str, date, date]] = []
    for i in range(months):
        start = _add_months(first, i)
        end = _add_months(start, 1)
        name = f"{PARENT_TABLE}_{start:%Y_%m}"
        specs.append((name, start, end))
    return specs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=DEFAULT_MONTHS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    specs = _partition_specs(args.months)
    if args.dry_run:
        for name, start, end in specs:
            print(f"would ensure {name} [{start} .. {end})")
        return

    with psycopg.connect(_dsn(), autocommit=False) as conn, conn.cursor() as cur:
        for name, start, end in specs:
            cur.execute(
                f'CREATE TABLE IF NOT EXISTS "{name}" '
                f'PARTITION OF {PARENT_TABLE} '
                f"FOR VALUES FROM (%s) TO (%s)",
                (start, end),
            )
            print(f"ensured {name} [{start} .. {end})")
        conn.commit()


if __name__ == "__main__":
    main()
