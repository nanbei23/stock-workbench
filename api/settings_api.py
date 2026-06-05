"""设置API — 完整CRUD，SQLite持久化
路由顺序: 具体路径 MUST 在 {key} 参数路由之前，否则会被吞掉。
"""
import os
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, Any

from services import investment_profile_service, settings_service

router = APIRouter(tags=["设置"])

settings_service.ensure_settings_table()


# ── Pydantic Models ──

class SettingUpdate(BaseModel):
    key: str
    value: Any


class SettingsBulkUpdate(BaseModel):
    settings: dict


class VerificationTestRequest(BaseModel):
    endpoint: str = ""
    api_key: str = ""
    model: str = ""


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
    return settings_service.get_all_settings()


# ── 批量更新 ──

@router.post("/settings/bulk")
async def bulk_update_settings(data: SettingsBulkUpdate):
    """批量更新设置"""
    return settings_service.bulk_update_settings(data.settings)


@router.post("/settings/investment-profile/infer")
async def infer_investment_profile():
    """根据本地交易历史推断投资风格草稿，不直接写入设置。"""
    return investment_profile_service.infer_profile_from_trade_history()


# ── 重置默认 ──

@router.post("/settings/reset")
async def reset_settings():
    """重置为默认设置"""
    return settings_service.reset_settings()


@router.get("/settings/onboarding")
async def get_onboarding_status():
    """获取首次使用引导状态"""
    return settings_service.onboarding_status()


@router.post("/settings/onboarding/complete")
async def complete_onboarding():
    """标记首次使用引导已完成"""
    return settings_service.complete_onboarding()


# ── 测试API连接 ──

@router.post("/settings/test-llm")
async def test_api_connection():
    """测试DeepSeek API连接"""
    cfg = settings_service.llm_test_config()
    key = cfg["api_key"] or os.environ.get("DEEPSEEK_API_KEY", "")
    url = cfg["endpoint"] or "https://api.deepseek.com"
    mdl = cfg["model"]

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
async def test_verification_connection(req: VerificationTestRequest | None = None):
    """测试旁观者核对模型API连接"""
    cfg = settings_service.verification_test_config()
    model = (req.model if req else "") or cfg["model"]
    endpoint = (req.endpoint if req else "") or cfg["endpoint"]
    api_key = (req.api_key if req else "") or cfg["api_key"]

    if not api_key:
        return {"status": "error", "message": "API密钥未配置"}
    if not endpoint:
        return {"status": "error", "message": "Base URL未配置"}
    if not model:
        return {"status": "error", "message": "核对模型未配置"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{endpoint.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
            )
        try:
            data = resp.json()
        except ValueError:
            return {"status": "error", "message": f"非JSON响应: {resp.text[:200]}"}
        if resp.status_code < 400 and "choices" in data:
            return {"status": "ok", "message": f"连接成功 ({model})"}
        if "error" in data:
            err = data["error"]
            msg = err.get("message", "未知错误") if isinstance(err, dict) else str(err)
            return {"status": "error", "message": msg[:200]}
        return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


# ── 导出全部数据 ──

@router.get("/settings/export")
async def export_all_data():
    """导出完整 SQLite 数据库文件"""
    backup = settings_service.create_backup_file()
    return FileResponse(
        backup["path"],
        media_type="application/vnd.sqlite3",
        filename=backup["filename"],
    )


# ── 导入数据 ──

@router.post("/settings/import")
async def import_data(data: ImportData):
    """导入数据（追加/覆盖模式）"""
    return settings_service.import_data(data)


@router.get("/settings/backup/status")
async def backup_status():
    """数据库版本、迁移状态和最近备份"""
    return settings_service.migration_status()


@router.post("/settings/backup/create")
async def create_backup():
    """一键创建本地 SQLite 数据库备份"""
    return settings_service.create_backup_file()


@router.post("/settings/backup/restore-latest")
async def restore_latest_backup():
    """从最近一次本地 SQLite 数据库备份恢复"""
    return settings_service.restore_latest_backup()


# ── 清空全部数据 ──

@router.post("/settings/clear-all")
async def clear_all_data():
    """清空全部数据（危险操作）"""
    return settings_service.clear_all_data()


# ── 通知轮询 ──

@router.get("/notifications")
async def poll_notifications():
    """轮询通知（条件单触发/分析完成/策略变化/异动）"""
    return settings_service.poll_notifications()


# ── 模型模式快捷设置（POST，AI分析台调用）──

@router.post("/settings/model_mode")
async def set_model_mode(data: SettingUpdate):
    """快捷设置模型模式（POST，供AI分析台调用）"""
    return settings_service.set_model_mode(data.value)


# ── 获取远程模型列表（兼容 OpenAI /v1/models 协议）──

class FetchModelsReq(BaseModel):
    endpoint: str
    api_key: str = ""


def _model_list_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/models"
    if base.endswith("/models"):
        return base
    return f"{base}/v1/models"


def _parse_model_ids(data) -> list[str]:
    models = []
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        models = [m["id"] for m in data["data"] if isinstance(m, dict) and "id" in m]
    elif isinstance(data, list):
        models = [m if isinstance(m, str) else m.get("id", "") for m in data if isinstance(m, (str, dict))]
    return sorted({m for m in models if m})


def _connect_error_detail(url: str, exc: Exception) -> str:
    reason = str(exc) or exc.__class__.__name__
    return (
        f"无法连接到模型接口 {url}：{reason}。"
        "请检查 Base URL、部署机器网络、DNS、证书，以及 launchd/终端进程是否带有代理环境变量。"
    )


@router.post("/settings/fetch-models")
async def fetch_models(req: FetchModelsReq):
    """从自定义API端点获取模型列表（兼容 OpenAI /v1/models 协议）"""
    url = _model_list_url(req.endpoint)
    headers = {}
    if req.api_key:
        headers["Authorization"] = f"Bearer {req.api_key}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                message = resp.text[:200]
                try:
                    payload = resp.json()
                    err = payload.get("error") if isinstance(payload, dict) else None
                    if isinstance(err, dict):
                        message = err.get("message") or message
                    elif err:
                        message = str(err)
                except ValueError:
                    pass
                raise HTTPException(resp.status_code, detail=f"模型接口返回 {resp.status_code}: {message}")
            data = resp.json()
        models = _parse_model_ids(data)
        if not models:
            raise HTTPException(502, detail=f"模型接口已连接，但未返回模型列表: {str(data)[:200]}")
        return {"status": "ok", "models": models}
    except HTTPException:
        raise
    except httpx.ConnectError as exc:
        raise HTTPException(400, detail=_connect_error_detail(url, exc))
    except httpx.TimeoutException:
        raise HTTPException(408, detail=f"连接模型接口超时 {url}，请检查网络、代理或服务状态")
    except Exception as e:
        raise HTTPException(500, detail=f"获取模型失败: {str(e)}")


# ═══════════════════════════════════════════════
# ★ {key} 参数路由 — 放在最后
# ═══════════════════════════════════════════════

@router.get("/settings/{key}")
async def get_setting(key: str):
    """获取单个设置"""
    return settings_service.get_setting(key)


@router.put("/settings/{key}")
async def update_setting(key: str, data: SettingUpdate):
    """更新单个设置"""
    return settings_service.update_setting(key, data.value)
