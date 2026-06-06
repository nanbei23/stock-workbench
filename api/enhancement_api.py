"""Enhancement API: model provider pool, review panels, risk and health checks."""

import hashlib
import json

from fastapi import APIRouter, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from services import enhancement_service

router = APIRouter(tags=["enhancements"])


def _cached_json(payload: dict, request: Request, max_age: int = 30):
    encoded = json.dumps(jsonable_encoder(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    etag = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": f"private, max-age={max_age}"})
    return JSONResponse(
        content=json.loads(encoded),
        headers={"ETag": etag, "Cache-Control": f"private, max-age={max_age}"},
    )


class ModelProviderPayload(BaseModel):
    id: str | None = None
    name: str | None = None
    base_url: str
    api_key: str | None = ""
    models: list[str] | None = None
    quick_model: str | None = ""
    deep_model: str | None = ""
    default_model: str | None = ""
    context_length: str | int | None = ""
    embedding_model: str | None = ""
    embedding_dimensions: int | None = 1536
    usage: list[str] | None = None
    apply_to: str | None = None


class ApplyProviderPayload(BaseModel):
    target: str = "ai"


class WorkerPoolWorkerPayload(BaseModel):
    id: str | None = ""
    name: str | None = ""
    enabled: bool = True
    provider_ids: list[str] | None = None
    model_tier: str | None = "deep"
    sleep_seconds: int | None = 5
    stale_minutes: int | None = 15


class WorkerPoolConfigPayload(BaseModel):
    workers: list[WorkerPoolWorkerPayload] = []


class TemplatePayload(BaseModel):
    name: str | None = None


@router.get("/model-providers")
async def list_model_providers():
    return enhancement_service.list_model_providers()


@router.post("/model-providers")
async def save_model_provider(payload: ModelProviderPayload):
    return enhancement_service.save_model_provider(payload.model_dump())


@router.put("/model-providers/{provider_id}")
async def update_model_provider(provider_id: str, payload: ModelProviderPayload):
    return enhancement_service.update_model_provider(provider_id, payload.model_dump())


@router.delete("/model-providers/{provider_id}")
async def delete_model_provider(provider_id: str):
    return enhancement_service.delete_model_provider(provider_id)


@router.post("/model-providers/{provider_id}/apply")
async def apply_model_provider(provider_id: str, payload: ApplyProviderPayload):
    return enhancement_service.apply_model_provider(provider_id, payload.target)


@router.post("/model-providers/{provider_id}/refresh")
async def refresh_model_provider(provider_id: str):
    return await enhancement_service.refresh_model_provider(provider_id)


@router.post("/model-providers/{provider_id}/test")
async def test_model_provider(provider_id: str):
    return await enhancement_service.test_model_provider(provider_id)


@router.get("/worker-pool/config")
async def get_worker_pool_config():
    return enhancement_service.get_worker_pool_config()


@router.post("/worker-pool/config")
async def save_worker_pool_config(payload: WorkerPoolConfigPayload):
    return enhancement_service.save_worker_pool_config(payload.model_dump())


@router.get("/ai/report-versions/{code}")
async def report_versions(code: str, limit: int = Query(default=20, ge=1, le=100)):
    return await enhancement_service.report_versions(code, limit)


@router.get("/ai/report-compare")
async def compare_reports(left_id: int, right_id: int):
    return await enhancement_service.compare_reports(left_id, right_id)


@router.get("/portfolio/risk-exposure")
async def risk_exposure():
    return await enhancement_service.risk_exposure()


@router.get("/portfolio/risk-center")
async def risk_center():
    return await enhancement_service.risk_center()


@router.get("/portfolio/professional-summary")
async def portfolio_professional_summary():
    return await enhancement_service.portfolio_professional_summary()


@router.get("/events")
async def events():
    return await enhancement_service.events()


@router.get("/data-health")
async def data_health():
    return await enhancement_service.data_health()


@router.get("/data-audit")
async def data_audit():
    return await enhancement_service.data_audit()


@router.post("/data-health/fix")
async def fix_data_health():
    return await enhancement_service.fix_data_health()


@router.get("/workspace-templates")
async def workspace_templates():
    return enhancement_service.workspace_templates()


@router.post("/workspace-templates")
async def save_workspace_template(payload: TemplatePayload):
    return enhancement_service.save_workspace_template(payload.name or "")


@router.post("/workspace-templates/{template_id}/apply")
async def apply_workspace_template(template_id: str):
    return enhancement_service.apply_workspace_template(template_id)


@router.get("/ai/task-metrics")
async def task_metrics():
    return await enhancement_service.task_metrics()


@router.get("/system-diagnostics")
async def system_diagnostics():
    return await enhancement_service.system_diagnostics()


@router.get("/operations/dashboard")
async def operations_dashboard():
    return await enhancement_service.operations_dashboard()


@router.get("/market-regime")
async def market_regime(request: Request):
    return _cached_json(await enhancement_service.market_regime(), request, max_age=20)


@router.get("/hotspots")
async def market_hotspots(request: Request, limit: int = Query(default=12, ge=1, le=30)):
    return _cached_json(await enhancement_service.market_hotspots(limit=limit), request, max_age=45)


@router.get("/hotspots/{topic_name}")
async def hotspot_detail(topic_name: str, request: Request):
    return _cached_json(await enhancement_service.hotspot_detail(topic_name), request, max_age=45)


@router.get("/research-pulse")
async def research_pulse(request: Request):
    return _cached_json(enhancement_service.research_pulse(), request, max_age=30)


@router.get("/strategy-lifecycle")
async def strategy_lifecycle(request: Request):
    return _cached_json(await enhancement_service.strategy_lifecycle(), request, max_age=30)


@router.get("/research-progress")
async def research_progress(request: Request):
    return _cached_json(await enhancement_service.research_progress(), request, max_age=10)


@router.get("/notifications/digest")
async def notification_digest():
    return await enhancement_service.notification_digest()


@router.get("/ai/readiness")
async def ai_readiness():
    return enhancement_service.ai_readiness()
