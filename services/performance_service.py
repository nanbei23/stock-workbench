"""Unified AI performance workspace service."""

from __future__ import annotations

from typing import Any

from models.database import get_db
from services import shadow_portfolio_service, signal_tracking_service


def _clean_filter(value: str | None) -> str | None:
    if value in ("", "all", None):
        return None
    return value


async def filter_options() -> dict[str, Any]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """
            SELECT DISTINCT COALESCE(model_mode, 'manual') AS model_mode,
                            COALESCE(depth, 'manual') AS depth
            FROM analysis_reports
            ORDER BY model_mode, depth
            """
        )
    finally:
        await db.close()
    model_modes = sorted({row["model_mode"] for row in rows if row["model_mode"]})
    depths = sorted({row["depth"] for row in rows if row["depth"]})
    return {
        "windows": [
            {"value": "7", "label": "近7天"},
            {"value": "30", "label": "近30天"},
            {"value": "90", "label": "近90天"},
            {"value": "all", "label": "全部"},
        ],
        "model_modes": model_modes,
        "depths": depths,
    }


async def overview(
    window: str = "all",
    model_mode: str | None = None,
    depth: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    model_mode = _clean_filter(model_mode)
    depth = _clean_filter(depth)
    limit = max(1, min(int(limit or 100), 500))

    signal_stats = signal_tracking_service.get_stats(
        window=window,
        model_mode=model_mode,
        depth=depth,
    )
    signal_tracking = signal_tracking_service.list_tracking(
        window=window,
        model_mode=model_mode,
        depth=depth,
    )
    summary = await shadow_portfolio_service.summary()
    positions = await shadow_portfolio_service.list_positions()
    comparison = await shadow_portfolio_service.comparison()
    orders = await shadow_portfolio_service.list_orders(
        limit=limit,
        window=window,
        model_mode=model_mode,
        depth=depth,
    )
    calibration = await shadow_portfolio_service.calibration(
        limit=limit,
        window=window,
        model_mode=model_mode,
        depth=depth,
    )
    deviation = await shadow_portfolio_service.execution_deviation()
    return {
        "filters": {
            "window": window,
            "model_mode": model_mode,
            "depth": depth,
            "options": await filter_options(),
        },
        "signal": {
            "stats": signal_stats,
            "tracking": signal_tracking,
        },
        "shadow": {
            "summary": summary,
            "positions": positions,
            "comparison": comparison,
            "orders": orders,
            "calibration": calibration,
            "deviation": deviation,
        },
    }
