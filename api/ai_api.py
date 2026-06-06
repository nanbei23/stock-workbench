"""
AI分析台 API — Phase 4
三层架构：L1规则引擎(实时) + L2 TradingAgents(深度) + L3 gbrain(知识)
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from scheduler.ta_bridge import (
    PIPELINE_STAGES,
)
from scheduler.gbrain_client import api_search as gbrain_api_search, api_save as gbrain_api_save
from tasks import (
    _tasks,
    _tasks_status,
    _queue,
    queue_status,
)
from services import ai_analysis_service
from services import ai_task_service
from services import ai_report_service
from services import ai_fact_service
from services import ai_signal_service
from services import auth_service
from schemas.ai_task import (
    ActiveTaskResponse,
    AnalysisResultResponse,
    AnalysisStartRequest,
    AnalysisStartResponse,
    AnalysisStatusResponse,
    BatchAnalyzeRequest,
    BatchAnalyzeResponse,
    AnalysisTaskListResponse,
    GbrainSaveRequest,
    QueueStatusResponse,
    TaskActionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])

# ============================================================
# 异动日志
# ============================================================

_anomaly_log: list[dict] = []

# ============================================================
# L1 规则引擎 — 实时建议
# ============================================================

@router.get("/ai/suggestions")
async def get_suggestions():
    """L1实时建议总览"""
    return await ai_signal_service.get_suggestions()


# ============================================================
# L2 TradingAgents 深度分析
# ============================================================

@router.post("/ai/analyze/{code}", response_model=AnalysisStartResponse)
async def start_analysis(code: str, req: Optional[AnalysisStartRequest] = None):
    """触发L2 TradingAgents深度分析（带队列和并发控制）"""
    req = req or AnalysisStartRequest()
    return await ai_analysis_service.start_analysis(
        code=code,
        trade_date=req.trade_date,
        depth=req.depth or "standard",
        selected_analysts=req.selected_analysts,
        debate_rounds=req.debate_rounds,
        risk_rounds=req.risk_rounds,
    )


@router.get("/ai/analyze/{task_id}/status", response_model=AnalysisStatusResponse)
async def get_analysis_status(task_id: str):
    """查询分析进度"""
    return await ai_task_service.get_analysis_status(task_id, PIPELINE_STAGES)


@router.get("/ai/analyze/{task_id}/result", response_model=AnalysisResultResponse)
async def get_analysis_result(task_id: str):
    """获取分析结果"""
    return await ai_task_service.get_analysis_result(task_id)


@router.get("/ai/analyze/{task_id}/stream")
async def stream_analysis(task_id: str):
    """SSE实时推送分析进度"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_generator():
        last_completed = set()

        try:
            while True:
                if task.status == "completed":
                    yield f"data: {json.dumps({'type': 'completed', 'result': task.result, 'elapsed': task.elapsed}, ensure_ascii=False)}\n\n"
                    break
                elif task.status == "failed":
                    yield f"data: {json.dumps({'type': 'failed', 'error': task.error}, ensure_ascii=False)}\n\n"
                    break
                elif task.status == "pending" and task_id in _tasks_status and _tasks_status[task_id].get("status") == "queued":
                    yield f"data: {json.dumps({'type': 'queued', 'position': list(_queue).index(task_id) + 1 if task_id in _queue else 0}, ensure_ascii=False)}\n\n"

                for stage in PIPELINE_STAGES:
                    sid = stage["id"]
                    if sid not in last_completed and task.stages.get(sid, {}).get("status") == "completed":
                        last_completed.add(sid)
                        yield f"data: {json.dumps({'type': 'stage_completed', 'stage': sid, 'name': stage['name'], 'icon': stage['icon'], 'report': task.stages[sid]['report']}, ensure_ascii=False)}\n\n"

                completed = sum(1 for s in task.stages.values() if s["status"] == "completed")
                elapsed = time.time() - time.mktime(datetime.fromisoformat(task.started_at).timetuple()) if task.started_at else 0
                # 使用真实token统计（如果有），否则显示0
                ts = task.token_stats or {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}
                yield f"data: {json.dumps({'type': 'progress', 'completed': completed, 'total': len(PIPELINE_STAGES), 'elapsed': round(elapsed, 1), 'token_stats': ts}, ensure_ascii=False)}\n\n"

                await asyncio.sleep(1)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass  # 客户端断开，正常退出
        except Exception as e:
            logger.warning("SSE stream error: %s", e)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/ai/batch-analyze", response_model=BatchAnalyzeResponse)
async def batch_analyze(req: BatchAnalyzeRequest):
    """批量触发L2分析（带队列限制）"""
    return await ai_analysis_service.batch_analyze(
        codes=req.codes,
        trade_date=req.trade_date,
        depth=req.depth or "standard",
        selected_analysts=req.selected_analysts,
        debate_rounds=req.debate_rounds,
        risk_rounds=req.risk_rounds,
    )


# ============================================================
# 取消分析 + 队列状态
# ============================================================

@router.post("/ai/analyze/{task_id}/cancel", response_model=TaskActionResponse)
async def cancel_analysis(task_id: str):
    """取消一个运行中或排队中的分析任务"""
    return await ai_analysis_service.cancel_analysis(task_id)


@router.post("/ai/analyze/{task_id}/resume", response_model=TaskActionResponse)
async def resume_analysis(task_id: str):
    """从断点续跑一个失败的分析任务"""
    return await ai_analysis_service.resume_analysis(task_id)


@router.get("/ai/tasks", response_model=AnalysisTaskListResponse)
async def list_ai_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    status: Optional[str] = Query(None),
):
    """AI任务中心：历史任务、队列位置、失败原因和可操作状态"""
    return await ai_task_service.list_tasks(PIPELINE_STAGES, limit=limit, status=status)


@router.post("/ai/tasks/{task_id}/retry", response_model=TaskActionResponse)
async def retry_ai_task(task_id: str):
    """重试一个已结束的AI任务"""
    return await ai_analysis_service.retry_analysis(task_id)


@router.post("/ai/tasks/{task_id}/cancel", response_model=TaskActionResponse)
async def cancel_ai_task(task_id: str):
    """从任务中心取消排队中或运行中的任务"""
    return await ai_analysis_service.cancel_analysis(task_id)


@router.get("/ai/queue/status", response_model=QueueStatusResponse)
async def get_queue_status():
    """获取任务队列状态"""
    return queue_status()


@router.get("/ai/active-task", response_model=ActiveTaskResponse)
async def get_active_task():
    """获取当前运行中的任务（页面刷新后恢复用）"""
    return await ai_task_service.get_active_task(PIPELINE_STAGES)


@router.get("/ai/reports")
async def list_reports(
    code: Optional[str] = None,
    signal: Optional[str] = None,
    depth: Optional[str] = None,
    model_mode: Optional[str] = None,
    limit: int = Query(default=20, le=500),
    user: dict = Depends(auth_service.require_login_user),
):
    """历史分析报告列表"""
    return await ai_report_service.list_reports(
        code=code,
        signal=signal,
        depth=depth,
        model_mode=model_mode,
        limit=limit,
        login_user_id=user.get("id") or "admin",
    )


@router.get("/ai/reports/{report_id}")
async def get_report(report_id: int, user: dict = Depends(auth_service.require_login_user)):
    """单份报告详情"""
    return await ai_report_service.get_report(report_id, login_user_id=user.get("id") or "admin")


# ============================================================
# 异动日志
# ============================================================

@router.get("/ai/anomalies")
async def get_anomalies(limit: int = Query(default=50, le=200), code: Optional[str] = Query(None)):
    """获取异动日志，支持 ?code=XXXXXX 按个股筛选"""
    return await ai_report_service.get_anomalies(limit=limit, code=code, memory_log=_anomaly_log)


@router.post("/ai/trigger")
async def trigger_l1_analysis():
    """手动触发一次L1分析"""
    return await ai_signal_service.trigger_all(_anomaly_log)


@router.post("/ai/trigger/{code}")
async def trigger_l1_for_stock(code: str):
    """手动触发单只股票的L1异动检测"""
    return await ai_signal_service.trigger_stock(code, _anomaly_log)


# ============================================================
# gbrain 集成 (L3)
# ============================================================

@router.get("/ai/gbrain/search")
async def gbrain_search(q: str = Query(..., min_length=1)):
    """搜索gbrain知识库"""
    return await gbrain_api_search(q)


@router.post("/ai/gbrain/save")
async def gbrain_save(req: GbrainSaveRequest):
    """存入gbrain知识库"""
    return await gbrain_api_save(req.slug, req.title, req.content)


# ============================================================
# Phase 6: 事实账本 + 报告复合验证
# ============================================================

@router.get("/ai/reports/{report_id}/fact-check")
async def fact_check_report(report_id: int):
    """获取事实账本：优先返回七层数据核对结果，否则回退到旧版正则比对"""
    return await ai_fact_service.get_fact_check(report_id)


@router.post("/ai/reports/{report_id}/recheck")
async def recheck_report(report_id: int):
    """重新核对事实账本：读取报告 → 调用七层工具获取数据 → 旁观者模型核对"""
    return await ai_fact_service.recheck_report(report_id)


@router.post("/ai/reports/{report_id}/bystander-verify")
async def bystander_verify(report_id: int):
    """旁观者模型核对报告"""
    return await ai_fact_service.bystander_verify(report_id)


@router.get("/ai/report-quality")
async def report_quality(limit: int = Query(default=50, ge=1, le=200)):
    """报告质量复盘：事实核对、幻觉项与信号后验收益"""
    return await ai_report_service.get_quality_summary(limit=limit)
