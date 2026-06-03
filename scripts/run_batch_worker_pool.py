#!/usr/bin/env python3
"""Start configured batch worker pool from settings."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.database import init_db
from services import enhancement_service


STOP = False
LAST_WORKER_IDS: set[str] = set()


def _request_stop(_signum, _frame) -> None:
    global STOP
    STOP = True


def _worker_command(worker: dict) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_batch_worker.py"),
        "--worker-id",
        str(worker["id"]),
        "--sleep",
        str(worker.get("sleep_seconds") or 5),
        "--stale-minutes",
        str(worker.get("stale_minutes") or 15),
    ]
    provider_ids = [str(item).strip() for item in worker.get("provider_ids") or [] if str(item).strip()]
    if provider_ids:
        cmd.extend(["--model-provider-ids", ",".join(provider_ids)])
    model_tier = worker.get("model_tier")
    if model_tier in {"quick", "deep"}:
        cmd.extend(["--model-tier", model_tier])
    return cmd


def _enabled_workers() -> list[dict]:
    config = enhancement_service.get_worker_pool_config()
    return [worker for worker in config.get("workers", []) if worker.get("enabled")]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run configured stock-workbench batch worker pool.")
    parser.add_argument("--dry-run", action="store_true", help="print worker commands without starting them")
    parser.add_argument("--restart-delay", type=float, default=5.0, help="seconds to wait before restarting an exited worker")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    asyncio.run(init_db())
    if args.dry_run:
        workers = _enabled_workers()
        if not workers:
            print("No enabled batch workers configured. Configure them in Settings > AI engine > Worker pool.", flush=True)
            return 1
        commands = {worker["id"]: _worker_command(worker) for worker in workers}
        for worker_id, command in commands.items():
            print(worker_id + ": " + " ".join(command), flush=True)
        return 0

    processes: dict[str, subprocess.Popen] = {}
    while not STOP:
        workers = _enabled_workers()
        commands = {worker["id"]: _worker_command(worker) for worker in workers}
        worker_ids = set(commands)
        global LAST_WORKER_IDS
        if worker_ids != LAST_WORKER_IDS:
            print(
                f"enabled workers: {len(commands)}"
                + (f" ({', '.join(sorted(commands))})" if commands else ""),
                flush=True,
            )
            LAST_WORKER_IDS = worker_ids
        if not commands:
            print("No enabled batch workers configured; worker pool is idle.", flush=True)
            time.sleep(max(5.0, args.restart_delay))
            continue
        for worker_id, proc in list(processes.items()):
            if worker_id in commands:
                continue
            if proc.poll() is None:
                print(f"stopping disabled worker {worker_id}", flush=True)
                proc.terminate()
            processes.pop(worker_id, None)
        for worker_id, command in commands.items():
            proc = processes.get(worker_id)
            if proc and proc.poll() is None:
                continue
            if proc and proc.returncode is not None:
                print(
                    f"{worker_id} exited with {proc.returncode}; "
                    f"command={' '.join(command)}; restarting after {args.restart_delay}s",
                    flush=True,
                )
                time.sleep(max(1.0, args.restart_delay))
            print(f"starting {worker_id}: {' '.join(command)}", flush=True)
            processes[worker_id] = subprocess.Popen(command, cwd=str(ROOT), env=os.environ.copy())
        time.sleep(2.0)

    for proc in processes.values():
        if proc.poll() is None:
            proc.terminate()
    deadline = time.time() + 10
    for proc in processes.values():
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        if proc.poll() is None:
            proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
