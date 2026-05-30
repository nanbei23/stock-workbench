"""Settings business operations."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from config import DB_PATH
from models.database import MIGRATIONS
from repositories import settings_repository as repo


DEFAULTS = {
    "auto_refresh_enabled": "false",
    "refresh_interval": "30",
    "change_threshold": "5",
    "volume_threshold": "3",
    "northbound_threshold": "5",
    "anomaly_monitor_enabled": "true",
    "llm_provider": "deepseek",
    "llm_name": "",
    "deep_think_model": "",
    "quick_think_model": "",
    "llm_model_options": "[]",
    "llm_context_length": "",
    "api_key": "",
    "custom_endpoint": "",
    "output_language": "zh",
    "verification_name": "",
    "verification_model": "",
    "verification_model_options": "[]",
    "verification_context_length": "",
    "debate_rounds": "1",
    "risk_rounds": "1",
    "checkpoint_enabled": "true",
    "schedule_open_report": "true",
    "schedule_am_close": "true",
    "schedule_pm_open": "true",
    "schedule_close_report": "true",
    "schedule_anomaly_realtime": "true",
    "notify_strategy_change": "true",
    "notify_order_trigger": "true",
    "notify_anomaly": "true",
    "notify_analysis_done": "true",
    "browser_notify_enabled": "false",
    "commission_rate": "0.0003",
    "commission_min": "5",
    "stamp_tax_rate": "0.0005",
    "transfer_fee_rate": "0.00001",
    "stop_loss_pct": "8",
    "onboarding_completed": "false",
    "onboarding_dismissed_at": "",
    "hermes_tool_policy": json.dumps(
        {
            "add_watchlist": "draft",
            "record_trade": "draft",
            "set_position": "draft",
            "create_conditional_order": "draft",
        },
        ensure_ascii=False,
    ),
}


def _loads(value, fallback):
    if not value:
        return fallback
    try:
        data = json.loads(value) if isinstance(value, str) else value
        return data if data is not None else fallback
    except (TypeError, ValueError):
        return fallback


def ensure_settings_table():
    repo.ensure_settings_table()


def get_all_settings():
    result = repo.fetch_settings()
    for key, value in DEFAULTS.items():
        result.setdefault(key, value)
    return result


def bulk_update_settings(settings: dict):
    repo.upsert_settings(settings)
    return {"status": "ok", "updated": len(settings)}


def reset_settings():
    repo.reset_settings(DEFAULTS)
    return {"status": "ok", "reset": len(DEFAULTS)}


def get_setting(key: str):
    row = repo.fetch_setting(key)
    if row:
        return {"key": row["key"], "value": row["value"]}
    if key in DEFAULTS:
        return {"key": key, "value": DEFAULTS[key]}
    raise HTTPException(404, f"设置项 {key} 不存在")


def update_setting(key: str, value):
    repo.upsert_settings({key: value})
    return {"status": "ok", "key": key, "value": value}


def onboarding_status():
    settings = get_all_settings()
    conn = repo.open_connection()
    try:
        counts = {
            "accounts": conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0],
            "watchlist": conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0],
            "portfolio": conn.execute("SELECT COUNT(*) FROM portfolio WHERE total_shares > 0").fetchone()[0],
        }
    finally:
        conn.close()

    has_models = bool(_loads(settings.get("llm_model_options"), [])) and bool(
        settings.get("quick_think_model") or settings.get("deep_think_model")
    )
    has_api = bool(settings.get("api_key") and settings.get("custom_endpoint"))
    has_cash = any(key.startswith("cash_balance_") or key == "cash_balance" for key in settings)
    steps = [
        {
            "key": "account",
            "label": "账户",
            "status": "ok" if counts["accounts"] else "pending",
            "message": "已创建账户" if counts["accounts"] else "先创建默认账户或券商账户",
        },
        {
            "key": "cash",
            "label": "现金",
            "status": "ok" if has_cash else "pending",
            "message": "已设置现金余额" if has_cash else "建议在持仓页录入账户现金，资产看板才完整",
        },
        {
            "key": "models",
            "label": "AI模型",
            "status": "ok" if has_api and has_models else "pending",
            "message": "AI 引擎已配置" if has_api and has_models else "在设置页填写 Base URL/API Key 并获取模型列表",
        },
        {
            "key": "watchlist",
            "label": "自选股",
            "status": "ok" if counts["watchlist"] else "pending",
            "message": "已有自选股" if counts["watchlist"] else "添加常看的股票，盯盘和分析入口会更顺手",
        },
        {
            "key": "hermes",
            "label": "Hermes",
            "status": "ok" if _loads(settings.get("hermes_tool_policy"), {}) else "pending",
            "message": "已启用受控写库草稿策略",
        },
    ]
    pending = [step for step in steps if step["status"] != "ok"]
    completed = settings.get("onboarding_completed") == "true" or not pending
    return {
        "completed": completed,
        "dismissed_at": settings.get("onboarding_dismissed_at") or "",
        "counts": counts,
        "steps": steps,
        "pending_count": len(pending),
    }


def complete_onboarding():
    value = datetime.now().isoformat(timespec="seconds")
    repo.upsert_settings({"onboarding_completed": "true", "onboarding_dismissed_at": value})
    return {"status": "ok", "completed": True, "completed_at": value}


def set_model_mode(value):
    return update_setting("model_mode", value)


def llm_test_config():
    settings = repo.fetch_settings()
    return {
        "api_key": settings.get("api_key", ""),
        "endpoint": settings.get("custom_endpoint", ""),
        "model": settings.get("quick_think_model", "deepseek-chat"),
    }


def verification_test_config():
    settings = repo.fetch_settings_like("verification_%")
    return {
        "model": settings.get("verification_model") or "mimo-v2.5-pro",
        "endpoint": settings.get("verification_endpoint") or "https://token-plan-cn.xiaomimimo.com/v1",
        "api_key": settings.get("verification_api_key") or "",
    }


def export_payload():
    content = json.dumps(repo.export_data(), ensure_ascii=False, indent=2, default=str)
    filename = f"stock-workbench-backup-{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
    return content, filename


def _backup_dir() -> Path:
    path = Path(DB_PATH).resolve().parent / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def migration_status():
    conn = repo.open_connection()
    try:
        try:
            rows = conn.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
        except Exception:
            rows = []
        applied = [dict(row) for row in rows]
        latest_known = max((version for version, _, _ in MIGRATIONS), default=0)
        latest_applied = max((row["version"] for row in applied), default=0)
        db_file = Path(DB_PATH)
        backups = sorted(_backup_dir().glob("stock-workbench-backup-*.json"), reverse=True)
        return {
            "database": {
                "path": str(db_file),
                "exists": db_file.exists(),
                "size_bytes": db_file.stat().st_size if db_file.exists() else 0,
            },
            "migrations": {
                "latest_known": latest_known,
                "latest_applied": latest_applied,
                "pending": [version for version, _, _ in MIGRATIONS if version > latest_applied],
                "applied": applied,
                "up_to_date": latest_applied >= latest_known,
            },
            "backups": [
                {
                    "filename": item.name,
                    "path": str(item),
                    "size_bytes": item.stat().st_size,
                    "created_at": datetime.fromtimestamp(item.stat().st_mtime).isoformat(),
                }
                for item in backups[:10]
            ],
        }
    finally:
        conn.close()


def create_backup_file():
    content, filename = export_payload()
    target = _backup_dir() / filename
    target.write_text(content, encoding="utf-8")
    return {
        "status": "ok",
        "filename": filename,
        "path": str(target),
        "size_bytes": target.stat().st_size,
        "created_at": datetime.fromtimestamp(target.stat().st_mtime).isoformat(),
    }


def restore_latest_backup():
    backups = sorted(_backup_dir().glob("stock-workbench-backup-*.json"), reverse=True)
    if not backups:
        raise HTTPException(404, "没有可恢复的备份")
    data = json.loads(backups[0].read_text(encoding="utf-8"))
    payload = SimpleNamespace(
        settings=data.get("settings"),
        watchlist=data.get("watchlist"),
        portfolio=data.get("portfolio"),
        orders=data.get("orders"),
    )
    return {
        "status": "ok",
        "filename": backups[0].name,
        "imported": repo.import_data(payload),
    }


def import_data(data):
    return {"status": "ok", "imported": repo.import_data(data)}


def clear_all_data():
    tables = [
        "watchlist",
        "portfolio",
        "trades",
        "conditional_orders",
        "analysis_reports",
        "daily_pnl",
        "anomaly_logs",
    ]
    return {"status": "ok", "cleared": repo.clear_data(tables)}


def poll_notifications():
    notifications = repo.fetch_recent_notifications()
    return {"count": len(notifications), "notifications": notifications}
