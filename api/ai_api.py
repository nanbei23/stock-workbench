"""
AI分析台 API — Phase 4
三层架构：L1规则引擎(实时) + L2 TradingAgents(深度) + L3 gbrain(知识)
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.responses import JSONResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ── Extracted modules ────────────────────────────────────────
from scheduler.ai_engine import (
    get_watchlist_and_portfolio,
    get_quote,
    get_index_quotes,
    evaluate_suggestion,
    get_stock_name,
    ANOMALY_THRESHOLDS,
)
from scheduler.ta_bridge import (
    PIPELINE_STAGES,
    run_trading_agents,
    run_with_snapshot,
    trigger_l2_for_stock,
)
from scheduler.gbrain_client import api_search as gbrain_api_search, api_save as gbrain_api_save
from tasks import (
    MAX_CONCURRENT,
    MAX_QUEUE,
    AnalysisTask,
    _tasks,
    _tasks_status,
    _queue,
    run_with_limits,
    queue_status,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])

# ============================================================
# 数据模型
# ============================================================

class AnalyzeRequest(BaseModel):
    code: str
    trade_date: Optional[str] = None  # 默认今天

class BatchAnalyzeRequest(BaseModel):
    codes: list[str]
    trade_date: Optional[str] = None
    mode: Optional[str] = "economy"
    depth: Optional[str] = "standard"
    selected_analysts: Optional[list] = None
    debate_rounds: Optional[int] = None
    risk_rounds: Optional[int] = None


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
    stocks = get_watchlist_and_portfolio()

    loop = asyncio.get_event_loop()
    quotes = await asyncio.gather(
        *[loop.run_in_executor(None, get_quote, s["code"]) for s in stocks]
    )

    indices = await loop.run_in_executor(None, get_index_quotes)

    northbound = {}
    try:
        from data.signal import get_northbound
        nb_data = await loop.run_in_executor(None, get_northbound)
        if nb_data:
            sh_net = nb_data.get("sh_net", 0) or 0
            sz_net = nb_data.get("sz_net", 0) or 0
            total = sh_net + sz_net
            northbound = {
                "sh_connect": round(sh_net, 2),
                "sz_connect": round(sz_net, 2),
                "total": round(total, 2),
                "direction": "net_in" if total >= 0 else "net_out",
            }
    except Exception as e:
        logger.warning("获取北向资金失败: %s", e)

    suggestions = []
    anomalies = []
    for stock, quote in zip(stocks, quotes):
        if quote:
            sug = evaluate_suggestion(stock, quote)
            suggestions.append(sug)
            if sug.get("anomaly"):
                anomalies.append({
                    **sug["anomaly"],
                    "code": sug["code"],
                    "name": sug["name"],
                    "price": sug["price"],
                    "change_pct": sug["change_pct"],
                    "time": datetime.now().strftime("%H:%M"),
                })

    return {
        "suggestions": suggestions,
        "indices": indices,
        "northbound": northbound,
        "anomalies": anomalies,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ============================================================
# L2 TradingAgents 深度分析
# ============================================================

@router.post("/ai/analyze/{code}")
async def start_analysis(code: str, request: Request):
    """触发L2 TradingAgents深度分析（带队列和并发控制）"""
    trade_date = date.today().isoformat()
    depth = "standard"
    selected_analysts = None
    debate_rounds = None
    risk_rounds = None
    try:
        body = await request.json()
        trade_date = body.get("trade_date") or trade_date
        depth = body.get("depth", "standard")
        selected_analysts = body.get("selected_analysts")
        debate_rounds = body.get("debate_rounds")
        risk_rounds = body.get("risk_rounds")
    except Exception:
        pass

    for task in _tasks.values():
        if task.code == code and task.status in ("running", "pending"):
            return {"task_id": task.task_id, "status": "running", "message": "该股票已有分析任务在运行"}

    task_id = str(uuid.uuid4())[:8]
    name = get_stock_name(code)

    task = AnalysisTask(
        task_id=task_id,
        code=code,
        name=name,
        status="pending",
        started_at=datetime.now().isoformat(),
    )
    task.depth = depth
    task.selected_analysts = selected_analysts
    task.debate_rounds = debate_rounds
    task.risk_rounds = risk_rounds
    _tasks[task_id] = task

    async def _wrapper(tid, c, td):
        await run_with_snapshot(tid, c, td)

    asyncio.create_task(run_with_limits(task_id, _wrapper, code, trade_date))

    return {"task_id": task_id, "status": "pending", "message": f"已提交 {name} 深度分析任务"}


@router.get("/ai/analyze/{task_id}/status")
async def get_analysis_status(task_id: str):
    """查询分析进度"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    completed = sum(1 for s in task.stages.values() if s["status"] == "completed")
    total = len(PIPELINE_STAGES)

    result = {
        "task_id": task_id,
        "code": task.code,
        "name": task.name,
        "status": task.status,
        "progress": f"{completed}/{total}",
        "elapsed": task.elapsed,
        "stages": {
            sid: {
                "status": s["status"],
                "name": next(st["name"] for st in PIPELINE_STAGES if st["id"] == sid),
                "icon": next(st["icon"] for st in PIPELINE_STAGES if st["id"] == sid),
            }
            for sid, s in task.stages.items()
        },
    }
    if task.status == "failed" and task.error:
        result["error"] = task.error
    return result


@router.get("/ai/analyze/{task_id}/result")
async def get_analysis_result(task_id: str):
    """获取分析结果"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status == "failed":
        return {"status": "failed", "error": task.error or "未知错误", "task_id": task_id}

    if task.status != "completed":
        return {"status": task.status, "message": "分析尚未完成"}

    return {
        "task_id": task_id,
        "code": task.code,
        "name": task.name,
        "status": task.status,
        "elapsed": task.elapsed,
        "result": task.result,
    }


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


@router.post("/ai/batch-analyze")
async def batch_analyze(req: BatchAnalyzeRequest):
    """批量触发L2分析（带队列限制）"""
    trade_date = req.trade_date or date.today().isoformat()

    running = sum(1 for v in _tasks_status.values() if v.get("status") == "running")
    queued = sum(1 for v in _tasks_status.values() if v.get("status") == "queued")
    pending = sum(1 for t in _tasks.values() if t.status == "pending" and t.task_id not in _tasks_status)
    current_total = running + queued + pending
    capacity = MAX_CONCURRENT + MAX_QUEUE
    new_count = len(req.codes)

    if current_total + new_count > capacity:
        available = max(0, capacity - current_total)
        if available == 0:
            raise HTTPException(
                status_code=429,
                detail=f"队列已满：运行中{running}，排队{queued}，容量{capacity}。请稍后再试。",
            )
        accepted_codes = req.codes[:available]
        rejected = req.codes[available:]
    else:
        accepted_codes = req.codes
        rejected = []

    results = []
    skipped = []
    for code in accepted_codes:
        already_running = False
        for t in _tasks.values():
            if t.code == code and t.status in ("running", "pending"):
                skipped.append({"code": code, "reason": "已有分析任务在运行"})
                already_running = True
                break
        if already_running:
            continue

        task_id = str(uuid.uuid4())[:8]
        name = get_stock_name(code)

        task = AnalysisTask(
            task_id=task_id,
            code=code,
            name=name,
            status="pending",
            started_at=datetime.now().isoformat(),
            depth=req.depth or "standard",
            selected_analysts=req.selected_analysts,
            debate_rounds=req.debate_rounds,
            risk_rounds=req.risk_rounds,
        )
        _tasks[task_id] = task

        async def _wrapper(tid, c, td):
            await run_with_snapshot(tid, c, td)

        asyncio.create_task(run_with_limits(task_id, _wrapper, code, trade_date))

        results.append({"task_id": task_id, "code": code, "name": name})

    response = {"count": len(results), "tasks": results, "message": f"已提交{len(results)}个分析任务"}
    if skipped:
        response["skipped"] = skipped
    if rejected:
        response["rejected"] = rejected
        response["rejected_reason"] = f"队列容量不足（当前{current_total}+新增{new_count}>容量{capacity}）"

    return response


# ============================================================
# 取消分析 + 队列状态
# ============================================================

@router.post("/ai/analyze/{task_id}/cancel")
async def cancel_analysis(task_id: str):
    """取消一个运行中或排队中的分析任务"""
    if task_id not in _tasks_status:
        raise HTTPException(404, "任务不存在或已完成")

    _tasks_status[task_id]["cancel"].set()
    _tasks_status[task_id]["status"] = "cancelling"

    if task_id in _tasks:
        _tasks[task_id].status = "failed"
        _tasks[task_id].error = "用户取消"
        _tasks[task_id].completed_at = datetime.now().isoformat()

    return {"status": "ok", "message": "取消请求已发送"}


@router.post("/ai/analyze/{task_id}/resume")
async def resume_analysis(task_id: str):
    """从断点续跑一个失败的分析任务"""
    old_task = _tasks.get(task_id)
    if not old_task:
        raise HTTPException(404, "任务不存在")
    if old_task.status != "failed":
        raise HTTPException(400, f"任务状态为 {old_task.status}，只能续跑失败的任务")

    code = old_task.code
    # 检查是否已有运行中的任务
    for t in _tasks.values():
        if t.code == code and t.status in ("running", "pending"):
            return {"task_id": t.task_id, "status": "running", "message": "该股票已有分析任务在运行"}

    new_task_id = str(uuid.uuid4())[:8]
    name = old_task.name or get_stock_name(code)
    trade_date = date.today().isoformat()

    task = AnalysisTask(
        task_id=new_task_id,
        code=code,
        name=name,
        status="pending",
        started_at=datetime.now().isoformat(),
    )
    task.depth = getattr(old_task, 'depth', 'standard') or 'standard'
    task.selected_analysts = getattr(old_task, 'selected_analysts', None)
    task.debate_rounds = getattr(old_task, 'debate_rounds', None)
    task.risk_rounds = getattr(old_task, 'risk_rounds', None)
    _tasks[new_task_id] = task

    async def _wrapper(tid, c, td, resume_tid):
        await run_with_snapshot(tid, c, td, resume_tid)

    asyncio.create_task(run_with_limits(new_task_id, _wrapper, code, trade_date, task_id))

    return {"task_id": new_task_id, "status": "pending", "message": f"已从断点续跑 {name} 分析任务"}


@router.get("/ai/queue/status")
async def get_queue_status():
    """获取任务队列状态"""
    return queue_status()


@router.get("/ai/active-task")
async def get_active_task():
    """获取当前运行中的任务（页面刷新后恢复用）"""
    for tid, task in _tasks.items():
        if task.status in ("running", "pending"):
            completed = sum(1 for s in task.stages.values() if s["status"] == "completed")
            # 返回每个阶段的完成状态，前端精确恢复
            stages = {}
            for name, info in task.stages.items():
                stages[name] = info.get("status", "pending")
            return {
                "task_id": tid,
                "code": task.code,
                "name": task.name,
                "status": task.status,
                "progress": f"{completed}/{len(PIPELINE_STAGES)}",
                "stages": stages,
                "depth": getattr(task, 'depth', 'standard'),
                "selected_analysts": getattr(task, 'selected_analysts', None),
                "debate_rounds": getattr(task, 'debate_rounds', None),
                "risk_rounds": getattr(task, 'risk_rounds', None),
            }
    return {"task_id": None}


# ============================================================
# 历史报告
# ============================================================

def _get_db():
    """获取数据库连接"""
    import sqlite3
    from pathlib import Path
    db_path = Path(__file__).parent.parent / "data" / "workbench.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/ai/reports")
async def list_reports(
    code: Optional[str] = None,
    signal: Optional[str] = None,
    limit: int = Query(default=20, le=100),
):
    """历史分析报告列表"""
    db = _get_db()
    try:
        query = "SELECT id, task_id, code, signal, confidence, risk_score, duration_seconds, created_at, depth, model_mode FROM analysis_reports WHERE 1=1"
        params = []

        if code:
            query += " AND code = ?"
            params.append(code)
        if signal:
            query += " AND signal = ?"
            params.append(signal)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = db.execute(query, params).fetchall()
        # 预加载 watchlist 名称映射
        name_map = {}
        try:
            for w in db.execute("SELECT code, name FROM watchlist").fetchall():
                name_map[w["code"]] = w["name"]
        except Exception:
            pass
        reports = []
        for r in rows:
            d = dict(r)
            raw = d.get("raw_state")
            name = ""
            if raw:
                try:
                    rs = json.loads(raw)
                    name = rs.get("name", "")
                except Exception:
                    pass
            if not name:
                name = name_map.get(d.get("code", ""), "")
            d["name"] = name
            reports.append(d)
        return {
            "count": len(reports),
            "reports": reports,
        }
    finally:
        db.close()


@router.get("/ai/reports/{report_id}")
async def get_report(report_id: int):
    """单份报告详情"""
    db = _get_db()
    try:
        row = db.execute("SELECT * FROM analysis_reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="报告不存在")

        report = dict(row)
        # 补充股票名称
        if not report.get("name"):
            raw = report.get("raw_state")
            if raw:
                try:
                    rs = json.loads(raw)
                    report["name"] = rs.get("name", "")
                except Exception:
                    pass
        if not report.get("name"):
            try:
                w = db.execute("SELECT name FROM watchlist WHERE code=?", (report.get("code",""),)).fetchone()
                if w: report["name"] = w["name"]
            except Exception:
                pass
        if report.get("raw_state"):
            report["result"] = json.loads(report["raw_state"])
        
        # 解析JSON字段（DB中存为TEXT）
        for json_key in ["risk_debate", "investment_debate"]:
            if report.get(json_key):
                try:
                    report[json_key] = json.loads(report[json_key])
                except Exception:
                    pass
        
        # 使用分析时快照的事实账本 + 自动生成的报告复核（均预存，无时间衰减）
        if report.get("fact_check"):
            try:
                report["_fact_check"] = json.loads(report["fact_check"])
            except Exception:
                pass
        if report.get("bystander_verify"):
            try:
                report["_bystander_verify"] = json.loads(report["bystander_verify"])
            except Exception:
                pass
        
        return report
    finally:
        db.close()


# ============================================================
# 异动日志
# ============================================================

@router.get("/ai/anomalies")
async def get_anomalies(limit: int = Query(default=50, le=200), code: Optional[str] = Query(None)):
    """获取异动日志，支持 ?code=XXXXXX 按个股筛选"""
    from config import DB_PATH
    try:
        db = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row
        today = datetime.now().strftime("%Y-%m-%d")
        if code:
            code6 = code[:6]
            rows = db.execute(
                """SELECT code, name, anomaly_type, description, severity, created_at
                   FROM anomaly_logs
                   WHERE date(created_at) = ? AND code LIKE ?
                   ORDER BY created_at DESC LIMIT ?""",
                (today, f"%{code6}%", limit)
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT code, name, anomaly_type, description, severity, created_at
                   FROM anomaly_logs
                   WHERE date(created_at) = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (today, limit)
            ).fetchall()
        db.close()
        anomalies = [
            {
                "code": r["code"],
                "name": r["name"] or r["code"],
                "anomaly_type": r["anomaly_type"],
                "message": r["description"] or "",
                "level": r["severity"] or "info",
                "time": r["created_at"],
                "change_pct": 0,
                "price": 0,
            }
            for r in rows
        ]
        # 如果DB没数据，回退到内存列表
        if not anomalies:
            mem = _anomaly_log[-limit:]
            if code:
                code6 = code[:6]
                mem = [a for a in mem if a.get("code", "").startswith(code6)]
            return {"count": len(mem), "anomalies": mem}
        return {"count": len(anomalies), "anomalies": anomalies}
    except Exception:
        mem = _anomaly_log[-limit:]
        if code:
            code6 = code[:6]
            mem = [a for a in mem if a.get("code", "").startswith(code6)]
        return {"count": len(mem), "anomalies": mem}


@router.post("/ai/trigger")
async def trigger_l1_analysis():
    """手动触发一次L1分析"""
    stocks = get_watchlist_and_portfolio()

    loop = asyncio.get_event_loop()
    quotes = await asyncio.gather(
        *[loop.run_in_executor(None, get_quote, s["code"]) for s in stocks]
    )

    anomalies = []
    for stock, quote in zip(stocks, quotes):
        if quote:
            sug = evaluate_suggestion(stock, quote)
            if sug.get("anomaly"):
                anomaly = {
                    **sug["anomaly"],
                    "code": sug["code"],
                    "name": sug["name"],
                    "price": sug["price"],
                    "change_pct": sug["change_pct"],
                    "time": datetime.now().strftime("%H:%M"),
                    "l1_advice": sug["advice"],
                }
                anomalies.append(anomaly)
                _anomaly_log.append(anomaly)

    return {
        "checked": len(stocks),
        "anomalies": anomalies,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.post("/ai/trigger/{code}")
async def trigger_l1_for_stock(code: str):
    """手动触发单只股票的L1异动检测"""
    loop = asyncio.get_event_loop()
    quote = await loop.run_in_executor(None, get_quote, code)
    if not quote:
        raise HTTPException(status_code=404, detail=f"无法获取 {code} 行情")

    stock = {"code": code, "name": quote.get("name", code)}
    sug = evaluate_suggestion(stock, quote)
    anomalies = []
    if sug.get("anomaly"):
        anomaly = {
            **sug["anomaly"],
            "code": sug["code"],
            "name": sug["name"],
            "price": sug["price"],
            "change_pct": sug["change_pct"],
            "time": datetime.now().strftime("%H:%M"),
            "l1_advice": sug["advice"],
        }
        anomalies.append(anomaly)
        _anomaly_log.append(anomaly)

    return {
        "checked": 1,
        "anomalies": anomalies,
        "suggestion": sug,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ============================================================
# gbrain 集成 (L3)
# ============================================================

@router.get("/ai/gbrain/search")
async def gbrain_search(q: str = Query(..., min_length=1)):
    """搜索gbrain知识库"""
    return await gbrain_api_search(q)


@router.post("/ai/gbrain/save")
async def gbrain_save(slug: str, title: str, content: str):
    """存入gbrain知识库"""
    return await gbrain_api_save(slug, title, content)


@router.post("/ai/generate-cond-order")
async def generate_cond_order(request: Request):
    """从AI分析结果生成条件单"""
    body = await request.json()
    code = body.get('code', '')
    name = body.get('name', '')
    action = body.get('action', 'buy')
    price = body.get('price', 0)
    shares = body.get('shares', 0)
    condition_type = body.get('condition_type', 'price_lte')

    if not code or not price:
        return JSONResponse({'error': 'code and price required'}, status_code=400)

    def _insert():
        db = _get_db()
        try:
            db.execute(
                """INSERT INTO conditional_orders
                (code, name, action, condition_type, target_price, shares, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')""",
                (code, name, action, condition_type, price, shares)
            )
            db.commit()
        finally:
            db.close()

    await asyncio.to_thread(_insert)
    return {'success': True, 'message': f'条件单已创建: {code} {action} @{price} x{shares}'}


# ============================================================
# Phase 6: 事实账本 + 报告复合验证
# ============================================================

import re

@router.get("/ai/reports/{report_id}/fact-check")
async def fact_check_report(report_id: int):
    """获取事实账本：优先返回七层数据核对结果，否则回退到旧版正则比对"""
    from scheduler.ai_engine import _get_db
    from data.info import get_stock_info
    
    db = _get_db()
    try:
        row = db.execute(
            "SELECT * FROM analysis_reports WHERE id=?", (report_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "报告不存在")
    finally:
        db.close()
    
    # 新结构: 七层数据核对（存储在 fact_check 列）
    fc_raw = row["fact_check"]
    if fc_raw:
        try:
            import json
            fc = json.loads(fc_raw) if isinstance(fc_raw, str) else fc_raw
            if isinstance(fc, dict) and "stages" in fc:
                return fc
        except Exception:
            pass
    
    # 旧结构: 回退到正则比对
    code = row["code"]
    report_text = ""
    for col in ["market_report", "sentiment_report", "news_report", "fundamentals_report",
                "policy_report", "hot_money_report", "lockup_report", "final_decision"]:
        val = row[col] or ""
        if val:
            report_text += val + "\n"
    
    claims = extract_numerical_claims(report_text)
    
    actual_data = {}
    try:
        from data.helpers import tencent_quote_batch
        quotes = await tencent_quote_batch([code])
        q = quotes.get(code, {})
        if q:
            actual_data = {
                "现价": q.get("price"),
                "涨跌幅": q.get("change_pct"),
                "PE": q.get("pe"),
                "总市值": q.get("total_market_cap"),
                "成交量": q.get("volume"),
            }
            actual_data = {k: v for k, v in actual_data.items() if v is not None}
        if actual_data and "PB" not in actual_data:
            try:
                info = await get_stock_info(code)
                if info and info.get("pb"):
                    actual_data["PB"] = info.get("pb")
            except Exception:
                pass
    except Exception:
        pass
    
    results = []
    for claim in claims:
        matched = False
        actual_val = None
        for key, val in actual_data.items():
            if val and claim["keyword"] in key:
                actual_val = val
                try:
                    diff = abs(float(claim["value"]) - float(val))
                    threshold = max(abs(float(val)) * 0.1, 1)
                    matched = diff < threshold
                except (ValueError, TypeError):
                    matched = False
                break
        
        results.append({
            "claim": claim["text"],
            "keyword": claim["keyword"],
            "claimed_value": claim["value"],
            "actual_value": actual_val,
            "status": "verified" if matched else ("mismatch" if actual_val else "unverifiable"),
        })
    
    verified = sum(1 for r in results if r["status"] == "verified")
    mismatched = sum(1 for r in results if r["status"] == "mismatch")
    unverifiable = sum(1 for r in results if r["status"] == "unverifiable")
    total = len(results)
    
    return {
        "report_id": report_id,
        "code": code,
        "total_claims": total,
        "verified": verified,
        "mismatched": mismatched,
        "unverifiable": unverifiable,
        "accuracy": round(verified / max(total - unverifiable, 1) * 100, 1),
        "claims": results,
    }


@router.post("/ai/reports/{report_id}/recheck")
async def recheck_report(report_id: int):
    """重新核对事实账本：读取报告 → 调用七层工具获取数据 → 旁观者模型核对"""
    import asyncio
    from datetime import timedelta
    from scheduler.fact_checker import check_all_stages
    from tradingagents.agents.utils.core_stock_tools import get_stock_data
    from tradingagents.agents.utils.technical_indicators_tools import get_indicators
    from tradingagents.agents.utils.fundamental_data_tools import get_fundamentals, get_balance_sheet, get_cashflow
    from tradingagents.agents.utils.news_data_tools import get_news, get_global_news, get_insider_transactions

    db = _get_db()
    try:
        row = db.execute("SELECT * FROM analysis_reports WHERE id=?", (report_id,)).fetchone()
        if not row:
            raise HTTPException(404, "报告不存在")
    finally:
        db.close()

    code = row["code"]
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    snapshots = {}

    # === 七层数据快照 ===
    # market: K线 + 技术指标 + 腾讯行情
    try:
        sd = get_stock_data.invoke({"symbol": code, "start_date": start, "end_date": today})
        ind = get_indicators.invoke({"symbol": code, "indicator": "all", "curr_date": today})
        from data.helpers import tencent_quote_batch
        quotes = await tencent_quote_batch([code])
        q = quotes.get(code, {})
        snapshots["market"] = f"[get_stock_data]\n{sd}\n\n[get_indicators]\n{ind}\n\n[tencent_quote]\n{json.dumps(q, ensure_ascii=False)}"
    except Exception as e:
        logger.warning("recheck market 失败: %s", e)

    # social: 舆情新闻
    try:
        news = get_news.invoke({"ticker": code, "start_date": start, "end_date": today})
        snapshots["social"] = f"[get_news]\n{news}"
    except Exception as e:
        logger.warning("recheck social 失败: %s", e)

    # news: 新闻 + 全球新闻
    try:
        news = get_news.invoke({"ticker": code, "start_date": start, "end_date": today})
        gnews = get_global_news.invoke({"curr_date": today})
        snapshots["news"] = f"[get_news]\n{news}\n\n[get_global_news]\n{gnews}"
    except Exception as e:
        logger.warning("recheck news 失败: %s", e)

    # fundamentals: 基本面 + 资产负债 + 现金流
    try:
        fund = get_fundamentals.invoke({"ticker": code, "curr_date": today})
        bs = get_balance_sheet.invoke({"ticker": code})
        cf = get_cashflow.invoke({"ticker": code})
        snapshots["fundamentals"] = f"[get_fundamentals]\n{fund}\n\n[get_balance_sheet]\n{bs}\n\n[get_cashflow]\n{cf}"
    except Exception as e:
        logger.warning("recheck fundamentals 失败: %s", e)

    # policy: 政策新闻
    try:
        gnews = get_global_news.invoke({"curr_date": today})
        snapshots["policy"] = f"[get_global_news]\n{gnews}"
    except Exception as e:
        logger.warning("recheck policy 失败: %s", e)

    # hot_money: 资金面
    try:
        sd = get_stock_data.invoke({"symbol": code, "start_date": start, "end_date": today})
        news = get_news.invoke({"ticker": code, "start_date": start, "end_date": today})
        insider = get_insider_transactions.invoke({"ticker": code})
        snapshots["hot_money"] = f"[get_stock_data]\n{sd}\n\n[get_news]\n{news}\n\n[get_insider_transactions]\n{insider}"
    except Exception as e:
        logger.warning("recheck hot_money 失败: %s", e)

    # lockup: 解禁
    try:
        insider = get_insider_transactions.invoke({"ticker": code})
        fund = get_fundamentals.invoke({"ticker": code, "curr_date": today})
        snapshots["lockup"] = f"[get_insider_transactions]\n{insider}\n\n[get_fundamentals]\n{fund}"
    except Exception as e:
        logger.warning("recheck lockup 失败: %s", e)

    # === 提取报告文本 ===
    stage_report_map = {
        "market": "market_report",
        "social": "sentiment_report",
        "news": "news_report",
        "fundamentals": "fundamentals_report",
        "policy": "policy_report",
        "hot_money": "hot_money_report",
        "lockup": "lockup_report",
    }
    task_stages = {}
    for sid, col in stage_report_map.items():
        val = row[col] or ""
        if val:
            task_stages[sid] = {"report": val}

    if not snapshots:
        return {"error": "无法获取数据快照", "stages_checked": 0}

    # === 旁观者核对 ===
    db2 = _get_db()
    try:
        fact_ledger = check_all_stages(snapshots, task_stages, db2)
    finally:
        db2.close()

    # === 写回 DB ===
    db3 = _get_db()
    try:
        db3.execute("UPDATE analysis_reports SET fact_check=? WHERE id=?",
                    (json.dumps(fact_ledger, ensure_ascii=False), report_id))
        db3.commit()
    finally:
        db3.close()

    return fact_ledger


def extract_numerical_claims(text: str) -> list:
    """从报告文本中提取数值型断言"""
    claims = []
    patterns = [
        (r'(?:PE|市盈率|P/E)[^\d]{0,10}(\d+\.?\d*)\s*倍?', 'PE'),
        (r'(?:PB|市净率|P/B)[^\d]{0,10}(\d+\.?\d*)\s*倍?', 'PB'),
        (r'(?:ROE|净资产收益率)[^\d]{0,10}(\d+\.?\d*)\s*%?', 'ROE'),
        (r'(?:营收|收入|营业收入)[^\d]{0,10}[+]?(\d+\.?\d*)\s*%?', '营收'),
        (r'(?:净利润|净利)[^\d]{0,10}[+]?(\d+\.?\d*)\s*%?', '净利润'),
        (r'(?:毛利率)[^\d]{0,10}(\d+\.?\d*)\s*%?', '毛利率'),
        (r'(?:目标价|目标价位)[^\d]{0,10}[¥￥]?(\d+\.?\d*)', '目标价'),
        (r'(?:现价|股价|收盘价)[^\d]{0,10}[¥￥]?(\d+\.?\d*)', '现价'),
        (r'(?:涨跌幅|涨幅|跌幅)[^\d]{0,10}[+-]?(\d+\.?\d*)\s*%?', '涨跌幅'),
        (r'(?:成交量|成交额)[^\d]{0,10}(\d+\.?\d*)\s*[万亿]?', '成交量'),
        (r'(?:总市值|市值)[^\d]{0,10}(\d+\.?\d*)\s*[万亿]?', '总市值'),
    ]
    
    for pattern, keyword in patterns:
        for match in re.finditer(pattern, text):
            claims.append({
                "text": match.group(0)[:50],
                "keyword": keyword,
                "value": match.group(1),
                "position": match.start(),
            })
    
    return claims[:30]  # 限制数量


@router.post("/ai/reports/{report_id}/bystander-verify")
async def bystander_verify(report_id: int):
    """旁观者模型核对报告"""
    from scheduler.ai_engine import _get_db, get_llm_config
    
    db = _get_db()
    try:
        row = db.execute(
            "SELECT * FROM analysis_reports WHERE id=?", (report_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "报告不存在")
    finally:
        db.close()
    
    cfg = get_llm_config()
    verify_model = cfg.get("verification_model") or "mimo-v2.5-pro"
    verify_endpoint = cfg.get("verification_endpoint", "")
    verify_key = cfg.get("verification_api_key", "")
    
    # 构建核对prompt — 完整报告 + 分析结论 + 元数据 + 快照 + 事实账本
    code = row["code"]
    
    # 1. 完整报告（所有分析师 + 决策链）
    analyst_sections = {
        "市场技术": row["market_report"] or "",
        "市场情绪": row["sentiment_report"] or "",
        "新闻舆情": row["news_report"] or "",
        "基本面": row["fundamentals_report"] or "",
        "政策分析": row["policy_report"] or "",
        "游资追踪": row["hot_money_report"] or "",
        "解禁监控": row["lockup_report"] or "",
    }
    decision_sections = {
        "多空辩论": row["investment_debate"] or "",
        "风控评估": row["risk_debate"] or "",
        "交易计划": row["trader_plan"] or "",
        "最终决策": row["final_decision"] or "",
    }
    
    # 判断哪些分析师实际输出了内容
    active_analysts = [k for k, v in analyst_sections.items() if v]
    skipped_analysts = [k for k, v in analyst_sections.items() if not v]
    
    report_text = ""
    for name, content in analyst_sections.items():
        if content:
            report_text += f"\n### {name}\n{content[:400]}\n"
    for name, content in decision_sections.items():
        if content:
            report_text += f"\n### {name}\n{content[:400]}\n"
    
    # 2. 分析结论
    signal = row["signal"] or "N/A"
    confidence = row["confidence"]
    risk_score = row["risk_score"]
    target_price = None
    try:
        if row["raw_state"]:
            raw = json.loads(row["raw_state"])
            target_price = raw.get("target_price")
    except Exception:
        pass
    conclusion = f"信号={signal}"
    if confidence: conclusion += f" 置信度={confidence}"
    if risk_score: conclusion += f" 风险评分={risk_score}"
    if target_price: conclusion += f" 目标价={target_price}"
    
    # 3. 分析元数据
    meta = f"已运行分析师({len(active_analysts)}个): {', '.join(active_analysts)}"
    if skipped_analysts:
        meta += f" | 未运行({len(skipped_analysts)}个): {', '.join(skipped_analysts)}"
    
    # 4. 事实账本（新格式 stages）
    fact_check_info = "无"
    try:
        if row["fact_check"]:
            fc = json.loads(row["fact_check"])
            if fc.get("stages"):
                lines = [f"总准确率={fc.get('overall_accuracy',0)}% 幻觉={fc.get('total_hallucinations',0)}"]
                for sid, st in fc["stages"].items():
                    lines.append(f"  {sid}: {st.get('accuracy',0)}% (匹配{st.get('matched',0)}/幻觉{st.get('mismatched',0)}/无源{st.get('no_source',0)})")
                    for h in st.get("hallucinations", [])[:3]:
                        if h.get("status") == "mismatch":
                            lines.append(f"    ⚠ {h['keyword']}: 报告={h.get('claimed_value')} 实际={h.get('snapshot_value')}")
                fact_check_info = "\n".join(lines)
    except Exception:
        pass
    
    prompt = f"""你是A股分析报告的独立复核员。请基于以下完整证据，评估报告质量。

## 分析结论
{conclusion}

## 分析元数据
{meta}

## 完整报告
{report_text[:3500]}

## 事实核查
{fact_check_info}

## 评估要求
基于以上证据评估：
1. **逻辑严密性**：各分析师观点是否自洽，结论是否被报告内容支撑
2. **深度充分性**：分析广度（{len(active_analysts)}个分析师）是否足够支撑结论
3. **数据一致性**：报告中引用的数字是否与事实核查结果一致
4. **整体可信度**：综合评分

JSON输出：
{{"hallucinations": [{{"claim": "具体问题", "issue": "说明", "severity": "high/medium/low"}}], "overall_score": 0-100, "summary": "评估结论"}}"""

    # 调用旁观者模型
    import subprocess
    import yaml
    
    api_url = verify_endpoint or "https://token-plan-cn.xiaomimimo.com/v1"
    if not api_url.endswith("/chat/completions"):
        api_url = api_url.rstrip("/") + "/chat/completions"
    
    # 密钥优先级：设置页 → 环境变量 → Hermes配置
    api_key = verify_key
    if not api_key:
        api_key = os.environ.get("MIMO_API_KEY", "")
    if not api_key:
        try:
            hermes_cfg = os.path.expanduser("~/.hermes/config.yaml")
            with open(hermes_cfg) as f:
                cfg = yaml.safe_load(f)
            for m in cfg.get("custom_providers", []):
                if "mimo" in str(m.get("base_url", "")).lower():
                    # 优先用小米mimo的key
                    if not api_key or m.get("name") == "小米mimo":
                        api_key = m.get("api_key", "")
        except Exception:
            pass
    
    if not api_key:
        return {"error": f"未配置API密钥 verify_key={bool(verify_key)} env={bool(os.environ.get('MIMO_API_KEY',''))} hermes_found={api_key[:10] if api_key else 'no'}", "status": "skipped"}
    
    try:
        import subprocess, tempfile
        payload = json.dumps({
            "model": verify_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2000,
            "temperature": 0.3,
        })
        result = subprocess.run([
            "curl", "-s", "-X", "POST", api_url,
            "-H", f"Authorization: Bearer {api_key}",
            "-H", "Content-Type: application/json",
            "-d", payload,
            "--max-time", "60"
        ], capture_output=True, text=True, timeout=65)
        
        if result.returncode != 0 or not result.stdout.strip():
            return {"error": f"curl失败 code={result.returncode} stderr={result.stderr[:200]} stdout={result.stdout[:100]}", "status": "failed"}
        
        resp_data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return {"error": f"API响应非JSON url={api_url} model={verify_model} key={api_key[:8]}...: {result.stdout[:300]}", "status": "failed"}
    except Exception as e:
        return {"error": f"API调用失败: {e}", "status": "failed"}

    if "choices" not in resp_data:
        return {"error": f"API响应异常: {json.dumps(resp_data, ensure_ascii=False)[:300]}", "status": "failed"}

    try:
        content = resp_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return {"error": f"响应格式异常: {e} | {json.dumps(resp_data, ensure_ascii=False)[:300]}", "status": "failed"}

    # 尝试解析JSON
    try:
        import json as _json
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            result = _json.loads(json_match.group())
        else:
            result = {"summary": content, "overall_score": 50, "hallucinations": []}

        # 保存到DB
        try:
            _db = _get_db()
            _db.execute("UPDATE analysis_reports SET bystander_verify=? WHERE id=?", (json.dumps(result, ensure_ascii=False), report_id))
            _db.commit()
            _db.close()
        except Exception as _e:
            import logging; logging.getLogger(__name__).warning("bystander save failed: %s", _e)

        return {
            "report_id": report_id,
            "code": code,
            "verify_model": verify_model,
            "result": result,
        }
    except Exception as e:
        return {"error": str(e), "status": "failed"}
