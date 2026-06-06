"""Batch research APIs for v2.8."""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from services import batch_report_service

router = APIRouter(tags=["batch-research"])


class BatchResearchCreatePayload(BaseModel):
    job_type: str = Field(default="report_generation")
    codes: list[str] = Field(default_factory=list)
    report_ids: list[int] = Field(default_factory=list)
    allow_all: bool = False
    group: str = "all"
    top_n: int = Field(default=0, ge=0)
    skip_recent_days: int = Field(default=30, ge=0)
    refresh_snapshots: bool = False
    snapshot_concurrency: int = Field(default=3, ge=1, le=10)
    analysis_mode: str = "snapshot-tradingagents"
    analysis_concurrency: int = Field(default=1, ge=1, le=10)
    analysis_depth: str = "standard"
    model_mode: str = "balanced"
    snapshot_model_tier: str = "deep"
    debate_rounds: int = Field(default=1, ge=1, le=5)
    risk_rounds: int = Field(default=1, ge=1, le=5)
    timeout_seconds: int = Field(default=3600, ge=60, le=28800)
    role_retry_attempts: int = Field(default=3, ge=1, le=8)
    role_retry_backoff_seconds: float = Field(default=2.0, ge=0, le=120)
    max_consecutive_failures: int = Field(default=20, ge=0, le=50)
    max_failure_rate: float = Field(default=0.6, ge=0, le=1)
    min_failure_rate_items: int = Field(default=20, ge=1, le=500)
    guard_window_items: int = Field(default=20, ge=1, le=500)
    resilience_mode: str = "robust"
    model_fallback_enabled: bool = True
    fallback_provider_ids: list[str] = Field(default_factory=list)
    plan_top_n: int = Field(default=10, ge=1, le=50)
    multi_role: bool = False
    stage: str = "final"
    parent_plan_id: Optional[str] = None
    context_strategy: str = "auto"
    model_strategy: str = "single"
    role_models: dict = Field(default_factory=dict)
    allowed_worker_ids: list[str] = Field(default_factory=list)
    primary_provider_ids: list[str] = Field(default_factory=list)
    quota_exhausted_action: str = "switch_model"
    quota_pause_scope: str = "item"
    failure_retry_mode: str = "auto_switch_model"
    selection_id: Optional[str] = None
    source_page: Optional[str] = None
    source_label: Optional[str] = None
    max_auto_item_retries: int = Field(default=2, ge=0, le=10)
    auto_retry_delay_seconds: int = Field(default=60, ge=0, le=3600)
    max_auto_retry_delay_seconds: int = Field(default=900, ge=0, le=7200)
    max_runtime_cooldown_seconds: int = Field(default=300, ge=0, le=3600)
    title: Optional[str] = None
    trade_date: Optional[str] = None
    output_dir: Optional[Path] = None


class BatchReportCreatePayload(BaseModel):
    codes: list[str] = Field(default_factory=list)
    group: str = "all"
    depth: str = "standard"
    model_mode: str = "balanced"
    debate_rounds: Optional[int] = 1
    risk_rounds: Optional[int] = 1
    batch_size: int = Field(default=2, ge=1, le=10)
    trade_date: Optional[str] = None


class BatchRetryPayload(BaseModel):
    error_type: Optional[str] = None
    model_provider_ids: list[str] = Field(default_factory=list)
    model_tier: Optional[str] = None


@router.post("/batch-research/jobs")
async def create_batch_research_job(payload: BatchResearchCreatePayload):
    return await batch_report_service.create_research_job(**payload.model_dump())


@router.post("/batch-research/preflight")
async def preflight_batch_research_models(payload: BatchResearchCreatePayload):
    return batch_report_service.preflight_batch_models(**payload.model_dump())


@router.get("/batch-research/workers")
async def get_batch_research_workers(stale_minutes: int = Query(default=15, ge=1, le=120)):
    return batch_report_service.get_worker_status(stale_minutes=stale_minutes)


@router.post("/batch-research/workers/reclaim-stale")
async def reclaim_stale_batch_research_workers(
    stale_minutes: int = Query(default=15, ge=1, le=120),
    worker_id: Optional[str] = Query(default=None),
):
    return batch_report_service.reclaim_stale_workers(stale_minutes=stale_minutes, worker_id=worker_id)


@router.get("/batch-research/jobs")
async def list_batch_research_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    status: Optional[str] = Query(None),
    job_type: Optional[str] = Query(None),
):
    return batch_report_service.list_research_jobs(limit=limit, status=status, job_type=job_type)


@router.get("/batch-research/jobs/{job_id}")
async def get_batch_research_job(job_id: str):
    return batch_report_service.get_research_job(job_id)


@router.get("/batch-research/jobs/{job_id}/items")
async def get_batch_research_items(job_id: str, status: Optional[str] = Query(None)):
    return batch_report_service.get_research_items(job_id, status=status)


@router.get("/batch-research/jobs/{job_id}/logs")
async def get_batch_research_logs(job_id: str, limit: int = Query(default=200, ge=1, le=1000)):
    return batch_report_service.get_job_logs(job_id, limit=limit)


@router.get("/batch-research/jobs/{job_id}/failure-groups")
async def get_batch_research_failure_groups(job_id: str):
    return batch_report_service.get_failure_groups(job_id)


@router.get("/batch-research/jobs/{job_id}/runtime-stats")
async def get_batch_research_runtime_stats(job_id: str):
    return batch_report_service.get_runtime_stats(job_id)


@router.get("/batch-research/jobs/{job_id}/analysis")
async def get_batch_research_analysis(job_id: str):
    return await batch_report_service.get_batch_analysis(job_id)


@router.get("/batch-research/jobs/{job_id}/artifacts")
async def get_batch_research_artifacts(job_id: str):
    return batch_report_service.get_job_artifacts(job_id)


@router.get("/batch-research/items/{item_id}/steps")
async def get_batch_research_item_steps(item_id: int):
    return batch_report_service.get_research_item_steps(item_id)


@router.post("/batch-research/jobs/{job_id}/pause")
async def pause_batch_research_job(job_id: str):
    return batch_report_service.pause_job(job_id)


@router.post("/batch-research/jobs/{job_id}/manual-complete")
async def manual_complete_batch_research_job(job_id: str):
    return batch_report_service.manual_complete_job(job_id)


@router.post("/batch-research/jobs/{job_id}/resume")
async def resume_batch_research_job(job_id: str):
    return await batch_report_service.resume_job(job_id)


@router.post("/batch-research/jobs/{job_id}/retry-failed")
async def retry_failed_batch_research_items(job_id: str, payload: BatchRetryPayload | None = None):
    payload = payload or BatchRetryPayload()
    return await batch_report_service.retry_failed(
        job_id,
        error_type=payload.error_type,
        model_provider_ids=payload.model_provider_ids,
        model_tier=payload.model_tier,
    )


@router.post("/batch-research/jobs/{job_id}/cancel")
async def cancel_batch_research_job(job_id: str):
    return batch_report_service.cancel_job(job_id)


# Backward-compatible old batch report API.
@router.post("/batch-reports")
async def create_batch_report(payload: BatchReportCreatePayload):
    data = payload.model_dump()
    return await batch_report_service.create_research_job(
        job_type="report_generation",
        codes=data.get("codes") or [],
        group=data.get("group") or "all",
        skip_recent_days=30,
        analysis_mode="snapshot",
        analysis_concurrency=max(1, min(int(data.get("batch_size") or 1), 10)),
        analysis_depth=data.get("depth") or "standard",
        model_mode=data.get("model_mode") or "balanced",
        snapshot_model_tier="quick" if data.get("model_mode") == "economy" or data.get("depth") == "quick" else "deep",
        trade_date=data.get("trade_date"),
    )


@router.get("/batch-reports")
async def list_batch_reports(
    limit: int = Query(default=50, ge=1, le=200),
    status: Optional[str] = Query(None),
):
    return batch_report_service.list_research_jobs(limit=limit, status=status, job_type="report_generation")


@router.get("/batch-reports/{job_id}")
async def get_batch_report(job_id: str):
    return batch_report_service.get_research_job(job_id)
