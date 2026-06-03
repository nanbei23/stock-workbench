"""Task queue — concurrent analysis management.

Provides TaskQueue with Semaphore + deque for controlling concurrent
TradingAgents analysis jobs, plus AnalysisTask data model.
"""

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Optional

from config import DB_PATH
from repositories import analysis_task_repository as task_repository

logger = logging.getLogger(__name__)

# ── Queue configuration ──────────────────────────────────────
MAX_CONCURRENT = 2
MAX_QUEUE = 5
DEFAULT_ANALYSIS_TIMEOUT_SECONDS = 3600
DEFAULT_ANALYSIS_TIMEOUT_LABEL = "1小时"
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)
_queue = deque()
_tasks_status = {}  # task_id -> {"status": str, "cancel": asyncio.Event()}


class AnalysisCancelled(Exception):
    pass


class AnalysisTask:
    """In-memory representation of an analysis task."""

    def __init__(
        self,
        task_id: str,
        code: str,
        name: str,
        status: str = "pending",
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        elapsed: Optional[float] = None,
        result: Optional[dict] = None,
        error: Optional[str] = None,
        stages: Optional[dict] = None,
        depth: str = "standard",
        selected_analysts: Optional[list] = None,
        debate_rounds: Optional[int] = None,
        risk_rounds: Optional[int] = None,
    ):
        self.task_id = task_id
        self.code = code
        self.name = name
        self.status = status
        self.started_at = started_at
        self.completed_at = completed_at
        self.elapsed = elapsed
        self.result = result
        self.error = error
        self.stages = stages or {}
        self.depth = depth
        self.selected_analysts = selected_analysts
        self.debate_rounds = debate_rounds
        self.risk_rounds = risk_rounds
        self.token_stats = None

    # Pydantic-compatible dict helper for JSON serialisation
    def dict(self):
        return {
            "task_id": self.task_id,
            "code": self.code,
            "name": self.name,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed": self.elapsed,
            "result": self.result,
            "error": self.error,
            "stages": self.stages,
            "depth": self.depth,
            "selected_analysts": self.selected_analysts,
            "debate_rounds": self.debate_rounds,
            "risk_rounds": self.risk_rounds,
            "token_stats": self.token_stats,
        }


# Global task registry (process-scoped)
_tasks: dict[str, AnalysisTask] = {}


async def persist_task(task: AnalysisTask, queue_status: Optional[str] = None):
    """Persist the observable task snapshot without blocking the event loop."""
    try:
        await asyncio.to_thread(task_repository.persist_task_snapshot, task, queue_status, DB_PATH)
    except Exception as e:
        logger.warning("analysis task persistence failed: %s", e, exc_info=True)


async def update_persisted_task_status(
    task_id: str,
    status: str,
    error: Optional[str] = None,
    queue_status: Optional[str] = None,
):
    """Update status for tasks that may no longer have a live in-memory object."""
    try:
        await asyncio.to_thread(
            task_repository.update_task_status,
            task_id,
            status,
            error,
            DB_PATH,
            None,
            queue_status,
        )
    except Exception as e:
        logger.warning("analysis task status persistence failed: %s", e)


async def mark_interrupted_tasks():
    """Mark unfinished persisted tasks as interrupted after process restart."""
    try:
        await asyncio.to_thread(task_repository.mark_interrupted, DB_PATH)
    except Exception as e:
        logger.warning("mark interrupted analysis tasks failed: %s", e)


async def run_with_limits(task_id: str, coro_fn, *args, timeout: float = DEFAULT_ANALYSIS_TIMEOUT_SECONDS):
    """Run *coro_fn(task_id, *args)* under the global concurrency semaphore.

    Parameters
    ----------
    task_id : str
        Unique task identifier.
    coro_fn : callable
        An **async** callable that performs the actual work.  It will be
        wrapped in ``asyncio.to_thread`` only when needed; for now we call
        it directly so callers can pass either a coroutine function or a
        blocking function via ``functools.partial``.
    timeout : float
        Maximum wall-clock seconds before the task is cancelled.
    """
    if _semaphore.locked() and len(_queue) >= MAX_QUEUE:
        if task_id in _tasks:
            _tasks[task_id].status = "failed"
            _tasks[task_id].error = "分析队列已满，请稍后再试"
            _tasks[task_id].completed_at = datetime.now().isoformat()
            await persist_task(_tasks[task_id], "queue_full")
        else:
            await update_persisted_task_status(task_id, "failed", "分析队列已满，请稍后再试")
        _tasks_status[task_id] = {"status": "failed", "cancel": asyncio.Event()}
        return

    _queue.append(task_id)
    _tasks_status[task_id] = {"status": "queued", "cancel": asyncio.Event()}
    if task_id in _tasks:
        await persist_task(_tasks[task_id], "queued")

    try:
        async with _semaphore:
            if _tasks_status[task_id]["cancel"].is_set():
                _tasks_status[task_id]["status"] = "cancelled"
                if task_id in _tasks:
                    _tasks[task_id].status = "failed"
                    _tasks[task_id].error = "分析已取消"
                    _tasks[task_id].completed_at = datetime.now().isoformat()
                    await persist_task(_tasks[task_id], "cancelled")
                else:
                    await update_persisted_task_status(task_id, "cancelled", "分析已取消", "cancelled")
                return
            if task_id in _queue:
                _queue.remove(task_id)
            _tasks_status[task_id]["status"] = "running"
            if task_id in _tasks:
                _tasks[task_id].status = "running"
                await persist_task(_tasks[task_id], "running")

            # Check cancel flag
            if _tasks_status[task_id]["cancel"].is_set():
                _tasks_status[task_id]["status"] = "cancelled"
                if task_id in _tasks:
                    _tasks[task_id].status = "failed"
                    _tasks[task_id].error = "分析已取消"
                    _tasks[task_id].completed_at = datetime.now().isoformat()
                    await persist_task(_tasks[task_id], "cancelled")
                else:
                    await update_persisted_task_status(task_id, "cancelled", "分析已取消", "cancelled")
                return

            # Run with timeout
            await asyncio.wait_for(coro_fn(task_id, *args), timeout=timeout)

    except asyncio.TimeoutError:
        _tasks_status[task_id]["status"] = "timeout"
        if task_id in _tasks:
            _tasks[task_id].status = "failed"
            _tasks[task_id].error = f"分析超时（{DEFAULT_ANALYSIS_TIMEOUT_LABEL}）"
            _tasks[task_id].completed_at = datetime.now().isoformat()
            await persist_task(_tasks[task_id], "timeout")
        else:
            await update_persisted_task_status(task_id, "timeout", f"分析超时（{DEFAULT_ANALYSIS_TIMEOUT_LABEL}）")
        logger.error("分析超时: %s", task_id)
    except asyncio.CancelledError:
        _tasks_status[task_id]["status"] = "cancelled"
        if task_id in _tasks:
            _tasks[task_id].status = "failed"
            _tasks[task_id].error = "分析已取消"
            _tasks[task_id].completed_at = datetime.now().isoformat()
            await persist_task(_tasks[task_id], "cancelled")
        else:
            await update_persisted_task_status(task_id, "cancelled", "分析已取消")
    except Exception as e:
        _tasks_status[task_id]["status"] = "failed"
        if task_id in _tasks:
            _tasks[task_id].status = "failed"
            _tasks[task_id].error = str(e)
            _tasks[task_id].completed_at = datetime.now().isoformat()
            await persist_task(_tasks[task_id], "failed")
        else:
            await update_persisted_task_status(task_id, "failed", str(e))
        logger.error("run_with_limits 失败: %s", e, exc_info=True)
    else:
        # 成功完成 — 更新状态
        _tasks_status[task_id]["status"] = "completed"
        if task_id in _tasks:
            if _tasks[task_id].status not in ("completed", "failed"):
                _tasks[task_id].status = "completed"
            _tasks[task_id].completed_at = _tasks[task_id].completed_at or datetime.now().isoformat()
            await persist_task(_tasks[task_id], "completed")
        else:
            await update_persisted_task_status(task_id, "completed")
    finally:
        if task_id in _queue:
            try:
                _queue.remove(task_id)
            except ValueError:
                pass
        # 清理其他已终态的任务（保留当前刚完成的，让前端有机会读取）
        _stale = [k for k, v in _tasks_status.items()
                  if k != task_id and v.get("status") in ("completed", "failed", "timeout", "cancelled")]
        for k in _stale:
            _tasks_status.pop(k, None)
            _tasks.pop(k, None)


def queue_status() -> dict:
    """Return current queue metrics."""
    running = sum(1 for v in _tasks_status.values() if v.get("status") == "running")
    queued = sum(1 for v in _tasks_status.values() if v.get("status") == "queued")
    return {
        "max_concurrent": MAX_CONCURRENT,
        "max_queue": MAX_QUEUE,
        "running": running,
        "queued": queued,
        "available_slots": max(0, MAX_CONCURRENT - running),
    }
