"""Cross-cutting enhancement features for settings, AI review, and risk panels."""

import json
import re
import uuid
from datetime import datetime

import httpx
from fastapi import HTTPException

from models.database import get_db
from repositories import settings_repository


MODEL_PROVIDERS_KEY = "model_providers"
WORKSPACE_TEMPLATES_KEY = "workspace_templates"


def _loads(value, fallback):
    if not value:
        return fallback
    try:
        data = json.loads(value) if isinstance(value, str) else value
        return data if data is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _dumps(value):
    return json.dumps(value, ensure_ascii=False)


def _settings():
    return settings_repository.fetch_settings()


def _provider_name(base_url: str) -> str:
    host = re.sub(r"^https?://", "", base_url or "").split("/")[0].split(":")[0]
    host = re.sub(r"^api\.", "", host)
    return f"{host.split('.')[0]} analysis" if host else "custom analysis"


def list_model_providers():
    settings = _settings()
    providers = _loads(settings.get(MODEL_PROVIDERS_KEY), [])
    return {"count": len(providers), "providers": [_public_provider(item) for item in providers]}


def _public_provider(provider: dict):
    public = {key: value for key, value in provider.items() if key != "api_key"}
    public["has_api_key"] = bool(provider.get("api_key"))
    return public


def _public_settings(settings: dict):
    public = dict(settings)
    if "api_key" in public:
        public["api_key"] = "********" if public["api_key"] else ""
    if "verification_api_key" in public:
        public["verification_api_key"] = "********" if public["verification_api_key"] else ""
    return public


def save_model_provider(payload: dict):
    settings = _settings()
    providers = _loads(settings.get(MODEL_PROVIDERS_KEY), [])
    provider_id = payload.get("id") or str(uuid.uuid4())[:8]
    base_url = (payload.get("base_url") or "").strip()
    if not base_url:
        raise HTTPException(400, "Base URL required")
    provider = {
        "id": provider_id,
        "name": payload.get("name") or _provider_name(base_url),
        "base_url": base_url,
        "api_key": payload.get("api_key") or "",
        "models": payload.get("models") or [],
        "quick_model": payload.get("quick_model") or payload.get("default_model") or "",
        "deep_model": payload.get("deep_model") or payload.get("default_model") or "",
        "default_model": payload.get("default_model") or "",
        "context_length": payload.get("context_length") or "",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    providers = [item for item in providers if item.get("id") != provider_id]
    providers.insert(0, provider)
    settings_repository.upsert_settings({MODEL_PROVIDERS_KEY: _dumps(providers)})
    return {"status": "ok", "provider": _public_provider(provider), "count": len(providers)}


def delete_model_provider(provider_id: str):
    settings = _settings()
    providers = _loads(settings.get(MODEL_PROVIDERS_KEY), [])
    kept = [item for item in providers if item.get("id") != provider_id]
    settings_repository.upsert_settings({MODEL_PROVIDERS_KEY: _dumps(kept)})
    return {"status": "ok", "deleted": len(providers) - len(kept), "count": len(kept)}


def apply_model_provider(provider_id: str, target: str = "ai"):
    providers = _loads(_settings().get(MODEL_PROVIDERS_KEY), [])
    provider = next((item for item in providers if item.get("id") == provider_id), None)
    if not provider:
        raise HTTPException(404, "模型配置不存在")
    models = provider.get("models") or []
    if target == "verification":
        updates = {
            "verification_name": provider.get("name", ""),
            "verification_endpoint": provider.get("base_url", ""),
            "verification_api_key": provider.get("api_key", ""),
            "verification_model": provider.get("default_model") or provider.get("deep_model") or provider.get("quick_model") or "",
            "verification_model_options": _dumps(models),
            "verification_context_length": provider.get("context_length", ""),
        }
    else:
        updates = {
            "llm_name": provider.get("name", ""),
            "custom_endpoint": provider.get("base_url", ""),
            "api_key": provider.get("api_key", ""),
            "quick_think_model": provider.get("quick_model") or provider.get("default_model") or "",
            "deep_think_model": provider.get("deep_model") or provider.get("default_model") or "",
            "llm_model_options": _dumps(models),
            "llm_context_length": provider.get("context_length", ""),
        }
    settings_repository.upsert_settings(updates)
    return {"status": "ok", "target": target, "settings": _public_settings(updates)}


def _model_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/models"
    if base.endswith("/models"):
        return base
    return f"{base}/v1/models"


async def refresh_model_provider(provider_id: str):
    settings = _settings()
    providers = _loads(settings.get(MODEL_PROVIDERS_KEY), [])
    provider = next((item for item in providers if item.get("id") == provider_id), None)
    if not provider:
        raise HTTPException(404, "模型配置不存在")
    url = _model_url(provider.get("base_url", ""))
    headers = {}
    if provider.get("api_key"):
        headers["Authorization"] = f"Bearer {provider['api_key']}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"模型刷新失败: {exc}") from exc
    models = []
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        models = [item.get("id") for item in data["data"] if isinstance(item, dict) and item.get("id")]
    elif isinstance(data, list):
        models = [item if isinstance(item, str) else item.get("id", "") for item in data]
        models = [item for item in models if item]
    provider["models"] = sorted(set(models))
    provider["updated_at"] = datetime.now().isoformat(timespec="seconds")
    settings_repository.upsert_settings({MODEL_PROVIDERS_KEY: _dumps(providers)})
    return {"status": "ok", "provider": _public_provider(provider), "models": provider["models"]}


async def test_model_provider(provider_id: str):
    settings = _settings()
    providers = _loads(settings.get(MODEL_PROVIDERS_KEY), [])
    provider = next((item for item in providers if item.get("id") == provider_id), None)
    if not provider:
        raise HTTPException(404, "模型配置不存在")
    model = provider.get("default_model") or provider.get("quick_model") or provider.get("deep_model")
    if not model:
        raise HTTPException(400, "请先为该配置选择模型")
    base = (provider.get("base_url") or "").rstrip("/")
    if not base:
        raise HTTPException(400, "Base URL required")
    chat_url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if provider.get("api_key"):
        headers["Authorization"] = f"Bearer {provider['api_key']}"
    started = datetime.now()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                chat_url,
                headers=headers,
                json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
            )
        latency_ms = int((datetime.now() - started).total_seconds() * 1000)
        if resp.status_code < 400:
            return {"status": "ok", "model": model, "latency_ms": latency_ms}
        text = resp.text[:200]
        return {"status": "error", "model": model, "latency_ms": latency_ms, "message": f"HTTP {resp.status_code}: {text}"}
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"连接测试失败: {exc}") from exc


async def _fetchall(query: str, params=()):
    db = await get_db()
    try:
        rows = await db.execute_fetchall(query, params)
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def report_versions(code: str, limit: int = 20):
    rows = await _fetchall(
        """
        SELECT id, task_id, code, signal, confidence, risk_score, depth,
               model_mode, created_at, duration_seconds
        FROM analysis_reports
        WHERE code = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (code[:6], max(1, min(limit, 100))),
    )
    return {"code": code[:6], "count": len(rows), "versions": rows}


async def compare_reports(left_id: int, right_id: int):
    rows = await _fetchall(
        "SELECT id, code, signal, confidence, risk_score, raw_state, created_at FROM analysis_reports WHERE id IN (?, ?)",
        (left_id, right_id),
    )
    reports = {row["id"]: row for row in rows}
    if left_id not in reports or right_id not in reports:
        raise HTTPException(404, "报告不存在")
    left = reports[left_id]
    right = reports[right_id]
    return {
        "left": left,
        "right": right,
        "diff": {
            "signal_changed": left.get("signal") != right.get("signal"),
            "confidence_delta": _num(right.get("confidence")) - _num(left.get("confidence")),
            "risk_delta": _num(right.get("risk_score")) - _num(left.get("risk_score")),
            "same_code": left.get("code") == right.get("code"),
        },
    }


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


async def condition_backtest(payload: dict):
    code = (payload.get("code") or "").strip()[:6]
    if not code:
        raise HTTPException(400, "code required")
    condition_type = payload.get("condition_type") or "price_lte"
    target = _num(payload.get("target_price"))
    rows = await _fetchall(
        """
        SELECT date, close_price
        FROM daily_pnl
        WHERE code6 = ? AND close_price IS NOT NULL
        ORDER BY date DESC
        LIMIT ?
        """,
        (code, int(payload.get("days") or 90)),
    )
    rows = list(reversed(rows))
    triggers = []
    for row in rows:
        price = _num(row.get("close_price"))
        hit = (
            condition_type == "price_lte" and price <= target
            or condition_type == "price_gte" and price >= target
        )
        if hit:
            triggers.append({"date": row.get("date"), "price": price})
    first = triggers[0] if triggers else None
    last_price = _num(rows[-1].get("close_price")) if rows else 0
    return {
        "code": code,
        "condition_type": condition_type,
        "target_price": target,
        "sample_days": len(rows),
        "trigger_count": len(triggers),
        "first_trigger": first,
        "last_price": last_price,
        "post_trigger_return_pct": round((last_price - first["price"]) / first["price"] * 100, 2) if first and first["price"] else None,
        "triggers": triggers[:20],
    }


async def risk_exposure():
    rows = await _fetchall(
        """
        SELECT p.code, p.name, p.total_shares, p.avg_cost, p.current_price,
               p.market_value, p.account_id, a.name AS account_name
        FROM portfolio p
        LEFT JOIN accounts a ON a.id = p.account_id
        WHERE p.total_shares > 0
        """
    )
    total = 0.0
    accounts = {}
    positions = []
    for row in rows:
        value = _num(row.get("market_value")) or _num(row.get("current_price")) * _num(row.get("total_shares")) or _num(row.get("avg_cost")) * _num(row.get("total_shares"))
        total += value
        account_id = row.get("account_id") or "default"
        accounts.setdefault(account_id, {"id": account_id, "name": row.get("account_name") or account_id, "market_value": 0.0, "positions": 0})
        accounts[account_id]["market_value"] += value
        accounts[account_id]["positions"] += 1
        positions.append({**row, "market_value_calc": round(value, 2), "bucket": (row.get("code") or "")[:2]})
    for item in positions:
        item["weight_pct"] = round(item["market_value_calc"] / total * 100, 2) if total else 0
    buckets = {}
    for item in positions:
        buckets[item["bucket"]] = buckets.get(item["bucket"], 0) + item["market_value_calc"]
    warnings = []
    top = max(positions, key=lambda item: item["weight_pct"], default=None)
    if top and top["weight_pct"] >= 35:
        warnings.append(f"单一个股集中度较高：{top.get('name') or top.get('code')} {top['weight_pct']}%")
    for bucket, value in buckets.items():
        pct = value / total * 100 if total else 0
        if pct >= 50:
            warnings.append(f"代码段 {bucket} 暴露超过 {pct:.1f}% ，建议检查行业/主题集中度")
    return {
        "total_market_value": round(total, 2),
        "accounts": list(accounts.values()),
        "positions": sorted(positions, key=lambda item: item["weight_pct"], reverse=True),
        "buckets": [{"bucket": k, "market_value": round(v, 2), "weight_pct": round(v / total * 100, 2) if total else 0} for k, v in sorted(buckets.items())],
        "warnings": warnings,
    }


async def events():
    news = await _fetchall(
        """
        SELECT code6, source, title, published_at, cached_at
        FROM news_cache
        WHERE title LIKE '%公告%' OR title LIKE '%财报%' OR title LIKE '%减持%' OR title LIKE '%解禁%' OR title LIKE '%问询%'
        ORDER BY COALESCE(published_at, cached_at) DESC
        LIMIT 20
        """
    )
    orders = await _fetchall(
        """
        SELECT id, code, name, expires_at, status
        FROM conditional_orders
        WHERE status = 'pending' AND expires_at IS NOT NULL
        ORDER BY expires_at ASC
        LIMIT 10
        """
    )
    items = [
        {"type": "news_event", "code": row.get("code6"), "title": row.get("title"), "time": row.get("published_at") or row.get("cached_at"), "source": row.get("source")}
        for row in news
    ]
    items.extend(
        {"type": "order_expiry", "code": row.get("code"), "title": f"条件单即将到期：{row.get('name') or row.get('code')}", "time": row.get("expires_at"), "source": "conditional_order"}
        for row in orders
    )
    return {"count": len(items), "events": sorted(items, key=lambda item: item.get("time") or "", reverse=True)}


async def data_health():
    checks = []
    settings = _settings()
    checks.append({
        "key": "ai_models",
        "label": "AI模型列表",
        "status": "ok" if _loads(settings.get("llm_model_options"), []) else "warning",
        "message": "已获取模型列表" if _loads(settings.get("llm_model_options"), []) else "AI分析引擎尚未从 Base URL 获取模型列表",
    })
    duplicate_trades = await _fetchall(
        """
        SELECT code, trade_time, price, shares, COUNT(*) AS c
        FROM trades
        GROUP BY code, trade_time, price, shares
        HAVING c > 1
        LIMIT 10
        """
    )
    checks.append({"key": "duplicate_trades", "label": "重复交易", "status": "warning" if duplicate_trades else "ok", "message": f"{len(duplicate_trades)} 组疑似重复交易"})
    expired_orders = await _fetchall("SELECT COUNT(*) AS c FROM conditional_orders WHERE status='pending' AND expires_at IS NOT NULL AND expires_at < datetime('now')")
    expired_count = int(expired_orders[0]["c"] if expired_orders else 0)
    checks.append({"key": "expired_orders", "label": "过期条件单", "status": "warning" if expired_count else "ok", "message": f"{expired_count} 个过期未取消条件单"})
    no_fact = await _fetchall("SELECT COUNT(*) AS c FROM analysis_reports WHERE fact_check IS NULL OR fact_check = ''")
    no_fact_count = int(no_fact[0]["c"] if no_fact else 0)
    checks.append({"key": "report_fact_check", "label": "报告事实核对", "status": "warning" if no_fact_count else "ok", "message": f"{no_fact_count} 份报告尚未事实核对"})
    return {"checks": checks, "ok": all(item["status"] == "ok" for item in checks)}


async def fix_data_health():
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE conditional_orders SET status='expired' WHERE status='pending' AND expires_at IS NOT NULL AND expires_at < datetime('now')"
        )
        await db.commit()
        return {"status": "ok", "expired_orders": cursor.rowcount}
    finally:
        await db.close()


def workspace_templates():
    settings = _settings()
    saved = _loads(settings.get(WORKSPACE_TEMPLATES_KEY), [])
    builtins = [
        {"id": "steady", "name": "稳健复盘", "description": "标准深度、低轮数、适合日常收盘复盘", "settings": {"model_mode": "balanced", "debate_rounds": "1", "risk_rounds": "1"}},
        {"id": "deep-risk", "name": "深度风控", "description": "提高辩论和风控轮数，适合重仓前审查", "settings": {"model_mode": "flagship", "debate_rounds": "3", "risk_rounds": "3"}},
        {"id": "low-cost", "name": "低成本扫描", "description": "低成本模型模式，适合批量自选扫描", "settings": {"model_mode": "economy", "debate_rounds": "1", "risk_rounds": "1"}},
    ]
    return {"templates": builtins + saved}


def save_workspace_template(name: str):
    settings = _settings()
    saved = _loads(settings.get(WORKSPACE_TEMPLATES_KEY), [])
    keys = ["model_mode", "debate_rounds", "risk_rounds", "llm_name", "custom_endpoint", "quick_think_model", "deep_think_model", "llm_model_options", "verification_name", "verification_endpoint", "verification_model"]
    template = {
        "id": str(uuid.uuid4())[:8],
        "name": name or f"配置模板 {datetime.now().strftime('%m-%d %H:%M')}",
        "description": "从当前设置保存",
        "settings": {key: settings.get(key, "") for key in keys},
    }
    saved.insert(0, template)
    settings_repository.upsert_settings({WORKSPACE_TEMPLATES_KEY: _dumps(saved)})
    return {"status": "ok", "template": template}


def apply_workspace_template(template_id: str):
    template = next((item for item in workspace_templates()["templates"] if item.get("id") == template_id), None)
    if not template:
        raise HTTPException(404, "模板不存在")
    settings_repository.upsert_settings(template.get("settings") or {})
    return {"status": "ok", "template": template}


async def task_metrics():
    rows = await _fetchall(
        """
        SELECT status, queue_status, elapsed, error, depth, debate_rounds, risk_rounds
        FROM analysis_tasks
        ORDER BY updated_at DESC
        LIMIT 200
        """
    )
    by_status = {}
    by_depth = {}
    failures = []
    elapsed = []
    for row in rows:
        status = row.get("queue_status") or row.get("status") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
        depth = row.get("depth") or "standard"
        by_depth[depth] = by_depth.get(depth, 0) + 1
        if row.get("error"):
            failures.append(row.get("error"))
        if row.get("elapsed") is not None:
            elapsed.append(_num(row.get("elapsed")))
    return {
        "total": len(rows),
        "by_status": by_status,
        "by_depth": by_depth,
        "avg_elapsed": round(sum(elapsed) / len(elapsed), 2) if elapsed else 0,
        "recent_failures": failures[:5],
    }


async def system_diagnostics():
    providers = list_model_providers()
    health = await data_health()
    tasks = await task_metrics()
    risk = await risk_exposure()
    event_data = await events()
    settings = _settings()
    model_options = _loads(settings.get("llm_model_options"), [])
    verification_options = _loads(settings.get("verification_model_options"), [])
    warnings = []
    if not model_options:
        warnings.append("AI分析引擎尚未获取模型列表")
    if settings.get("verification_enabled") == "true" and not verification_options:
        warnings.append("旁观者核对已启用但尚未获取模型列表")
    warnings.extend(item.get("message", "") for item in health.get("checks", []) if item.get("status") != "ok")
    warnings.extend(risk.get("warnings", []))
    if tasks.get("by_status", {}).get("failed"):
        warnings.append(f"最近任务失败 {tasks['by_status']['failed']} 个")
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "model_provider_count": providers["count"],
            "ai_model_count": len(model_options),
            "verification_model_count": len(verification_options),
            "task_count": tasks.get("total", 0),
            "risk_warning_count": len(risk.get("warnings", [])),
            "event_count": event_data.get("count", 0),
            "health_ok": health.get("ok", False),
            "warning_count": len([item for item in warnings if item]),
        },
        "warnings": [item for item in warnings if item][:12],
        "health": health,
        "tasks": tasks,
        "risk": {
            "total_market_value": risk.get("total_market_value", 0),
            "position_count": len(risk.get("positions", [])),
            "warnings": risk.get("warnings", []),
        },
        "events": {"count": event_data.get("count", 0), "latest": (event_data.get("events") or [])[:5]},
    }


def ai_readiness():
    settings = _settings()
    model_options = _loads(settings.get("llm_model_options"), [])
    blockers = []
    warnings = []
    if not settings.get("custom_endpoint"):
        blockers.append("请先在设置页填写 AI 分析引擎 Base URL")
    if not settings.get("api_key"):
        blockers.append("请先填写 AI 分析引擎 API Key")
    if not model_options:
        blockers.append("请先通过 Base URL 获取 AI 模型列表")
    if not settings.get("quick_think_model"):
        blockers.append("请先选择快速思考模型")
    if not settings.get("deep_think_model"):
        blockers.append("请先选择深度思考模型")
    if settings.get("quick_think_model") and model_options and settings.get("quick_think_model") not in model_options:
        warnings.append("快速思考模型不在当前模型列表中")
    if settings.get("deep_think_model") and model_options and settings.get("deep_think_model") not in model_options:
        warnings.append("深度思考模型不在当前模型列表中")
    if settings.get("verification_enabled") == "true":
        verification_options = _loads(settings.get("verification_model_options"), [])
        if not settings.get("verification_endpoint") or not settings.get("verification_api_key") or not verification_options:
            warnings.append("旁观者核对已启用，但核对模型配置不完整")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "config": {
            "name": settings.get("llm_name", ""),
            "base_url": settings.get("custom_endpoint", ""),
            "quick_model": settings.get("quick_think_model", ""),
            "deep_model": settings.get("deep_think_model", ""),
            "model_count": len(model_options),
            "verification_enabled": settings.get("verification_enabled") == "true",
        },
    }
