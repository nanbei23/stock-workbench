"""信号跟踪 REST API"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from services import signal_tracking_service

router = APIRouter(prefix="/signal", tags=["signal"])


class TrackRequest(BaseModel):
    code: str
    name: str
    signal: str
    entry_price: float
    target_price: Optional[float] = None


class CloseRequest(BaseModel):
    exit_price: float


@router.post("/track")
async def add_tracking(req: TrackRequest):
    """手动添加信号跟踪"""
    return signal_tracking_service.add_tracking(req)


@router.get("/tracking")
async def list_tracking(status: Optional[str] = None, signal: Optional[str] = None,
                        code: Optional[str] = None, window: str = "all",
                        model_mode: Optional[str] = None, depth: Optional[str] = None):
    """获取跟踪列表"""
    return signal_tracking_service.list_tracking(
        status=status,
        signal=signal,
        code=code,
        window=window,
        model_mode=model_mode,
        depth=depth,
    )


@router.post("/tracking/{tracking_id}/close")
async def close_tracking(tracking_id: int, req: CloseRequest):
    """手动平仓"""
    return signal_tracking_service.close_tracking(tracking_id, req.exit_price)


@router.get("/stats")
async def get_stats(window: str = "all", model_mode: Optional[str] = None, depth: Optional[str] = None):
    """获取绩效统计"""
    return signal_tracking_service.get_stats(window=window, model_mode=model_mode, depth=depth)


@router.get("/signals/latest")
async def get_latest_signals():
    """获取每只股票的最新AI信号（用于自选股卡片显示）"""
    return signal_tracking_service.get_latest_signals()
