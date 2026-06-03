"""Quote application service."""

import asyncio

from fastapi import HTTPException

from data.helpers import get_session, _safe_float
from data.kline import get_kline, get_kline_with_ma, get_tencent_history_kline
from data.market import get_market_sentiment
from data.quote import get_batch_quotes, get_realtime_quote
from models.database import get_db
from repositories import quote_repository as repo


INDEX_CODES = {
    "sh": {"code": "000001", "name": "上证指数", "tencent": "sh000001"},
    "sz": {"code": "399001", "name": "深证成指", "tencent": "sz399001"},
    "cyb": {"code": "399006", "name": "创业板指", "tencent": "sz399006"},
}


async def get_batch(codes: str):
    code_list = [code.strip() for code in codes.split(",") if code.strip()]
    if not code_list:
        raise HTTPException(status_code=400, detail="codes 参数不能为空")
    if len(code_list) > 50:
        raise HTTPException(status_code=400, detail="单次最多查询50只股票")
    return await get_batch_quotes(code_list)


async def get_quote(code: str):
    result = await get_realtime_quote(code)
    if not result:
        raise HTTPException(status_code=404, detail=f"未找到股票 {code} 的行情数据")

    db = await get_db()
    try:
        position = await repo.get_position_cost(db, code)
    finally:
        await db.close()

    if position:
        avg_cost = position["avg_cost"]
        total_shares = position["total_shares"]
        price = result.get("price", 0)
        prev_close = result.get("prev_close", 0)
        if avg_cost and total_shares and price:
            result["avg_cost"] = avg_cost
            result["total_shares"] = total_shares
            result["unrealized_pnl"] = round((price - avg_cost) * total_shares, 3)
            result["unrealized_pnl_pct"] = round((price - avg_cost) / avg_cost * 100, 3)
        if prev_close and total_shares and price:
            result["daily_pnl"] = round((price - prev_close) * total_shares, 3)
    return result


def _kline_source_for_period(period: str) -> str:
    return "tencent_minute" if period in {"m1", "m5", "15", "30", "60"} else "mootdx"


def _frontend_time_value(row: dict, period: str):
    value = str(row.get("date") or "").strip()
    if not value:
        return ""
    return value if period in {"m1", "m5", "15", "30", "60"} else value.split(" ")[0]


def _normalize_kline_rows(rows) -> list[dict]:
    normalized = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item.setdefault("amount", 0)
        normalized.append(item)
    return normalized


def _kline_quality(rows: list[dict], period: str, requested_count: int) -> dict:
    issues: list[str] = []
    warnings: list[str] = []
    if not rows:
        return {"score": 0, "ok": False, "issues": ["empty_rows"], "warnings": warnings}

    duplicate_times = 0
    invalid_ohlc = 0
    invalid_fields = 0
    times = []
    for idx, row in enumerate(rows):
        try:
            open_price = float(row.get("open"))
            high = float(row.get("high"))
            low = float(row.get("low"))
            close = float(row.get("close"))
            float(row.get("volume"))
        except (TypeError, ValueError):
            invalid_fields += 1
            if invalid_fields <= 3:
                issues.append(f"invalid_numeric_row_{idx}")
            continue
        if min(open_price, high, low, close) <= 0 or high < max(open_price, close, low) or low > min(open_price, close, high):
            invalid_ohlc += 1
            if invalid_ohlc <= 3:
                issues.append(f"invalid_ohlc_row_{idx}")
        times.append(_frontend_time_value(row, period))

    duplicate_times = len(times) - len(set(times))
    if duplicate_times:
        issues.append(f"duplicate_time_values:{duplicate_times}")
    if len(rows) < min(requested_count, 20):
        warnings.append(f"short_series:{len(rows)}/{requested_count}")

    penalty = invalid_fields * 20 + invalid_ohlc * 20 + duplicate_times * 10 + (25 if warnings else 0)
    score = max(0, min(100, 100 - penalty))
    return {"score": score, "ok": not issues, "issues": issues, "warnings": warnings}


async def _get_baidu_daily_kline(code: str, count: int) -> list[dict]:
    payload = await get_kline_with_ma(code)
    rows = _normalize_kline_rows((payload or {}).get("kline") or [])
    return rows[-count:] if count and rows else rows


async def _get_tencent_history_rows(code: str, period: str, count: int) -> list[dict]:
    return _normalize_kline_rows(await asyncio.to_thread(get_tencent_history_kline, code, period, count))


async def get_kline_data(code: str, period: str, count: int):
    period = (period or "day").lower()
    source = _kline_source_for_period(period)
    attempts: list[dict] = []

    klines = _normalize_kline_rows(await asyncio.to_thread(get_kline, code, period, count))
    quality = _kline_quality(klines, period, count)
    attempts.append({"source": source, "count": len(klines), "ok": bool(klines), "score": quality["score"]})
    fallback_source = ""

    if not klines and period in {"day", "d", "week", "w", "month", "mon"}:
        tencent_rows = await _get_tencent_history_rows(code, period, count)
        tencent_quality = _kline_quality(tencent_rows, period, count)
        attempts.append({"source": "tencent_history", "count": len(tencent_rows), "ok": bool(tencent_rows), "score": tencent_quality["score"]})
        if tencent_rows:
            klines = tencent_rows
            quality = tencent_quality
            source = "tencent_history"
            fallback_source = "tencent_history"

    if not klines and period in {"day", "d"}:
        fallback_rows = await _get_baidu_daily_kline(code, count)
        fallback_quality = _kline_quality(fallback_rows, period, count)
        attempts.append({"source": "baidu_kline", "count": len(fallback_rows), "ok": bool(fallback_rows), "score": fallback_quality["score"]})
        if fallback_rows:
            klines = fallback_rows
            quality = fallback_quality
            source = "baidu_kline"
            fallback_source = "baidu_kline"

    quality = {**quality, "source_attempts": attempts}
    return {
        "code": code,
        "period": period,
        "count": len(klines),
        "klines": klines,
        "source": source,
        "fallback_source": fallback_source,
        "quality": quality,
        "quality_score": quality["score"],
        "issues": quality["issues"],
    }


async def get_indices():
    from data.helpers import TENCENT_QUOTE_URL

    url = TENCENT_QUOTE_URL + ",".join(item["tencent"] for item in INDEX_CODES.values())
    session = await get_session()
    async with session.get(url) as resp:
        raw_text = await resp.read()
        text = raw_text.decode("gbk", errors="replace")

    raw_map = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        var_part, _, val_part = line.partition("=")
        raw_code = var_part.split("_")[-1]
        fields = val_part.strip('" ;').split("~")
        if len(fields) < 35:
            continue
        raw_map[raw_code] = {
            "name": fields[1],
            "code": fields[2],
            "price": _safe_float(fields[3]),
            "prev_close": _safe_float(fields[4]),
            "open": _safe_float(fields[5]),
            "change": _safe_float(fields[31]),
            "change_pct": _safe_float(fields[32]),
            "high": _safe_float(fields[33]),
            "low": _safe_float(fields[34]),
            "volume": _safe_float(fields[6]),
            "amount": _safe_float(fields[37]),
        }

    return {
        key: raw_map.get(meta["tencent"], {"name": meta["name"], "code": meta["code"]})
        for key, meta in INDEX_CODES.items()
    }


async def get_market_sentiment_snapshot():
    return await get_market_sentiment()
