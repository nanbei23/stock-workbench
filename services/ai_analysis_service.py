"""L2 TradingAgents analysis task orchestration."""

import asyncio
import logging
import uuid
from datetime import date, datetime

from fastapi import HTTPException

from scheduler.ai_engine import get_stock_name
from scheduler.ta_bridge import PIPELINE_STAGES, run_with_snapshot
from tasks import (
    MAX_CONCURRENT,
    MAX_QUEUE,
    AnalysisTask,
    _queue,
    _tasks,
    _tasks_status,
    persist_task,
    run_with_limits,
    update_persisted_task_status,
)
from services import ai_task_service

logger = logging.getLogger(__name__)


def _active_task_for_code(code: str):
    for task in _tasks.values():
        if task.code == code and task.status in ("running", "pending"):
            return task
    return None


def _queue_counts() -> tuple[int, int, int]:
    running = sum(1 for value in _tasks_status.values() if value.get("status") == "running")
    queued = sum(1 for value in _tasks_status.values() if value.get("status") == "queued")
    pending = sum(
        1
        for task in _tasks.values()
        if task.status == "pending" and task.task_id not in _tasks_status
    )
    return running, queued, pending


def _ensure_queue_capacity(new_count: int = 1):
    running, queued, pending = _queue_counts()
    capacity = MAX_CONCURRENT + MAX_QUEUE
    current_total = running + queued + pending
    if current_total + new_count > capacity:
        raise HTTPException(
            status_code=429,
            detail=f"队列已满：运行中{running}，排队{queued}，容量{capacity}。请稍后再试。",
        )


def _new_task(
    code: str,
    *,
    depth: str = "standard",
    selected_analysts=None,
    debate_rounds=None,
    risk_rounds=None,
    name: str | None = None,
) -> AnalysisTask:
    task = AnalysisTask(
        task_id=str(uuid.uuid4())[:8],
        code=code,
        name=name or get_stock_name(code),
        status="pending",
        started_at=datetime.now().isoformat(),
        depth=depth or "standard",
        selected_analysts=selected_analysts,
        debate_rounds=debate_rounds,
        risk_rounds=risk_rounds,
    )
    _tasks[task.task_id] = task
    return task


async def _schedule_task(task: AnalysisTask, trade_date: str, resume_task_id: str | None = None):
    await persist_task(task, "pending")

    async def _wrapper(tid, c, td, resume_tid=None):
        await run_with_snapshot(tid, c, td, resume_tid)

    if resume_task_id:
        asyncio.create_task(run_with_limits(task.task_id, _wrapper, task.code, trade_date, resume_task_id))
    else:
        asyncio.create_task(run_with_limits(task.task_id, _wrapper, task.code, trade_date))


async def start_analysis(
    code: str,
    *,
    trade_date: str | None = None,
    depth: str = "standard",
    selected_analysts=None,
    debate_rounds=None,
    risk_rounds=None,
):
    active = _active_task_for_code(code)
    if active:
        return {"task_id": active.task_id, "status": "running", "message": "该股票已有分析任务在运行"}

    _ensure_queue_capacity()
    task = _new_task(
        code,
        depth=depth,
        selected_analysts=selected_analysts,
        debate_rounds=debate_rounds,
        risk_rounds=risk_rounds,
    )
    await _schedule_task(task, trade_date or date.today().isoformat())
    return {"task_id": task.task_id, "status": "pending", "message": f"已提交 {task.name} 深度分析任务"}


async def batch_analyze(
    codes: list[str],
    *,
    trade_date: str | None = None,
    depth: str = "standard",
    selected_analysts=None,
    debate_rounds=None,
    risk_rounds=None,
):
    trade_date = trade_date or date.today().isoformat()
    running, queued, pending = _queue_counts()
    current_total = running + queued + pending
    capacity = MAX_CONCURRENT + MAX_QUEUE
    new_count = len(codes)

    if current_total + new_count > capacity:
        available = max(0, capacity - current_total)
        if available == 0:
            raise HTTPException(
                status_code=429,
                detail=f"队列已满：运行中{running}，排队{queued}，容量{capacity}。请稍后再试。",
            )
        accepted_codes = codes[:available]
        rejected = codes[available:]
    else:
        accepted_codes = codes
        rejected = []

    results = []
    skipped = []
    for code in accepted_codes:
        if _active_task_for_code(code):
            skipped.append({"code": code, "reason": "已有分析任务在运行"})
            continue

        task = _new_task(
            code,
            depth=depth,
            selected_analysts=selected_analysts,
            debate_rounds=debate_rounds,
            risk_rounds=risk_rounds,
        )
        await _schedule_task(task, trade_date)
        results.append({"task_id": task.task_id, "code": code, "name": task.name})

    response = {"count": len(results), "tasks": results, "message": f"已提交{len(results)}个分析任务"}
    if skipped:
        response["skipped"] = skipped
    if rejected:
        response["rejected"] = rejected
        response["rejected_reason"] = f"队列容量不足（当前{current_total}+新增{new_count}>容量{capacity}）"
    return response


async def cancel_analysis(task_id: str):
    snapshot = await ai_task_service.get_task_snapshot(task_id)
    if not snapshot:
        raise HTTPException(404, "任务不存在")
    live_status = _tasks_status.get(task_id, {}).get("status")
    if task_id not in _tasks_status and snapshot.get("status") not in ("pending", "running"):
        raise HTTPException(400, "任务已结束，不能取消")

    if task_id in _tasks_status:
        _tasks_status[task_id]["cancel"].set()
        _tasks_status[task_id]["status"] = "cancelling"
    if live_status == "queued" and task_id in _queue:
        try:
            _queue.remove(task_id)
        except ValueError:
            pass

    if task_id in _tasks:
        _tasks[task_id].status = "failed"
        _tasks[task_id].error = "用户取消"
        _tasks[task_id].completed_at = datetime.now().isoformat()
        await persist_task(_tasks[task_id], "cancelled")
    else:
        await update_persisted_task_status(task_id, "cancelled", "用户取消", "cancelled")

    return {"status": "ok", "message": "任务已取消" if live_status == "queued" else "取消请求已发送"}


async def retry_analysis(task_id: str):
    old_task = await ai_task_service.get_task_snapshot(task_id)
    if not old_task:
        raise HTTPException(404, "任务不存在")
    if old_task.get("status") in ("pending", "running"):
        raise HTTPException(400, "任务仍在进行中，不能重试")

    code = old_task["code"]
    active = _active_task_for_code(code)
    if active:
        return {"task_id": active.task_id, "status": "running", "message": "该股票已有分析任务在运行"}

    _ensure_queue_capacity()
    task = _new_task(
        code,
        name=old_task.get("name") or get_stock_name(code),
        depth=old_task.get("depth") or "standard",
        selected_analysts=old_task.get("selected_analysts"),
        debate_rounds=old_task.get("debate_rounds"),
        risk_rounds=old_task.get("risk_rounds"),
    )
    await _schedule_task(task, date.today().isoformat())
    return {"task_id": task.task_id, "status": "pending", "message": f"已重新提交 {task.name} 分析任务"}


async def resume_analysis(task_id: str):
    old_task = await ai_task_service.get_resume_source_task(task_id)
    code = old_task["code"]

    active = _active_task_for_code(code)
    if active:
        return {"task_id": active.task_id, "status": "running", "message": "该股票已有分析任务在运行"}

    task = _new_task(
        code,
        name=old_task.get("name") or get_stock_name(code),
        depth=old_task.get("depth") or "standard",
        selected_analysts=old_task.get("selected_analysts"),
        debate_rounds=old_task.get("debate_rounds"),
        risk_rounds=old_task.get("risk_rounds"),
    )
    await _schedule_task(task, date.today().isoformat(), resume_task_id=task_id)
    return {"task_id": task.task_id, "status": "pending", "message": f"已从断点续跑 {task.name} 分析任务"}


async def trigger_l2_for_stock(code: str, trade_date: str | None = None) -> str | None:
    """Scheduler entry-point for automatic L2 triggering."""
    active = _active_task_for_code(code)
    if active:
        logger.info("L2 already queued/running for %s, skipping", code)
        return active.task_id

    running, queued, pending = _queue_counts()
    if running + queued + pending >= MAX_CONCURRENT + MAX_QUEUE:
        logger.warning("L2 queue full, skipping %s", code)
        return None

    task = _new_task(code)
    await _schedule_task(task, trade_date or date.today().isoformat())
    logger.info("Auto-triggered L2: %s(%s) task=%s", task.name, code, task.task_id)
    return task.task_id


def queue_position(task_id: str) -> int:
    return list(_queue).index(task_id) + 1 if task_id in _queue else 0


def pipeline_total() -> int:
    return len(PIPELINE_STAGES)
