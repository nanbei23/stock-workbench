"""Enhancement API: model provider pool, review panels, risk and health checks."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from services import enhancement_service

router = APIRouter(tags=["enhancements"])


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


class ApplyProviderPayload(BaseModel):
    target: str = "ai"


class BacktestPayload(BaseModel):
    code: str
    condition_type: str = "price_lte"
    target_price: float
    days: int = 90


class TemplatePayload(BaseModel):
    name: str | None = None


@router.get("/model-providers")
async def list_model_providers():
    return enhancement_service.list_model_providers()


@router.post("/model-providers")
async def save_model_provider(payload: ModelProviderPayload):
    return enhancement_service.save_model_provider(payload.model_dump())


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


@router.get("/ai/report-versions/{code}")
async def report_versions(code: str, limit: int = Query(default=20, ge=1, le=100)):
    return await enhancement_service.report_versions(code, limit)


@router.get("/ai/report-compare")
async def compare_reports(left_id: int, right_id: int):
    return await enhancement_service.compare_reports(left_id, right_id)


@router.post("/ai/conditional-order/backtest")
async def condition_backtest(payload: BacktestPayload):
    return await enhancement_service.condition_backtest(payload.model_dump())


@router.get("/portfolio/risk-exposure")
async def risk_exposure():
    return await enhancement_service.risk_exposure()


@router.get("/events")
async def events():
    return await enhancement_service.events()


@router.get("/data-health")
async def data_health():
    return await enhancement_service.data_health()


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


@router.get("/ai/readiness")
async def ai_readiness():
    return enhancement_service.ai_readiness()
