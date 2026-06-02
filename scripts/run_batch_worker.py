#!/usr/bin/env python3
"""Run long batch research jobs outside the web process."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.database import init_db
from services import batch_report_service


_STOP = False


def _request_stop(_signum, _frame) -> None:
    global _STOP
    _STOP = True


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run stock-workbench batch jobs as an independent worker.")
    parser.add_argument("--once", action="store_true", help="run at most one pending job and exit")
    parser.add_argument("--sleep", type=float, default=5.0, help="seconds to sleep when no job is available")
    parser.add_argument("--worker-id", default=f"batch-worker-{os.getpid()}", help="worker id shown in job runtime state")
    parser.add_argument("--stale-minutes", type=int, default=15, help="mark running jobs stale after this heartbeat gap")
    parser.add_argument("--model-provider-ids", default="", help="comma-separated model provider ids for this worker pool")
    parser.add_argument("--model-tier", choices=["quick", "deep"], default=None, help="force quick/deep model tier for this worker")
    args = parser.parse_args()
    provider_ids = [item.strip() for item in args.model_provider_ids.split(",") if item.strip()]

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    await init_db()

    while not _STOP:
        result = await batch_report_service.run_worker_once(
            worker_id=args.worker_id,
            stale_minutes=args.stale_minutes,
            model_provider_ids=provider_ids,
            model_tier=args.model_tier,
        )
        print(result, flush=True)
        if args.once:
            break
        if not result.get("ran"):
            await asyncio.sleep(max(1.0, args.sleep))


if __name__ == "__main__":
    asyncio.run(main())
