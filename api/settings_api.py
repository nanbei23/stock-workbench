"""设置API — 完整CRUD，SQLite持久化
路由顺序: 具体路径 MUST 在 {key} 参数路由之前，否则会被吞掉。
"""
import os
import sqlite3
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime

router = APIRouter(tags=["设置"])

DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "workbench.db")


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def _init_settings_table():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


_init_settings_table()

# ── 默认设置 ──
DEFAULTS = {
    # 行情监控
    "auto_refresh_enabled": "false",
    "refresh_interval": "30",
    "change_threshold": "5",
    "volume_threshold": "3",
    "northbound_threshold": "5",
    "anomaly_monitor_enabled": "true",
    # AI引擎
    "llm_provider": "deepseek",
    "deep_think_model": "deepseek-reasoner",
    "quick_think_model": "deepseek-chat",
    "api_key": "",
    "custom_endpoint": "",
    "output_language": "zh",
    "verification_model": "mimo-v2.5-pro",
    "debate_rounds": "1",
    "risk_rounds": "1",
    "checkpoint_enabled": "true",
    # AI调度
    "schedule_open_report": "true",
    "schedule_am_close": "true",
    "schedule_pm_open": "true",
    "schedule_close_report": "true",
    "schedule_anomaly_realtime": "true",
    # 通知
    "notify_strategy_change": "true",
    "notify_order_trigger": "true",
    "notify_anomaly": "true",
    "notify_analysis_done": "true",
    "browser_notify_enabled": "false",
    # 费率
    "commission_rate": "0.0003",
    "commission_min": "5",
    "stamp_tax_rate": "0.0005",
    "transfer_fee_rate": "0.00001",
    # 持仓止损
    "stop_loss_pct": "8",
}


# ── Pydantic Models ──

class SettingUpdate(BaseModel):
    key: str
    value: Any


class SettingsBulkUpdate(BaseModel):
    settings: dict


class ImportData(BaseModel):
    watchlist: Optional[list] = None
    portfolio: Optional[list] = None
    orders: Optional[list] = None
    settings: Optional[dict] = None


# ═══════════════════════════════════════════════
# ★ 具体路径 MUST 在 {key} 之前注册
# ═══════════════════════════════════════════════

# ── 获取全部设置 ──

@router.get("/settings")
async def get_all_settings():
    """获取全部设置（合并默认值）"""
    conn = _conn()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    saved = {r["key"]: r["value"] for r in rows}
    result = {}
    # 先加载所有DB中的设置
    result.update(saved)
    # 再补上默认值（DB中不存在的key）
    for k, v in DEFAULTS.items():
        if k not in result:
            result[k] = v
    return result


# ── 批量更新 ──

@router.post("/settings/bulk")
async def bulk_update_settings(data: SettingsBulkUpdate):
    """批量更新设置"""
    conn = _conn()
    for k, v in data.settings.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, str(v))
        )
    conn.commit()
    conn.close()
    return {"status": "ok", "updated": len(data.settings)}


# ── 重置默认 ──

@router.post("/settings/reset")
async def reset_settings():
    """重置为默认设置"""
    conn = _conn()
    conn.execute("DELETE FROM settings")
    for k, v in DEFAULTS.items():
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()
    return {"status": "ok", "reset": len(DEFAULTS)}


# ── 测试API连接 ──

@router.post("/settings/test-llm")
async def test_api_connection():
    """测试DeepSeek API连接"""
    conn = _conn()
    api_key = conn.execute("SELECT value FROM settings WHERE key='api_key'").fetchone()
    endpoint = conn.execute("SELECT value FROM settings WHERE key='custom_endpoint'").fetchone()
    model = conn.execute("SELECT value FROM settings WHERE key='quick_think_model'").fetchone()
    conn.close()

    key = (api_key["value"] if api_key else "") or os.environ.get("DEEPSEEK_API_KEY", "")
    url = (endpoint["value"] if endpoint else "") or "https://api.deepseek.com"
    mdl = model["value"] if model else "deepseek-chat"

    if not key:
        return {"status": "error", "message": "API密钥未配置"}

    try:
        resp = httpx.post(
            f"{url}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": mdl, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
            timeout=15,
        )
        if resp.status_code == 200:
            return {"status": "ok", "message": f"连接成功 ({mdl})",
                    "latency_ms": int(resp.elapsed.total_seconds() * 1000)}
        return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


# ── 测试旁观者核对连接 ──

@router.post("/settings/test-verification")
async def test_verification_connection():
    """测试旁观者核对模型API连接"""
    conn = _conn()
    rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'verification_%'").fetchall()
    conn.close()
    cfg = {r["key"]: r["value"] for r in rows}

    model = cfg.get("verification_model") or "mimo-v2.5-pro"
    endpoint = cfg.get("verification_endpoint") or "https://token-plan-cn.xiaomimimo.com/v1"
    api_key = cfg.get("verification_api_key") or ""

    if not api_key:
        return {"status": "error", "message": "API密钥未配置"}

    import subprocess, json
    try:
        result = subprocess.run([
            "curl", "-s", "-X", "POST", f"{endpoint}/chat/completions",
            "-H", f"Authorization: Bearer {api_key}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5}),
            "--max-time", "15",
        ], capture_output=True, text=True, timeout=20)
        if result.stdout:
            data = json.loads(result.stdout)
            if "choices" in data:
                return {"status": "ok", "message": f"连接成功 ({model})"}
            if "error" in data:
                return {"status": "error", "message": data["error"].get("message", "未知错误")[:200]}
        return {"status": "error", "message": f"无响应: {result.stderr[:200]}"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


# ── 导出全部数据 ──

@router.get("/settings/export")
async def export_all_data():
    """导出全部数据（JSON下载）"""
    conn = _conn()
    result = {}

    # 设置
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    result["settings"] = {r["key"]: r["value"] for r in rows}

    # 自选股
    rows = conn.execute("SELECT * FROM watchlist ORDER BY sort_order ASC").fetchall()
    result["watchlist"] = [dict(r) for r in rows]

    # 持仓
    rows = conn.execute("SELECT * FROM portfolio ORDER BY code").fetchall()
    result["portfolio"] = [dict(r) for r in rows]

    # 条件单
    rows = conn.execute("SELECT * FROM conditional_orders ORDER BY id").fetchall()
    result["orders"] = [dict(r) for r in rows]

    # 分析报告（最近100条）
    rows = conn.execute(
        "SELECT * FROM analysis_reports ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    result["reports"] = [dict(r) for r in rows]

    # 盈亏日历
    rows = conn.execute("SELECT * FROM daily_pnl ORDER BY date DESC").fetchall()
    result["daily_pnl"] = [dict(r) for r in rows]

    conn.close()

    # 返回文件下载
    import json
    content = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    filename = f"stock-workbench-backup-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── 导入数据 ──

@router.post("/settings/import")
async def import_data(data: ImportData):
    """导入数据（追加/覆盖模式）"""
    conn = _conn()
    imported = {"watchlist": 0, "portfolio": 0, "orders": 0, "settings": 0}

    if data.settings:
        for k, v in data.settings.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v))
            )
        imported["settings"] = len(data.settings)

    if data.watchlist:
        for item in data.watchlist:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO watchlist (code, name, group_name, strategy_state) "
                    "VALUES (?, ?, ?, ?)",
                    (item.get("code", ""), item.get("name", ""),
                     item.get("group_name", "默认"), item.get("strategy_state", "watch"))
                )
                imported["watchlist"] += 1
            except Exception:
                pass

    if data.portfolio:
        for item in data.portfolio:
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO portfolio "
                    "(code, name, total_shares, available_shares, avg_cost) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (item.get("code", ""), item.get("name", ""),
                     item.get("total_shares", 0), item.get("available_shares", 0),
                     item.get("avg_cost", 0))
                )
                imported["portfolio"] += 1
            except Exception:
                pass

    if data.orders:
        for item in data.orders:
            try:
                conn.execute(
                    "INSERT INTO conditional_orders "
                    "(code, name, condition_type, target_price, action, shares, status, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (item.get("code", ""), item.get("name", ""),
                     item.get("condition_type", "price_lte"),
                     item.get("target_price", 0), item.get("action", "buy"),
                     item.get("shares", 0), item.get("status", "active"),
                     item.get("notes", ""))
                )
                imported["orders"] += 1
            except Exception:
                pass

    conn.commit()
    conn.close()
    return {"status": "ok", "imported": imported}


# ── 清空全部数据 ──

@router.post("/settings/clear-all")
async def clear_all_data():
    """清空全部数据（危险操作）"""
    conn = _conn()
    tables = ["watchlist", "portfolio", "trades", "conditional_orders",
              "analysis_reports", "daily_pnl", "anomaly_logs"]
    counts = {}
    for table in tables:
        try:
            row = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()
            counts[table] = row["c"]
            conn.execute(f"DELETE FROM {table}")
        except Exception:
            counts[table] = "N/A"
    conn.commit()
    conn.close()
    return {"status": "ok", "cleared": counts}


# ── 通知轮询 ──

@router.get("/notifications")
async def poll_notifications():
    """轮询通知（条件单触发/分析完成/策略变化/异动）"""
    notifications = []
    conn = _conn()

    # 最近5分钟触发的条件单
    rows = conn.execute("""
        SELECT code, name, condition_type, target_price, action, shares, triggered_at
        FROM conditional_orders
        WHERE status = 'triggered'
          AND triggered_at > datetime('now', '-5 minutes')
        ORDER BY triggered_at DESC
    """).fetchall()
    for r in rows:
        d = dict(r)
        action_str = "买入" if d.get("action") == "buy" else "卖出"
        notifications.append({
            "type": "order_trigger",
            "title": f"条件单触发: {d.get('name', '') or d['code']}",
            "body": f"{action_str} {d.get('shares', 0)}股 @ {d['target_price']}",
            "time": d.get("triggered_at", ""),
            "data": d,
        })

    # 最近2分钟完成的分析报告
    rows = conn.execute("""
        SELECT id, code, signal, confidence, created_at
        FROM analysis_reports
        WHERE created_at > datetime('now', '-2 minutes')
        ORDER BY created_at DESC
        LIMIT 3
    """).fetchall()
    for r in rows:
        d = dict(r)
        notifications.append({
            "type": "analysis_done",
            "title": f"AI分析完成: {d['code']}",
            "body": f"信号: {d['signal']} | 置信度: {d.get('confidence', '—')}%",
            "time": d.get("created_at", ""),
            "data": d,
        })

    # 策略状态变化（最近5分钟内有更新的watchlist股票）
    rows = conn.execute("""
        SELECT code, name, strategy_state, strategy_state_updated_at
        FROM watchlist
        WHERE strategy_state_updated_at IS NOT NULL
          AND strategy_state_updated_at > datetime('now', '-5 minutes')
        ORDER BY strategy_state_updated_at DESC
    """).fetchall()
    state_labels = {
        "watch": "观察", "buy_zone": "买入区", "sell_zone": "卖出区",
        "hold": "持有", "stop_loss": "止损", "take_profit": "止盈",
    }
    for r in rows:
        d = dict(r)
        state_label = state_labels.get(d.get("strategy_state", ""), d.get("strategy_state", ""))
        notifications.append({
            "type": "strategy_change",
            "title": f"策略状态变化: {d.get('name', '') or d['code']}",
            "body": f"状态变更为: {state_label}",
            "time": d.get("strategy_state_updated_at", ""),
            "data": d,
        })

    # 异动告警（最近5分钟）
    rows = conn.execute("""
        SELECT id, code, name, anomaly_type, description, severity, created_at
        FROM anomaly_logs
        WHERE created_at > datetime('now', '-5 minutes')
        ORDER BY created_at DESC
        LIMIT 5
    """).fetchall()
    for r in rows:
        d = dict(r)
        severity_icon = "🔴" if d.get("severity") == "critical" else "🟡" if d.get("severity") == "warning" else "ℹ️"
        notifications.append({
            "type": "anomaly",
            "title": f"{severity_icon} 异动告警: {d.get('name', '') or d['code']}",
            "body": f"{d.get('anomaly_type', '')} — {d.get('description', '')[:80]}",
            "time": d.get("created_at", ""),
            "data": d,
        })

    conn.close()
    # 按时间排序（最新在前）
    notifications.sort(key=lambda n: n.get("time", ""), reverse=True)
    return {"count": len(notifications), "notifications": notifications}


# ── 模型模式快捷设置（POST，AI分析台调用）──

@router.post("/settings/model_mode")
async def set_model_mode(data: SettingUpdate):
    """快捷设置模型模式（POST，供AI分析台调用）"""
    conn = _conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("model_mode", str(data.value))
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "key": "model_mode", "value": data.value}


# ── 获取远程模型列表（兼容 OpenAI /v1/models 协议）──

class FetchModelsReq(BaseModel):
    endpoint: str
    api_key: str = ""

@router.post("/settings/fetch-models")
async def fetch_models(req: FetchModelsReq):
    """从自定义API端点获取模型列表（兼容 OpenAI /v1/models 协议）"""
    endpoint = req.endpoint.rstrip("/")
    # 自动拼接 /v1/models（处理端点已含 /v1 的情况）
    base = endpoint.rstrip("/")
    if base.endswith("/v1"):
        url = f"{base}/models"
    elif base.endswith("/models"):
        url = base
    else:
        url = f"{base}/v1/models"
    headers = {}
    if req.api_key:
        headers["Authorization"] = f"Bearer {req.api_key}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        # 解析 OpenAI 格式 {"data": [{"id": "model-name", ...}, ...]}
        models = []
        if isinstance(data, dict) and "data" in data:
            models = [m["id"] for m in data["data"] if isinstance(m, dict) and "id" in m]
        elif isinstance(data, list):
            models = [m if isinstance(m, str) else m.get("id", "") for m in data]
            models = [m for m in models if m]
        return {"status": "ok", "models": sorted(models)}
    except httpx.ConnectError:
        raise HTTPException(400, detail="无法连接到API端点，请检查地址是否正确")
    except httpx.TimeoutException:
        raise HTTPException(408, detail="连接超时")
    except Exception as e:
        raise HTTPException(500, detail=f"获取模型失败: {str(e)}")


# ═══════════════════════════════════════════════
# ★ {key} 参数路由 — 放在最后
# ═══════════════════════════════════════════════

@router.get("/settings/{key}")
async def get_setting(key: str):
    """获取单个设置"""
    conn = _conn()
    row = conn.execute("SELECT key, value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row:
        return {"key": row["key"], "value": row["value"]}
    if key in DEFAULTS:
        return {"key": key, "value": DEFAULTS[key]}
    raise HTTPException(404, f"设置项 {key} 不存在")


@router.put("/settings/{key}")
async def update_setting(key: str, data: SettingUpdate):
    """更新单个设置"""
    conn = _conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(data.value))
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "key": key, "value": data.value}
