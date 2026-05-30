"""Signal tracking application service."""

from fastapi import HTTPException

from scheduler import signal_tracker


def add_tracking(req):
    tracking_id = signal_tracker.create_tracking(
        report_id=0,
        code=req.code,
        name=req.name,
        signal=req.signal,
        entry_price=req.entry_price,
        target_price=req.target_price,
    )
    if tracking_id is None:
        raise HTTPException(status_code=500, detail="创建跟踪失败")
    return {
        "id": tracking_id,
        "status": "open",
        "message": f"已添加跟踪：{req.name} {req.signal} @ ¥{req.entry_price}",
    }


def list_tracking(status=None, signal=None, code=None):
    return signal_tracker.get_tracking_list(status=status, signal=signal, code=code)


def close_tracking(tracking_id: int, exit_price: float):
    ok = signal_tracker.close_tracking_manual(tracking_id, exit_price)
    if not ok:
        raise HTTPException(status_code=404, detail="跟踪记录不存在或已关闭")
    return {"message": "已平仓"}


def get_stats():
    return signal_tracker.get_stats()


def get_latest_signals():
    latest = {}
    for item in signal_tracker.get_tracking_list(status="open"):
        code = item["code"]
        if code not in latest or item["id"] > latest[code]["id"]:
            latest[code] = item
    return {"signals": latest}
