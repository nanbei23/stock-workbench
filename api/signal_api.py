"""信号跟踪 REST API"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from scheduler import signal_tracker

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
    tracking_id = signal_tracker.create_tracking(
        report_id=0, code=req.code, name=req.name, signal=req.signal,
        entry_price=req.entry_price, target_price=req.target_price
    )
    if tracking_id is None:
        raise HTTPException(500, "创建跟踪失败")
    return {"id": tracking_id, "status": "open",
            "message": f"已添加跟踪：{req.name} {req.signal} @ ¥{req.entry_price}"}


@router.get("/tracking")
async def list_tracking(status: Optional[str] = None, signal: Optional[str] = None,
                        code: Optional[str] = None):
    """获取跟踪列表"""
    return signal_tracker.get_tracking_list(status=status, signal=signal, code=code)


@router.post("/tracking/{tracking_id}/close")
async def close_tracking(tracking_id: int, req: CloseRequest):
    """手动平仓"""
    ok = signal_tracker.close_tracking_manual(tracking_id, req.exit_price)
    if not ok:
        raise HTTPException(404, "跟踪记录不存在或已关闭")
    return {"message": "已平仓"}


@router.get("/stats")
async def get_stats():
    """获取绩效统计"""
    return signal_tracker.get_stats()


@router.get("/signals/latest")
async def get_latest_signals():
    """获取每只股票的最新AI信号（用于自选股卡片显示）"""
    try:
        tracking_list = signal_tracker.get_tracking_list(status="open")
        # 按code分组，取最新的
        latest = {}
        for t in tracking_list:
            code = t["code"]
            if code not in latest or t["id"] > latest[code]["id"]:
                latest[code] = t
        return {"signals": latest}
    except Exception as e:
        raise HTTPException(500, str(e))
