"""Typed contracts for the AI task API."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


AnalysisDepth = Literal["quick", "standard", "deep", "custom"]
TaskStatus = Literal["pending", "queued", "running", "completed", "failed", "timeout", "cancelled", "cancelling"]


class AnalysisStartRequest(BaseModel):
    trade_date: Optional[str] = None
    depth: Optional[AnalysisDepth] = "standard"
    selected_analysts: Optional[list[str]] = None
    debate_rounds: Optional[int] = Field(default=None, ge=0)
    risk_rounds: Optional[int] = Field(default=None, ge=0)
    model_mode: Optional[str] = None


class AnalysisStartResponse(BaseModel):
    task_id: Optional[str] = None
    status: str
    message: Optional[str] = None


class BatchAnalyzeRequest(BaseModel):
    codes: list[str] = Field(default_factory=list)
    trade_date: Optional[str] = None
    mode: Optional[str] = "economy"
    depth: Optional[AnalysisDepth] = "standard"
    selected_analysts: Optional[list[str]] = None
    debate_rounds: Optional[int] = Field(default=None, ge=0)
    risk_rounds: Optional[int] = Field(default=None, ge=0)


class BatchTaskItem(BaseModel):
    task_id: str
    code: str
    name: Optional[str] = None


class BatchSkipItem(BaseModel):
    code: str
    reason: str


class BatchAnalyzeResponse(BaseModel):
    count: int
    tasks: list[BatchTaskItem] = Field(default_factory=list)
    message: str
    skipped: Optional[list[BatchSkipItem]] = None
    rejected: Optional[list[str]] = None
    rejected_reason: Optional[str] = None


class StageStatus(BaseModel):
    status: str = "pending"
    name: str = ""
    icon: str = ""


class AnalysisStatusResponse(BaseModel):
    task_id: str
    code: Optional[str] = None
    name: Optional[str] = None
    status: str
    progress: str
    elapsed: Optional[float] = None
    stages: dict[str, StageStatus] = Field(default_factory=dict)
    error: Optional[str] = None
    queue_status: Optional[str] = None


class AnalysisResultResponse(BaseModel):
    task_id: str
    status: str
    code: Optional[str] = None
    name: Optional[str] = None
    elapsed: Optional[float] = None
    result: Optional[dict[str, Any]] = None
    message: Optional[str] = None
    error: Optional[str] = None


class ActiveTaskResponse(BaseModel):
    task_id: Optional[str] = None
    code: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[str] = None
    stages: dict[str, str] = Field(default_factory=dict)
    depth: Optional[AnalysisDepth] = None
    selected_analysts: Optional[list[str]] = None
    debate_rounds: Optional[int] = None
    risk_rounds: Optional[int] = None


class QueueStatusResponse(BaseModel):
    max_concurrent: int
    max_queue: int
    running: int
    queued: int
    available_slots: int


class TaskActionResponse(BaseModel):
    status: str
    message: Optional[str] = None
    task_id: Optional[str] = None


class AnalysisTaskListItem(BaseModel):
    task_id: str
    code: Optional[str] = None
    name: Optional[str] = None
    status: str
    queue_status: Optional[str] = None
    progress: str
    queue_position: int = 0
    error: Optional[str] = None
    depth: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    updated_at: Optional[str] = None
    can_cancel: bool = False
    can_retry: bool = False


class AnalysisTaskListResponse(BaseModel):
    count: int
    queue: QueueStatusResponse
    tasks: list[AnalysisTaskListItem] = Field(default_factory=list)


class GbrainSaveRequest(BaseModel):
    slug: str
    title: str
    content: str


class GenerateConditionalOrderRequest(BaseModel):
    code: str
    name: str = ""
    action: Literal["buy", "sell"] = "buy"
    price: float = Field(gt=0)
    shares: int = Field(default=0, ge=0)
    condition_type: Literal["price_lte", "price_gte", "change_pct_gte", "change_pct_lte"] = "price_lte"
    notes: str = ""
    expires_at: Optional[str] = None


class GenerateConditionalOrderResponse(BaseModel):
    success: bool
    id: Optional[int] = None
    message: str


class ConditionalOrderDraftRequest(BaseModel):
    report_id: int
    shares: int = Field(default=0, ge=0)
    expires_at: Optional[str] = None


class ConditionalOrderDraft(BaseModel):
    code: str
    name: str = ""
    action: Literal["buy", "sell"]
    condition_type: Literal["price_lte", "price_gte", "change_pct_gte", "change_pct_lte"]
    target_price: float
    shares: int = 0
    notes: str = ""
    expires_at: Optional[str] = None
    source_report_id: int
    source_task_id: Optional[str] = None
    signal: Optional[str] = None
    confidence: Optional[float] = None
    warnings: list[str] = Field(default_factory=list)


class ConditionalOrderDraftResponse(BaseModel):
    draft: ConditionalOrderDraft


class ConfirmConditionalOrderDraftRequest(ConditionalOrderDraft):
    pass
