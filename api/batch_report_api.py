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
    group: str = "all"
    top_n: int = Field(default=0, ge=0)
    skip_recent_days: int = Field(default=30, ge=0)
    refresh_snapshots: bool = False
    snapshot_concurrency: int = Field(default=3, ge=1, le=10)
    analysis_mode: str = "snapshot"
    analysis_concurrency: int = Field(default=1, ge=1, le=10)
    snapshot_model_tier: str = "deep"
    plan_top_n: int = Field(default=10, ge=1, le=50)
    multi_role: bool = False
    trade_date: Optional[str] = None
    output_dir: Optional[Path] = None


class BatchReportCreatePayload(BaseModel):
    codes: list[str] = Field(default_factory=list)
    group: str = "all"
    depth: str = "standard"
    debate_rounds: Optional[int] = 1
    risk_rounds: Optional[int] = 1
    batch_size: int = Field(default=2, ge=1, le=10)
    trade_date: Optional[str] = None


@router.post("/batch-research/jobs")
async def create_batch_research_job(payload: BatchResearchCreatePayload):
    return await batch_report_service.create_research_job(**payload.model_dump())


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


@router.post("/batch-research/jobs/{job_id}/resume")
async def resume_batch_research_job(job_id: str):
    return await batch_report_service.resume_job(job_id)


@router.post("/batch-research/jobs/{job_id}/retry-failed")
async def retry_failed_batch_research_items(job_id: str):
    return await batch_report_service.retry_failed(job_id)


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
        snapshot_model_tier="deep",
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
