"""AI analysis task presentation helpers."""

from fastapi import HTTPException

from models.database import get_db
from repositories import analysis_task_repository as repo
from tasks import AnalysisTask, _queue, _tasks, _tasks_status, queue_status


def _pipeline_meta(pipeline_stages):
    return {stage["id"]: stage for stage in pipeline_stages}


def _snapshot_from_memory(task: AnalysisTask) -> dict:
    return {
        "task_id": task.task_id,
        "code": task.code,
        "name": task.name,
        "status": task.status,
        "depth": getattr(task, "depth", "standard"),
        "selected_analysts": getattr(task, "selected_analysts", None),
        "debate_rounds": getattr(task, "debate_rounds", None),
        "risk_rounds": getattr(task, "risk_rounds", None),
        "stages": task.stages or {},
        "result": task.result,
        "error": task.error,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "elapsed": task.elapsed,
        "token_stats": getattr(task, "token_stats", None),
    }


async def _db_task_snapshot(task_id: str):
    db = await get_db()
    try:
        return await repo.get_task(db, task_id)
    finally:
        await db.close()


async def get_task_snapshot(task_id: str):
    task = _tasks.get(task_id)
    if task:
        return _snapshot_from_memory(task)
    return await _db_task_snapshot(task_id)


def _completed_count(stages: dict) -> int:
    return sum(1 for stage in stages.values() if stage.get("status") == "completed")


def format_task_status(snapshot: dict, pipeline_stages: list[dict]) -> dict:
    stages = snapshot.get("stages") or {}
    meta = _pipeline_meta(pipeline_stages)
    result = {
        "task_id": snapshot["task_id"],
        "code": snapshot.get("code"),
        "name": snapshot.get("name"),
        "status": snapshot.get("status"),
        "progress": f"{_completed_count(stages)}/{len(pipeline_stages)}",
        "elapsed": snapshot.get("elapsed"),
        "stages": {},
    }
    for sid, stage in stages.items():
        stage_meta = meta.get(sid, {})
        result["stages"][sid] = {
            "status": stage.get("status", "pending"),
            "name": stage_meta.get("name", sid),
            "icon": stage_meta.get("icon", ""),
        }
    if snapshot.get("status") == "failed" and snapshot.get("error"):
        result["error"] = snapshot["error"]
    if snapshot.get("queue_status"):
        result["queue_status"] = snapshot["queue_status"]
    return result


async def get_analysis_status(task_id: str, pipeline_stages: list[dict]) -> dict:
    snapshot = await get_task_snapshot(task_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="任务不存在")
    return format_task_status(snapshot, pipeline_stages)


def format_task_result(snapshot: dict) -> dict:
    status = snapshot.get("status")
    if status == "failed":
        return {
            "status": "failed",
            "error": snapshot.get("error") or "未知错误",
            "task_id": snapshot["task_id"],
        }
    if status != "completed":
        return {
            "status": status,
            "message": "分析尚未完成",
            "task_id": snapshot["task_id"],
        }
    return {
        "task_id": snapshot["task_id"],
        "code": snapshot.get("code"),
        "name": snapshot.get("name"),
        "status": status,
        "elapsed": snapshot.get("elapsed"),
        "result": snapshot.get("result"),
    }


async def get_analysis_result(task_id: str) -> dict:
    snapshot = await get_task_snapshot(task_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="任务不存在")
    return format_task_result(snapshot)


def format_active_task(snapshot: dict, pipeline_stages: list[dict]) -> dict:
    stages = snapshot.get("stages") or {}
    return {
        "task_id": snapshot["task_id"],
        "code": snapshot.get("code"),
        "name": snapshot.get("name"),
        "status": snapshot.get("status"),
        "progress": f"{_completed_count(stages)}/{len(pipeline_stages)}",
        "stages": {
            sid: stage.get("status", "pending")
            for sid, stage in stages.items()
        },
        "depth": snapshot.get("depth") or "standard",
        "selected_analysts": snapshot.get("selected_analysts"),
        "debate_rounds": snapshot.get("debate_rounds"),
        "risk_rounds": snapshot.get("risk_rounds"),
    }


async def get_active_task(pipeline_stages: list[dict]) -> dict:
    for task in _tasks.values():
        if task.status in ("running", "pending"):
            return format_active_task(_snapshot_from_memory(task), pipeline_stages)

    db = await get_db()
    try:
        snapshot = await repo.get_latest_active_task(db)
    finally:
        await db.close()
    if not snapshot:
        return {"task_id": None}
    return format_active_task(snapshot, pipeline_stages)


async def get_resume_source_task(task_id: str):
    snapshot = await get_task_snapshot(task_id)
    if not snapshot:
        raise HTTPException(404, "任务不存在")
    if snapshot.get("status") != "failed":
        raise HTTPException(400, f"任务状态为 {snapshot.get('status')}，只能续跑失败的任务")
    return snapshot


def format_task_list_item(snapshot: dict, pipeline_stages: list[dict]) -> dict:
    stages = snapshot.get("stages") or {}
    task_id = snapshot["task_id"]
    live = _tasks_status.get(task_id, {})
    queue_state = live.get("status") or snapshot.get("queue_status")
    item = {
        "task_id": task_id,
        "code": snapshot.get("code"),
        "name": snapshot.get("name"),
        "status": snapshot.get("status"),
        "queue_status": queue_state,
        "progress": f"{_completed_count(stages)}/{len(pipeline_stages)}",
        "queue_position": list(_queue).index(task_id) + 1 if task_id in _queue else 0,
        "error": snapshot.get("error"),
        "depth": snapshot.get("depth") or "standard",
        "started_at": snapshot.get("started_at"),
        "completed_at": snapshot.get("completed_at"),
        "updated_at": snapshot.get("updated_at"),
        "can_cancel": queue_state in ("queued", "running") or snapshot.get("status") in ("pending", "running"),
        "can_retry": snapshot.get("status") in ("failed", "timeout", "cancelled"),
    }
    return item


async def list_tasks(pipeline_stages: list[dict], limit: int = 50, status: str | None = None) -> dict:
    db = await get_db()
    try:
        snapshots = await repo.list_tasks(db, limit=limit, status=status)
    finally:
        await db.close()

    by_id = {snapshot["task_id"]: snapshot for snapshot in snapshots}
    for task in _tasks.values():
        if status and task.status != status and _tasks_status.get(task.task_id, {}).get("status") != status:
            continue
        by_id[task.task_id] = {**by_id.get(task.task_id, {}), **_snapshot_from_memory(task)}

    tasks = [
        format_task_list_item(snapshot, pipeline_stages)
        for snapshot in sorted(
            by_id.values(),
            key=lambda item: item.get("updated_at") or item.get("started_at") or "",
            reverse=True,
        )
    ][:limit]
    return {"count": len(tasks), "queue": queue_status(), "tasks": tasks}
