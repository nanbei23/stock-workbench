"""Cross-cutting enhancement features for settings, AI review, and risk panels."""

import asyncio
import json
import re
import uuid
from datetime import datetime, time

import httpx
from fastapi import HTTPException

from models.database import get_db
from repositories import portfolio_repository
from repositories import settings_repository
from services import ai_report_service, quote_service, settings_service
from data.market import get_market_sentiment
from data.signal import get_hot_reasons, get_industry_ranking


MODEL_PROVIDERS_KEY = "model_providers"
WORKER_POOL_KEY = "batch_worker_pool"
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


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


async def _safe_live(coro, fallback, timeout: float = 3.0):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except Exception:
        return fallback


def _clamp(value: float, low: float, high: float):
    return max(low, min(high, value))


def _breadth_usable(breadth: dict) -> bool:
    total = _safe_int(breadth.get("total"))
    counted = _safe_int(breadth.get("up")) + _safe_int(breadth.get("down")) + _safe_int(breadth.get("flat"))
    if total <= 0:
        return False
    return counted >= min(total * 0.5, max(800, total * 0.18))


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


def _normalize_worker_pool_worker(worker: dict, index: int) -> dict:
    provider_ids = [str(item).strip() for item in worker.get("provider_ids") or [] if str(item).strip()]
    model_tier = worker.get("model_tier") if worker.get("model_tier") in {"quick", "deep"} else "deep"
    worker_id = (worker.get("id") or worker.get("name") or f"worker-{index + 1}").strip()
    return {
        "id": re.sub(r"[^A-Za-z0-9_.-]+", "-", worker_id).strip("-") or f"worker-{index + 1}",
        "name": worker.get("name") or worker_id or f"Worker {index + 1}",
        "enabled": bool(worker.get("enabled", True)),
        "provider_ids": provider_ids,
        "model_tier": model_tier,
        "sleep_seconds": max(1, _safe_int(worker.get("sleep_seconds"), 5)),
        "stale_minutes": max(1, _safe_int(worker.get("stale_minutes"), 15)),
    }


def get_worker_pool_config():
    settings = _settings()
    workers = _loads(settings.get(WORKER_POOL_KEY), [])
    normalized = [_normalize_worker_pool_worker(worker, index) for index, worker in enumerate(workers) if isinstance(worker, dict)]
    return {"count": len(normalized), "workers": normalized}


def save_worker_pool_config(payload: dict):
    workers = payload.get("workers") if isinstance(payload, dict) else []
    normalized = [_normalize_worker_pool_worker(worker, index) for index, worker in enumerate(workers or []) if isinstance(worker, dict)]
    settings_repository.upsert_settings({WORKER_POOL_KEY: _dumps(normalized)})
    return {"status": "ok", "count": len(normalized), "workers": normalized}


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
    result = {"status": "ok", "provider": _public_provider(provider), "count": len(providers)}
    apply_to = payload.get("apply_to")
    if apply_to in {"ai", "verification"}:
        result["applied"] = apply_model_provider(provider_id, apply_to)
    return result


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
        "post_trigger_return_pct": round((last_price - first["price"]) / first["price"] * 100, 3) if first and first["price"] else None,
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
        positions.append({**row, "market_value_calc": round(value, 3), "bucket": (row.get("code") or "")[:2]})
    for item in positions:
        item["weight_pct"] = round(item["market_value_calc"] / total * 100, 3) if total else 0
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
        "total_market_value": round(total, 3),
        "accounts": list(accounts.values()),
        "positions": sorted(positions, key=lambda item: item["weight_pct"], reverse=True),
        "buckets": [{"bucket": k, "market_value": round(v, 3), "weight_pct": round(v / total * 100, 3) if total else 0} for k, v in sorted(buckets.items())],
        "warnings": warnings,
    }


async def risk_center():
    settings = _settings()
    risk = await risk_exposure()
    overview_rows = await _fetchall(
        """
        SELECT
          COALESCE(SUM(CASE WHEN key = 'cash_balance' THEN CAST(value AS REAL) ELSE 0 END), 0) AS default_cash,
          COALESCE(SUM(CASE WHEN key LIKE 'cash_balance_%' THEN CAST(value AS REAL) ELSE 0 END), 0) AS account_cash
        FROM settings
        WHERE key = 'cash_balance' OR key LIKE 'cash_balance_%'
        """
    )
    pnl_rows = await _fetchall(
        """
        SELECT date, total_pnl, total_assets
        FROM daily_pnl
        WHERE code6 = '' OR code6 IS NULL
        ORDER BY date DESC
        LIMIT 1
        """
    )
    pending_rows = await _fetchall(
        """
        SELECT code, name, action, shares, target_price, expires_at
        FROM conditional_orders
        WHERE status = 'pending'
        ORDER BY created_at DESC
        LIMIT 20
        """
    )
    stale_rows = await _fetchall(
        """
        SELECT code, name, updated_at
        FROM portfolio
        WHERE total_shares > 0
          AND (updated_at IS NULL OR updated_at < datetime('now', ?))
        ORDER BY updated_at ASC
        LIMIT 10
        """,
        (f"-{max(1, _safe_int(settings.get('risk_quote_stale_hours'), 24))} hours",),
    )

    thresholds = {
        "max_position_pct": _safe_float(settings.get("risk_max_position_pct"), 30),
        "max_bucket_pct": _safe_float(settings.get("risk_max_bucket_pct"), 45),
        "min_cash_pct": _safe_float(settings.get("risk_min_cash_pct"), 5),
        "daily_loss_pct": _safe_float(settings.get("risk_daily_loss_pct"), 3),
        "max_pending_order_amount": _safe_float(settings.get("risk_max_pending_order_amount"), 50000),
        "quote_stale_hours": _safe_int(settings.get("risk_quote_stale_hours"), 24),
    }
    cash = 0.0
    if overview_rows:
        row = overview_rows[0]
        cash = _safe_float(row.get("account_cash")) or _safe_float(row.get("default_cash"))
    market_value = _safe_float(risk.get("total_market_value"))
    total_assets = market_value + cash
    cash_pct = cash / total_assets * 100 if total_assets else 0

    checks = []
    top_position = (risk.get("positions") or [{}])[0] if risk.get("positions") else None
    top_weight = _safe_float(top_position.get("weight_pct")) if top_position else 0
    checks.append({
        "key": "position_concentration",
        "label": "单票集中度",
        "status": "warning" if top_weight > thresholds["max_position_pct"] else "ok",
        "value": round(top_weight, 3),
        "limit": thresholds["max_position_pct"],
        "message": f"{top_position.get('name') or top_position.get('code')} 仓位 {top_weight:.1f}%" if top_position else "暂无持仓",
    })

    top_bucket = max(risk.get("buckets") or [], key=lambda item: item.get("weight_pct", 0), default=None)
    bucket_weight = _safe_float(top_bucket.get("weight_pct")) if top_bucket else 0
    checks.append({
        "key": "bucket_concentration",
        "label": "主题集中度",
        "status": "warning" if bucket_weight > thresholds["max_bucket_pct"] else "ok",
        "value": round(bucket_weight, 3),
        "limit": thresholds["max_bucket_pct"],
        "message": f"代码段 {top_bucket.get('bucket')} 暴露 {bucket_weight:.1f}%" if top_bucket else "暂无暴露",
    })

    checks.append({
        "key": "cash_buffer",
        "label": "现金缓冲",
        "status": "warning" if total_assets and cash_pct < thresholds["min_cash_pct"] else "ok",
        "value": round(cash_pct, 3),
        "limit": thresholds["min_cash_pct"],
        "message": f"现金占比 {cash_pct:.1f}%",
    })

    latest_pnl = pnl_rows[0] if pnl_rows else {}
    daily_loss_pct = (
        abs(_safe_float(latest_pnl.get("total_pnl"))) / _safe_float(latest_pnl.get("total_assets")) * 100
        if _safe_float(latest_pnl.get("total_pnl")) < 0 and _safe_float(latest_pnl.get("total_assets")) else 0
    )
    checks.append({
        "key": "daily_loss",
        "label": "单日亏损线",
        "status": "warning" if daily_loss_pct > thresholds["daily_loss_pct"] else "ok",
        "value": round(daily_loss_pct, 3),
        "limit": thresholds["daily_loss_pct"],
        "message": f"{latest_pnl.get('date') or '最近'} 单日亏损 {daily_loss_pct:.1f}%",
    })

    oversize_orders = []
    for row in pending_rows:
        amount = _safe_float(row.get("shares")) * _safe_float(row.get("target_price"))
        if amount > thresholds["max_pending_order_amount"]:
            oversize_orders.append({**row, "amount": round(amount, 3)})
    checks.append({
        "key": "pending_order_amount",
        "label": "待执行计划金额",
        "status": "warning" if oversize_orders else "ok",
        "value": len(oversize_orders),
        "limit": thresholds["max_pending_order_amount"],
        "message": f"{len(oversize_orders)} 个待执行计划超过单笔金额上限",
        "details": oversize_orders[:5],
    })

    checks.append({
        "key": "quote_freshness",
        "label": "持仓价格新鲜度",
        "status": "warning" if stale_rows else "ok",
        "value": len(stale_rows),
        "limit": thresholds["quote_stale_hours"],
        "message": f"{len(stale_rows)} 只持仓超过 {thresholds['quote_stale_hours']} 小时未更新",
        "details": stale_rows,
    })

    warnings = [item["message"] for item in checks if item["status"] != "ok"]
    score = round(sum(1 for item in checks if item["status"] == "ok") / len(checks) * 100) if checks else 100
    return {
        "score": score,
        "ok": not warnings,
        "thresholds": thresholds,
        "summary": {
            "total_assets": round(total_assets, 3),
            "market_value": round(market_value, 3),
            "cash": round(cash, 3),
            "cash_pct": round(cash_pct, 3),
            "position_count": len(risk.get("positions", [])),
            "pending_order_count": len(pending_rows),
        },
        "checks": checks,
        "warnings": warnings,
        "exposure": risk,
    }


async def portfolio_professional_summary():
    risk = await risk_exposure()
    account_rows = await _fetchall(
        """
        SELECT a.id, a.name, a.broker,
               COUNT(p.code) AS position_count,
               COALESCE(SUM(CASE WHEN p.total_shares > 0 THEN p.market_value ELSE 0 END), 0) AS market_value,
               COALESCE(SUM(CASE WHEN p.total_shares > 0 THEN p.unrealized_pnl ELSE 0 END), 0) AS unrealized_pnl
        FROM accounts a
        LEFT JOIN portfolio p ON p.account_id = a.id
        GROUP BY a.id, a.name, a.broker
        ORDER BY market_value DESC
        """
    )
    strategy_rows = await _fetchall(
        """
        SELECT COALESCE(w.strategy_state, 'watch') AS strategy_state,
               COUNT(*) AS watch_count,
               COALESCE(SUM(CASE WHEN p.total_shares > 0 THEN p.market_value ELSE 0 END), 0) AS market_value
        FROM watchlist w
        LEFT JOIN portfolio p ON p.code = w.code
        GROUP BY COALESCE(w.strategy_state, 'watch')
        ORDER BY market_value DESC, watch_count DESC
        """
    )
    trade_rows = await _fetchall(
        """
        SELECT code, name,
               COUNT(*) AS trade_count,
               COALESCE(SUM(CASE WHEN direction = 'buy' THEN amount ELSE -amount END), 0) AS net_amount,
               MAX(trade_time) AS last_trade_at
        FROM trades
        GROUP BY code, name
        ORDER BY last_trade_at DESC
        LIMIT 12
        """
    )
    total = _safe_float(risk.get("total_market_value"))
    risk_accounts = {item.get("id"): item for item in risk.get("accounts", [])}
    for row in account_rows:
        if row.get("id") in risk_accounts:
            row["market_value"] = round(_safe_float(risk_accounts[row["id"]].get("market_value")), 3)
            row["position_count"] = risk_accounts[row["id"]].get("positions", row.get("position_count", 0))
        row["weight_pct"] = round(_safe_float(row.get("market_value")) / total * 100, 3) if total else 0
    return {
        "total_market_value": risk.get("total_market_value", 0),
        "accounts": account_rows,
        "strategy_exposure": strategy_rows,
        "top_positions": (risk.get("positions") or [])[:10],
        "buckets": risk.get("buckets", []),
        "recent_trade_activity": trade_rows,
    }


async def notification_digest():
    settings = _settings()
    recent = settings_service.poll_notifications().get("notifications", [])
    rows = await _fetchall(
        """
        SELECT type, COUNT(*) AS count
        FROM (
          SELECT 'order_trigger' AS type FROM conditional_orders WHERE status = 'triggered' AND triggered_at > datetime('now', '-1 day')
          UNION ALL
          SELECT 'analysis_done' AS type FROM analysis_reports WHERE created_at > datetime('now', '-1 day')
          UNION ALL
          SELECT 'strategy_change' AS type FROM watchlist WHERE strategy_state_updated_at IS NOT NULL AND strategy_state_updated_at > datetime('now', '-1 day')
          UNION ALL
          SELECT 'anomaly' AS type FROM anomaly_logs WHERE created_at > datetime('now', '-1 day')
        )
        GROUP BY type
        """
    )
    by_type = {row["type"]: row["count"] for row in rows}
    enabled = {
        "browser": settings.get("browser_notify_enabled") == "true",
        "digest": settings.get("notification_digest_enabled", "true") == "true",
        "strategy_change": settings.get("notify_strategy_change", "true") == "true",
        "order_trigger": settings.get("notify_order_trigger", "true") == "true",
        "anomaly": settings.get("notify_anomaly", "true") == "true",
        "analysis_done": settings.get("notify_analysis_done", "true") == "true",
    }
    missing = [key for key, value in enabled.items() if key != "browser" and not value]
    return {
        "enabled": enabled,
        "disabled_channels": missing,
        "recent": recent[:8],
        "by_type_24h": by_type,
        "count_24h": sum(by_type.values()),
    }


async def data_freshness():
    rows = await _fetchall(
        """
        SELECT
          (SELECT MAX(updated_at) FROM portfolio WHERE total_shares > 0) AS portfolio_updated_at,
          (SELECT MAX(trade_time) FROM trades) AS last_trade_at,
          (SELECT MAX(created_at) FROM analysis_reports) AS last_report_at,
          (SELECT MAX(cached_at) FROM news_cache) AS last_news_at,
          (SELECT MAX(created_at) FROM anomaly_logs) AS last_anomaly_at
        """
    )
    data = rows[0] if rows else {}
    now = datetime.now()
    items = []
    labels = {
        "portfolio_updated_at": ("持仓估值", 24),
        "last_trade_at": ("交易流水", 14 * 24),
        "last_report_at": ("AI报告", 7 * 24),
        "last_news_at": ("新闻缓存", 24),
        "last_anomaly_at": ("异动日志", 24),
    }
    for key, (label, max_hours) in labels.items():
        raw = data.get(key)
        age_hours = None
        status = "warning"
        if raw:
            parsed = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    parsed = datetime.strptime(str(raw).split(".")[0], fmt)
                    break
                except ValueError:
                    continue
            if parsed:
                age_hours = round((now - parsed).total_seconds() / 3600, 1)
                status = "ok" if age_hours <= max_hours else "warning"
        items.append({"key": key, "label": label, "time": raw, "age_hours": age_hours, "status": status, "max_hours": max_hours})
    return {"items": items, "ok": all(item["status"] == "ok" for item in items)}


async def operations_dashboard():
    audit, risk, portfolio, quality, backup, diagnostics, freshness, notifications, event_data = await asyncio.gather(
        data_audit(),
        risk_center(),
        portfolio_professional_summary(),
        ai_report_service.get_quality_summary(limit=80),
        asyncio.to_thread(settings_service.migration_status),
        system_diagnostics(),
        data_freshness(),
        notification_digest(),
        events(),
    )
    scores = [
        _safe_float(audit.get("score")),
        _safe_float(risk.get("score")),
        100 if freshness.get("ok") else 70,
        100 if backup.get("migrations", {}).get("up_to_date") else 70,
        100 if diagnostics.get("summary", {}).get("warning_count", 0) == 0 else 75,
    ]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "score": round(sum(scores) / len(scores)) if scores else 0,
        "data_trust": {"audit": audit, "freshness": freshness},
        "portfolio": portfolio,
        "risk": risk,
        "ai_quality": quality,
        "release_ops": backup,
        "notifications": notifications,
        "events": event_data,
        "diagnostics": diagnostics,
    }


def _contains_any(text: str, keywords: list[str]) -> bool:
    haystack = (text or "").lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def _hotspot_taxonomy():
    return [
        {"name": "AI算力", "keywords": ["ai", "人工智能", "算力", "大模型", "芯片", "服务器", "gpu", "英伟达", "机器人"]},
        {"name": "半导体", "keywords": ["半导体", "芯片", "晶圆", "封测", "光刻", "存储", "先进封装"]},
        {"name": "新能源", "keywords": ["新能源", "光伏", "储能", "电池", "锂电", "风电", "氢能"]},
        {"name": "智能汽车", "keywords": ["汽车", "智驾", "无人驾驶", "车联网", "整车", "零部件"]},
        {"name": "医药健康", "keywords": ["医药", "创新药", "医疗", "器械", "cro", "生物", "疫苗"]},
        {"name": "消费复苏", "keywords": ["消费", "食品", "白酒", "旅游", "酒店", "零售", "家电"]},
        {"name": "金融地产", "keywords": ["银行", "券商", "保险", "地产", "房地产", "物业"]},
        {"name": "高端制造", "keywords": ["制造", "工业母机", "军工", "航天", "机器人", "设备"]},
    ]


def _phase_status(now_time: time, start: time, end: time) -> str:
    if start <= now_time <= end:
        return "active"
    if now_time > end:
        return "done"
    return "upcoming"


def _topic_seed(name: str):
    return {
        "name": name,
        "heat_score": 0.0,
        "trend_direction": "flat",
        "reason_parts": [],
        "news_count": 0,
        "stock_count": 0,
        "related_stocks": [],
        "signals": [],
        "source_tags": [],
        "score_components": {},
        "updated_at": None,
        "market_metrics": {},
    }


def _add_stock_once(topic: dict, stock: dict):
    code = stock.get("code") or stock.get("stock_code") or ""
    name = stock.get("name") or stock.get("stock_name") or code
    if not code and not name:
        return
    existing = {(item.get("code"), item.get("name")) for item in topic["related_stocks"]}
    key = (code, name)
    if key in existing:
        return
    topic["related_stocks"].append({
        "code": code,
        "name": name,
        "strategy_state": stock.get("strategy_state") or stock.get("reason") or "market",
        "holding": bool(stock.get("holding")),
        "unrealized_pnl_pct": stock.get("unrealized_pnl_pct"),
        "has_anomaly": bool(stock.get("has_anomaly")),
        "change_pct": stock.get("change_pct"),
        "source": stock.get("source", ""),
    })


def _score_topic(topic: dict, amount: float, component: str, source: str, reason: str | None = None):
    topic["heat_score"] += amount
    topic["score_components"][component] = round(topic["score_components"].get(component, 0) + amount, 3)
    if source and source not in topic["source_tags"]:
        topic["source_tags"].append(source)
    if reason and reason not in topic["reason_parts"]:
        topic["reason_parts"].append(reason)


async def market_regime():
    risk, freshness, tasks, indices, sentiment, industries = await asyncio.gather(
        risk_center(),
        data_freshness(),
        task_metrics(),
        _safe_live(quote_service.get_indices(), {}, timeout=3),
        _safe_live(get_market_sentiment(), {"breadth": {}, "northbound": {}}, timeout=3),
        _safe_live(get_industry_ranking(), [], timeout=3),
    )
    pnl_rows = await _fetchall(
        """
        SELECT date, total_pnl_pct, total_pnl, total_assets
        FROM daily_pnl
        WHERE code6 = '' OR code6 IS NULL
        ORDER BY date DESC
        LIMIT 5
        """
    )
    latest = pnl_rows[0] if pnl_rows else {}
    pnl_values = [_safe_float(row.get("total_pnl_pct")) for row in pnl_rows if row.get("total_pnl_pct") is not None]
    avg_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else 0
    risk_score = _safe_float(risk.get("score"), 70)
    freshness_score = 100 if freshness.get("ok") else 70
    task_penalty = min(12, _safe_int(tasks.get("by_status", {}).get("failed"), 0) * 3)
    index_items = [item for item in (indices or {}).values() if isinstance(item, dict) and item.get("change_pct") is not None]
    avg_index_pct = sum(_safe_float(item.get("change_pct")) for item in index_items) / len(index_items) if index_items else 0
    positive_index_ratio = sum(1 for item in index_items if _safe_float(item.get("change_pct")) >= 0) / len(index_items) if index_items else 0.5
    raw_breadth = (sentiment or {}).get("breadth") or {}
    breadth_valid = _breadth_usable(raw_breadth)
    breadth = raw_breadth if breadth_valid else {}
    total = _safe_int(breadth.get("total"))
    up = _safe_int(breadth.get("up"))
    down = _safe_int(breadth.get("down"))
    limit_up = _safe_int(breadth.get("limit_up"))
    limit_down = _safe_int(breadth.get("limit_down"))
    breadth_balance = (up - down) / total if total else 0
    limit_balance = (limit_up - limit_down) / total if total else 0
    northbound = (sentiment or {}).get("northbound") or {}
    north_net = _safe_float(northbound.get("total_net"))
    industry_sample = [row for row in (industries or [])[:10] if isinstance(row, dict)]
    industry_avg = sum(_safe_float(row.get("change_pct")) for row in industry_sample) / len(industry_sample) if industry_sample else 0
    industry_positive_ratio = sum(1 for row in industry_sample if _safe_float(row.get("change_pct")) >= 0) / len(industry_sample) if industry_sample else 0.5
    live_source_count = sum([bool(index_items), breadth_valid, bool(northbound), bool(industry_sample)])
    score = round(_clamp(
        50
        + avg_index_pct * 5
        + (positive_index_ratio - 0.5) * 12
        + breadth_balance * 26
        + limit_balance * 80
        + _clamp(north_net / 10, -8, 8)
        + industry_avg * 3
        + (industry_positive_ratio - 0.5) * 8
        + avg_pnl * 2
        + (risk_score - 70) * 0.18
        + (freshness_score - 70) * 0.08
        - task_penalty,
        0,
        100,
    ))
    if score >= 72:
        regime = "risk_on"
        label = "进攻"
        action_bias = "可围绕主线分批推进，仍需控制单票集中度。"
    elif score >= 48:
        regime = "balanced"
        label = "均衡"
        action_bias = "维持观察和小步试错，优先等待确认信号。"
    else:
        regime = "risk_off"
        label = "防守"
        action_bias = "降低追高动作，优先复核仓位、现金和止损纪律。"
    notes = []
    if index_items:
        notes.append(f"主要指数平均涨跌幅 {avg_index_pct:.2f}% ，上涨指数占比 {positive_index_ratio * 100:.0f}%。")
    if total:
        notes.append(f"全市场上涨 {up} 家、下跌 {down} 家，涨停 {limit_up} 家、跌停 {limit_down} 家。")
    elif raw_breadth:
        notes.append("市场宽度源返回样本不完整，本次未纳入市场状态评分。")
    if northbound:
        notes.append(f"北向资金净流入 {north_net:.2f} 亿元。")
    if industry_sample:
        top = industry_sample[0]
        notes.append(f"行业领涨：{top.get('name') or '--'} {round(_safe_float(top.get('change_pct')), 3)}%。")
    notes.extend(risk.get("warnings", [])[:3])
    if not freshness.get("ok"):
        notes.append("部分核心数据超过新鲜度阈值，盘中决策前建议先刷新。")
    if tasks.get("recent_failures"):
        notes.append("最近 AI 任务存在失败记录，报告结论需要复核。")
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "regime": regime,
        "label": label,
        "score": score,
        "action_bias": action_bias,
        "position_guidance": {
            "cash_pct": risk.get("summary", {}).get("cash_pct", 0),
            "position_count": risk.get("summary", {}).get("position_count", 0),
            "risk_score": risk_score,
        },
        "latest_pnl": latest,
        "market_breadth": breadth,
        "raw_market_breadth": raw_breadth,
        "northbound": northbound,
        "indices": indices,
        "top_industries": industry_sample[:6],
        "source_summary": {
            "mode": "live_market_plus_local_risk" if live_source_count else "local_risk_fallback",
            "live_source_count": live_source_count,
            "sources": [
                {"name": "腾讯指数行情", "status": "ok" if index_items else "fallback"},
                {"name": "新浪/东方财富涨跌家数", "status": "ok" if breadth_valid else "invalid" if raw_breadth else "fallback"},
                {"name": "东方财富北向资金", "status": "ok" if northbound else "fallback"},
                {"name": "东方财富行业板块", "status": "ok" if industry_sample else "fallback"},
                {"name": "本地风控与组合数据", "status": "ok"},
            ],
            "reliability": round(55 + live_source_count * 10 + (10 if freshness.get("ok") else 0)),
        },
        "notes": notes[:5],
    }


async def market_hotspots(limit: int = 12):
    news_rows, watch_rows, anomaly_rows, live_hot_rows, industry_rows, sentiment = await asyncio.gather(
        _fetchall(
            """
            SELECT code6, source, title, content, sentiment, published_at, cached_at
            FROM news_cache
            ORDER BY COALESCE(published_at, cached_at) DESC
            LIMIT 120
            """
        ),
        _fetchall(
            """
            SELECT w.code, w.name, w.group_name, w.strategy_state,
                   COALESCE(p.total_shares, 0) AS total_shares,
                   COALESCE(p.unrealized_pnl_pct, 0) AS unrealized_pnl_pct
            FROM watchlist w
            LEFT JOIN portfolio p ON p.code = w.code
            ORDER BY w.sort_order ASC, w.added_at DESC
            LIMIT 200
            """
        ),
        _fetchall(
            """
            SELECT code, name, anomaly_type, severity, created_at
            FROM anomaly_logs
            WHERE created_at > datetime('now', '-7 days')
            ORDER BY created_at DESC
            LIMIT 120
            """
        ),
        _safe_live(get_hot_reasons(), [], timeout=3),
        _safe_live(get_industry_ranking(), [], timeout=3),
        _safe_live(get_market_sentiment(), {"breadth": {}, "northbound": {}}, timeout=3),
    )
    topic_map: dict[str, dict] = {}
    def topic_for(name: str):
        key = name or "未分类热点"
        if key not in topic_map:
            topic_map[key] = _topic_seed(key)
        return topic_map[key]

    anomaly_codes = {row.get("code") for row in anomaly_rows if row.get("code")}
    for row in (industry_rows or [])[:18]:
        name = row.get("name") or row.get("code") or ""
        if not name:
            continue
        change = _safe_float(row.get("change_pct"))
        up_count = _safe_int(row.get("up_count"))
        down_count = _safe_int(row.get("down_count"))
        lead_change = _safe_float(row.get("lead_change_pct"))
        topic = topic_for(name)
        score = 34 + max(0, change) * 5 + max(0, lead_change) * 0.6 + max(0, up_count - down_count) * 0.08
        _score_topic(topic, score, "industry_strength", "东方财富行业板块", f"{name} 板块涨跌幅 {change:.2f}%")
        topic["trend_direction"] = "up" if change > 0 else "down" if change < 0 else "flat"
        topic["market_metrics"] = {
            "change_pct": round(change, 3),
            "up_count": up_count,
            "down_count": down_count,
            "lead_stock": row.get("lead_stock") or "",
            "lead_change_pct": round(lead_change, 3),
        }
        if row.get("lead_stock"):
            _add_stock_once(topic, {
                "name": row.get("lead_stock"),
                "code": "",
                "change_pct": lead_change,
                "source": "industry_lead_stock",
            })

    if live_hot_rows:
        hot_topic = topic_for("实时热榜")
        for idx, row in enumerate(live_hot_rows[:20]):
            hot_value = _safe_float(row.get("hot_value"))
            change = _safe_float(row.get("change_pct"))
            score = max(4, 22 - idx) + _clamp(hot_value / 100000, 0, 12) + max(0, change) * 0.8
            _score_topic(hot_topic, score, "hot_rank", "同花顺/东方财富热榜", f"{row.get('name') or row.get('code')} 热度排名 {idx + 1}")
            _add_stock_once(hot_topic, {
                "code": row.get("code"),
                "name": row.get("name"),
                "change_pct": change,
                "source": "hot_rank",
            })
        hot_topic["trend_direction"] = "up" if sum(1 for row in live_hot_rows[:20] if _safe_float(row.get("change_pct")) > 0) >= 10 else "flat"

    for spec in _hotspot_taxonomy():
        topic = topic_for(spec["name"])
        related_news = []
        related_codes = set()
        for row in news_rows:
            text = f"{row.get('title') or ''} {row.get('content') or ''}"
            if _contains_any(text, spec["keywords"]):
                related_news.append(row)
                if row.get("code6"):
                    related_codes.add(row["code6"])
        related_stocks = []
        for row in watch_rows:
            stock_text = f"{row.get('name') or ''} {row.get('group_name') or ''}"
            code = row.get("code")
            if code in related_codes or _contains_any(stock_text, spec["keywords"]):
                related_stocks.append({
                    "code": code,
                    "name": row.get("name") or code,
                    "strategy_state": row.get("strategy_state") or "watch",
                    "holding": _safe_float(row.get("total_shares")) > 0,
                    "unrealized_pnl_pct": round(_safe_float(row.get("unrealized_pnl_pct")), 3),
                    "has_anomaly": code in anomaly_codes,
                    "source": "local_watchlist",
                })
        sentiment_boost = sum(1 for row in related_news if row.get("sentiment") == "positive") - sum(1 for row in related_news if row.get("sentiment") == "negative")
        anomaly_boost = sum(1 for row in related_stocks if row.get("has_anomaly"))
        heat_score = len(related_news) * 9 + len(related_stocks) * 7 + max(0, sentiment_boost) * 5 + anomaly_boost * 8
        if heat_score <= 0 and not topic["source_tags"]:
            continue
        if heat_score > 0:
            _score_topic(topic, heat_score, "local_news_watchlist", "本地新闻/自选/异动", f"{len(related_news)} 条相关新闻，{len(related_stocks)} 只自选/持仓关联标的")
            topic["news_count"] += len(related_news)
            topic["signals"].extend([row.get("title") for row in related_news[:4] if row.get("title")])
            for stock in related_stocks:
                _add_stock_once(topic, stock)
            topic["updated_at"] = (related_news[0].get("published_at") or related_news[0].get("cached_at")) if related_news else topic["updated_at"]
        trend_direction = "up" if sentiment_boost > 0 or anomaly_boost else "flat"
        if sentiment_boost < 0:
            trend_direction = "down"
        if topic["trend_direction"] == "flat":
            topic["trend_direction"] = trend_direction

    if not topic_map and watch_rows:
        grouped: dict[str, list[dict]] = {}
        for row in watch_rows:
            group = row.get("group_name") or "自选观察"
            grouped.setdefault(group, []).append(row)
        for group, rows in grouped.items():
            topic = topic_for(group)
            _score_topic(topic, min(70, 20 + len(rows) * 6), "local_watchlist_fallback", "本地自选分组", f"来自自选分组，{len(rows)} 只股票待跟踪")
            for row in rows[:8]:
                _add_stock_once(topic, {"code": row.get("code"), "name": row.get("name") or row.get("code"), "strategy_state": row.get("strategy_state") or "watch", "source": "watchlist_fallback"})
    topics = []
    for topic in topic_map.values():
        if topic["heat_score"] <= 0:
            continue
        topic["related_stocks"] = topic["related_stocks"][:8]
        topic["stock_count"] = len(topic["related_stocks"])
        topic["signals"] = topic["signals"][:5]
        topic["heat_score"] = round(min(100, topic["heat_score"]), 1)
        topic["reason"] = topic["reason_parts"][0] if topic["reason_parts"] else "实时市场和本地研究数据共同触发"
        topic["source_count"] = len(topic["source_tags"])
        topic["reliability"] = round(min(95, 42 + topic["source_count"] * 14 + min(20, topic["news_count"] * 2) + (10 if topic["market_metrics"] else 0)))
        topics.append(topic)
    topics.sort(key=lambda item: (item["heat_score"], item["news_count"], item["stock_count"]), reverse=True)
    raw_breadth = (sentiment or {}).get("breadth") or {}
    breadth_valid = _breadth_usable(raw_breadth)
    breadth = raw_breadth if breadth_valid else {}
    live_source_count = sum([bool(industry_rows), bool(live_hot_rows), breadth_valid])
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(topics),
        "topics": topics[: max(1, min(limit, 30))],
        "source_summary": {
            "mode": "live_market_plus_local_research" if live_source_count else "local_research_fallback",
            "live_source_count": live_source_count,
            "sources": [
                {"name": "东方财富行业板块", "status": "ok" if industry_rows else "fallback"},
                {"name": "同花顺/东方财富热榜", "status": "ok" if live_hot_rows else "fallback"},
                {"name": "新浪/东方财富市场宽度", "status": "ok" if breadth_valid else "invalid" if raw_breadth else "fallback"},
                {"name": "本地新闻缓存", "status": "ok" if news_rows else "empty"},
                {"name": "本地自选/持仓/异动", "status": "ok" if watch_rows or anomaly_rows else "empty"},
            ],
            "reliability": round(50 + live_source_count * 12 + (8 if news_rows else 0) + (8 if watch_rows or anomaly_rows else 0)),
        },
        "market_context": {"breadth": breadth, "raw_breadth": raw_breadth, "northbound": (sentiment or {}).get("northbound") or {}},
    }


async def hotspot_detail(topic_name: str):
    hotspots = await market_hotspots(limit=30)
    topic = next((item for item in hotspots.get("topics", []) if item.get("name") == topic_name), None)
    if not topic:
        raise HTTPException(404, "热点主题不存在")
    spec = next((item for item in _hotspot_taxonomy() if item["name"] == topic_name), {"keywords": [topic_name]})
    news_rows = await _fetchall(
        """
        SELECT code6, source, title, content, url, sentiment, published_at, cached_at
        FROM news_cache
        ORDER BY COALESCE(published_at, cached_at) DESC
        LIMIT 80
        """
    )
    related_news = [
        row for row in news_rows
        if _contains_any(f"{row.get('title') or ''} {row.get('content') or ''}", spec["keywords"])
    ][:12]
    trend_rows = await _fetchall(
        """
        SELECT date(cached_at) AS day, COUNT(*) AS count
        FROM news_cache
        WHERE cached_at > datetime('now', '-14 days')
        GROUP BY date(cached_at)
        ORDER BY day ASC
        """
    )
    return {
        "topic": topic,
        "related_news": related_news,
        "trend": trend_rows,
        "playbook": [
            "先确认主题是否同时有新闻催化、异动和持仓/自选关联。",
            "只把高热度作为候选池，不直接替代买卖纪律。",
            "若主题升温但风控分下降，优先降低单票仓位和条件单金额。",
        ],
    }


def research_pulse():
    now = datetime.now()
    current = now.time()
    phases = [
        {"key": "pre_market", "label": "盘前准备", "time": "09:00-09:25", "status": _phase_status(current, time(9, 0), time(9, 25)), "focus": "热点预热、隔夜消息、条件单复核"},
        {"key": "morning", "label": "早盘确认", "time": "09:30-11:30", "status": _phase_status(current, time(9, 30), time(11, 30)), "focus": "量价确认、异动股票、主线强弱"},
        {"key": "midday", "label": "午间复盘", "time": "11:30-13:00", "status": _phase_status(current, time(11, 30), time(13, 0)), "focus": "更新 AI 报告、复核风险、梳理下午计划"},
        {"key": "afternoon", "label": "尾盘执行", "time": "13:00-15:00", "status": _phase_status(current, time(13, 0), time(15, 0)), "focus": "条件单确认、仓位微调、收盘前纪律"},
        {"key": "post_market", "label": "收盘归档", "time": "15:00-17:30", "status": _phase_status(current, time(15, 0), time(17, 30)), "focus": "记录交易、生成复盘、更新策略生命周期"},
    ]
    active = next((item for item in phases if item["status"] == "active"), phases[-1] if current > time(17, 30) else phases[0])
    return {"generated_at": now.isoformat(timespec="seconds"), "active": active, "phases": phases}


async def strategy_lifecycle():
    portfolio_rows, watch_rows, plan_rows, order_rows, signal_rows = await asyncio.gather(
        _fetchall(
            """
            SELECT code, name, total_shares, market_value, unrealized_pnl_pct, updated_at
            FROM portfolio
            WHERE total_shares > 0
            ORDER BY market_value DESC
            LIMIT 80
            """
        ),
        _fetchall(
            """
            SELECT w.code, w.name, w.strategy_state, w.group_name, w.strategy_state_updated_at, p.code AS holding_code
            FROM watchlist w
            LEFT JOIN portfolio p ON p.code = w.code AND p.total_shares > 0
            WHERE p.code IS NULL
            ORDER BY w.sort_order ASC, w.added_at DESC
            LIMIT 80
            """
        ),
        _fetchall(
            """
            SELECT code, name, direction, plan_type, target_price, plan_shares, status, reason, created_at, expires_at
            FROM trading_plans
            ORDER BY created_at DESC
            LIMIT 80
            """
        ),
        _fetchall(
            """
            SELECT code, name, action, condition_type, target_price, shares, status, created_at, expires_at
            FROM conditional_orders
            ORDER BY created_at DESC
            LIMIT 80
            """
        ),
        _fetchall(
            """
            SELECT code, name, signal, entry_price, exit_price, pnl_pct, status, signal_date, exit_date, updated_at
            FROM signal_tracking
            ORDER BY updated_at DESC
            LIMIT 80
            """
        ),
    )
    columns = {
        "watching": {"label": "观察池", "items": []},
        "planned": {"label": "待执行", "items": []},
        "holding": {"label": "持仓中", "items": []},
        "weakening": {"label": "走弱复核", "items": []},
        "invalidated": {"label": "失效归档", "items": []},
        "exited": {"label": "已退出", "items": []},
    }
    for row in watch_rows[:20]:
        columns["watching"]["items"].append({
            "code": row.get("code"),
            "name": row.get("name") or row.get("code"),
            "source": row.get("group_name") or "watchlist",
            "detail": f"策略状态：{row.get('strategy_state') or 'watch'}",
            "updated_at": row.get("strategy_state_updated_at"),
        })
    for row in plan_rows:
        item = {
            "code": row.get("code"),
            "name": row.get("name") or row.get("code"),
            "source": "trading_plan",
            "detail": f"{row.get('direction')} {row.get('plan_shares') or 0} 股，目标 {row.get('target_price') or '--'}",
            "updated_at": row.get("created_at"),
        }
        if row.get("status") in ("cancelled", "expired"):
            columns["invalidated"]["items"].append(item)
        elif row.get("status") in ("filled", "triggered"):
            columns["holding"]["items"].append(item)
        else:
            columns["planned"]["items"].append(item)
    for row in order_rows:
        item = {
            "code": row.get("code"),
            "name": row.get("name") or row.get("code"),
            "source": "conditional_order",
            "detail": f"{row.get('action')} 条件 {row.get('condition_type')} {row.get('target_price')}",
            "updated_at": row.get("created_at"),
        }
        if row.get("status") in ("cancelled", "expired"):
            columns["invalidated"]["items"].append(item)
        elif row.get("status") == "triggered":
            columns["holding"]["items"].append(item)
        else:
            columns["planned"]["items"].append(item)
    for row in portfolio_rows:
        item = {
            "code": row.get("code"),
            "name": row.get("name") or row.get("code"),
            "source": "portfolio",
            "detail": f"{row.get('total_shares') or 0} 股，浮盈亏 {round(_safe_float(row.get('unrealized_pnl_pct')), 3)}%",
            "updated_at": row.get("updated_at"),
        }
        if _safe_float(row.get("unrealized_pnl_pct")) <= -5:
            columns["weakening"]["items"].append(item)
        else:
            columns["holding"]["items"].append(item)
    for row in signal_rows:
        item = {
            "code": row.get("code"),
            "name": row.get("name") or row.get("code"),
            "source": "signal_tracking",
            "detail": f"{row.get('signal')} · 后验收益 {round(_safe_float(row.get('pnl_pct')), 3)}%",
            "updated_at": row.get("updated_at") or row.get("exit_date") or row.get("signal_date"),
        }
        if row.get("status") == "closed":
            columns["exited"]["items"].append(item)
        elif row.get("status") == "invalidated":
            columns["invalidated"]["items"].append(item)
    result = []
    for key, data in columns.items():
        items = data["items"][:12]
        result.append({"key": key, "label": data["label"], "count": len(data["items"]), "items": items})
    return {"generated_at": datetime.now().isoformat(timespec="seconds"), "columns": result}


async def research_progress():
    tasks, stages = await asyncio.gather(
        _fetchall(
            """
            SELECT task_id, code, name, status, queue_status, depth, stages, error, elapsed, created_at, updated_at
            FROM analysis_tasks
            ORDER BY updated_at DESC
            LIMIT 8
            """
        ),
        _fetchall(
            """
            SELECT task_id, code, stage_id, completed_at
            FROM analysis_progress
            ORDER BY completed_at DESC
            LIMIT 80
            """
        ),
    )
    by_task: dict[str, list[dict]] = {}
    for row in stages:
        by_task.setdefault(row.get("task_id"), []).append(row)
    items = []
    for row in tasks:
        stage_rows = by_task.get(row.get("task_id"), [])
        stage_payload = _loads(row.get("stages"), {})
        total = len(stage_payload) if isinstance(stage_payload, dict) and stage_payload else max(len(stage_rows), 0)
        completed = len(stage_rows) if stage_rows else sum(1 for item in (stage_payload or {}).values() if isinstance(item, dict) and item.get("status") == "done")
        items.append({
            "task_id": row.get("task_id"),
            "code": row.get("code"),
            "name": row.get("name") or row.get("code"),
            "status": row.get("queue_status") or row.get("status"),
            "depth": row.get("depth") or "standard",
            "completed": completed,
            "total": total,
            "progress_pct": round(completed / total * 100) if total else 0,
            "elapsed": row.get("elapsed"),
            "error": row.get("error"),
            "updated_at": row.get("updated_at"),
            "latest_stage": stage_rows[0].get("stage_id") if stage_rows else None,
        })
    active = [item for item in items if item.get("status") in ("queued", "running", "pending")]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "active_count": len(active),
        "items": items,
        "stream_endpoint_pattern": "/api/ai/analyze/{task_id}/stream",
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
    mismatches = await _portfolio_mismatches()
    checks.append({
        "key": "portfolio_consistency",
        "label": "持仓一致性",
        "status": "warning" if mismatches else "ok",
        "message": f"{len(mismatches)} 只持仓与交易流水不一致",
        "details": mismatches[:8],
        "fixable": bool(mismatches),
    })
    invalid_accounts = await _invalid_account_refs()
    checks.append({
        "key": "account_refs",
        "label": "账户引用",
        "status": "warning" if invalid_accounts else "ok",
        "message": f"{len(invalid_accounts)} 处账户引用不存在",
        "details": invalid_accounts[:8],
        "fixable": bool(invalid_accounts),
    })
    cash_gaps = await _cash_ledger_gaps(settings)
    checks.append({
        "key": "cash_ledger",
        "label": "现金流水",
        "status": "warning" if cash_gaps else "ok",
        "message": f"{len(cash_gaps)} 个账户现金缺少流水或余额不一致",
        "details": cash_gaps[:8],
        "fixable": bool(cash_gaps),
    })
    ok_count = sum(1 for item in checks if item["status"] == "ok")
    score = round(ok_count / len(checks) * 100) if checks else 100
    return {"checks": checks, "ok": ok_count == len(checks), "score": score}


async def data_audit():
    health = await data_health()
    overview = await _fetchall(
        """
        SELECT
          (SELECT COUNT(*) FROM accounts) AS account_count,
          (SELECT COUNT(*) FROM watchlist) AS watchlist_count,
          (SELECT COUNT(*) FROM portfolio WHERE total_shares > 0) AS position_count,
          (SELECT COUNT(*) FROM trades) AS trade_count,
          (SELECT COUNT(*) FROM conditional_orders WHERE status = 'pending') AS pending_order_count,
          (SELECT COUNT(*) FROM cash_ledger) AS cash_ledger_count,
          (SELECT COUNT(*) FROM analysis_reports) AS report_count,
          (SELECT COUNT(*) FROM hermes_tool_runs WHERE status = 'ok') AS hermes_write_count
        """
    )
    stale_reports = await _fetchall(
        """
        SELECT code, MAX(created_at) AS last_report_at
        FROM analysis_reports
        GROUP BY code
        HAVING last_report_at < datetime('now', '-7 days')
        ORDER BY last_report_at ASC
        LIMIT 8
        """
    )
    orphan_signals = await _fetchall(
        """
        SELECT COUNT(*) AS c
        FROM signal_tracking st
        LEFT JOIN analysis_reports ar ON ar.id = st.report_id
        WHERE st.report_id IS NOT NULL AND st.report_id > 0 AND ar.id IS NULL
        """
    )
    fixable = [
        item for item in health.get("checks", [])
        if item.get("fixable") and item.get("status") != "ok"
    ]
    warnings = [
        item.get("message")
        for item in health.get("checks", [])
        if item.get("status") != "ok"
    ]
    if stale_reports:
        warnings.append(f"{len(stale_reports)} 只股票的最近 AI 报告超过 7 天")
    orphan_count = int(orphan_signals[0]["c"] if orphan_signals else 0)
    if orphan_count:
        warnings.append(f"{orphan_count} 条信号跟踪缺少来源报告")

    return {
        "score": health.get("score", 0),
        "ok": health.get("ok", False) and not stale_reports and not orphan_count,
        "summary": overview[0] if overview else {},
        "fixable_count": len(fixable),
        "warning_count": len(warnings),
        "warnings": warnings,
        "stale_reports": stale_reports,
        "orphan_signal_tracking": orphan_count,
        "checks": health.get("checks", []),
    }


async def fix_data_health():
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE conditional_orders SET status='expired' WHERE status='pending' AND expires_at IS NOT NULL AND expires_at < datetime('now')"
        )
        expired_orders = cursor.rowcount
        account_fixes = 0
        for table in ("portfolio", "trades", "conditional_orders"):
            cursor = await db.execute(
                f"""
                UPDATE {table}
                SET account_id = 'default'
                WHERE account_id IS NULL
                   OR account_id = ''
                   OR account_id NOT IN (SELECT id FROM accounts)
                """
            )
            account_fixes += cursor.rowcount
        trade_codes = await db.execute_fetchall("SELECT DISTINCT code FROM trades")
        recalculated = 0
        for row in trade_codes:
            await portfolio_repository.recalc_portfolio(db, row["code"])
            recalculated += 1
        await db.commit()
        return {
            "status": "ok",
            "expired_orders": expired_orders,
            "account_refs_fixed": account_fixes,
            "portfolio_recalculated": recalculated,
        }
    finally:
        await db.close()


async def _portfolio_mismatches():
    trade_rows = await _fetchall(
        """
        SELECT code, name, direction, price, shares, amount, commission, stamp_tax, transfer_fee
        FROM trades
        ORDER BY trade_time ASC
        """
    )
    expected: dict[str, dict] = {}
    for row in trade_rows:
        code = row["code"]
        item = expected.setdefault(code, {"code": code, "name": row.get("name") or code, "shares": 0, "cost": 0.0})
        shares = _safe_float(row.get("shares"))
        if row.get("direction") == "buy":
            item["shares"] += shares
            item["cost"] += float(row.get("amount") or 0) + float(row.get("commission") or 0) + float(row.get("stamp_tax") or 0) + float(row.get("transfer_fee") or 0)
        elif row.get("direction") == "sell" and item["shares"] > 0:
            avg_before = item["cost"] / item["shares"]
            item["shares"] = max(0, item["shares"] - shares)
            item["cost"] = avg_before * item["shares"]

    portfolio_rows = await _fetchall("SELECT code, name, total_shares, avg_cost FROM portfolio")
    actual = {row["code"]: row for row in portfolio_rows}
    mismatches = []
    for code, item in expected.items():
        expected_shares = round(_safe_float(item["shares"]), 3)
        expected_cost = round(item["cost"] / expected_shares, 3) if expected_shares else 0
        row = actual.get(code)
        actual_shares = round(_safe_float(row.get("total_shares")), 3) if row else 0
        actual_cost = round(float(row.get("avg_cost") or 0), 3) if row else 0
        if abs(expected_shares - actual_shares) > 0.001 or abs(expected_cost - actual_cost) > 0.001:
            mismatches.append({
                "code": code,
                "name": item.get("name") or (row.get("name") if row else code),
                "expected_shares": expected_shares,
                "actual_shares": actual_shares,
                "expected_avg_cost": expected_cost,
                "actual_avg_cost": actual_cost,
            })
    for code, row in actual.items():
        if code not in expected and _safe_float(row.get("total_shares")) > 0:
            mismatches.append({
                "code": code,
                "name": row.get("name") or code,
                "expected_shares": 0,
                "actual_shares": round(_safe_float(row.get("total_shares")), 3),
                "expected_avg_cost": 0,
                "actual_avg_cost": round(float(row.get("avg_cost") or 0), 3),
            })
    return mismatches


async def _invalid_account_refs():
    invalid = []
    for table in ("portfolio", "trades", "conditional_orders"):
        rows = await _fetchall(
            f"""
            SELECT '{table}' AS table_name, account_id, COUNT(*) AS count
            FROM {table}
            WHERE account_id IS NULL
               OR account_id = ''
               OR account_id NOT IN (SELECT id FROM accounts)
            GROUP BY account_id
            """
        )
        invalid.extend(rows)
    return invalid


async def _cash_ledger_gaps(settings: dict):
    gaps = []
    keys = [key for key in settings if key == "cash_balance" or key.startswith("cash_balance_")]
    for key in keys:
        account_id = key.replace("cash_balance_", "") if key.startswith("cash_balance_") else "default"
        configured = _safe_float(settings.get(key))
        rows = await _fetchall(
            """
            SELECT balance_after
            FROM cash_ledger
            WHERE account_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (account_id,),
        )
        if not rows:
            gaps.append({"account_id": account_id, "configured_cash": configured, "ledger_cash": None})
            continue
        ledger_cash = _safe_float(rows[0].get("balance_after"))
        if abs(configured - ledger_cash) > 0.01:
            gaps.append({"account_id": account_id, "configured_cash": configured, "ledger_cash": ledger_cash})
    return gaps


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
        "avg_elapsed": round(sum(elapsed) / len(elapsed), 3) if elapsed else 0,
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
