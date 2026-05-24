"""
炒股小牛马工作台 — Layer 1.5 K线
日/周/月: mootdx TCP
分时(m1/m5/15/30/60): 腾讯财经分时API（mootdx不支持真正的分钟级数据）
"""
import logging
import re
from typing import Optional

import aiohttp
import requests

from data.helpers import _pure_code, get_prefix, UA
from cache.shared_cache import cache

logger = logging.getLogger(__name__)

# mootdx K线 category 映射（仅日/周/月）
CATEGORY_MAP = {
    "day": 4,
    "d": 4,
    "week": 5,
    "w": 5,
    "month": 6,
    "mon": 6,
}

# 分钟级周期
INTRADAY_PERIODS = {"m1", "m5", "15", "30", "60"}

# 腾讯分时API
TENCENT_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"

_mootdx_client = None
_mootdx_tried = False


def _get_mootdx_client():
    """懒加载 mootdx Client。"""
    global _mootdx_client, _mootdx_tried
    if _mootdx_tried:
        return _mootdx_client
    _mootdx_tried = True
    try:
        from mootdx.quotes import Quotes
        _mootdx_client = Quotes.factory(market="std")
        logger.info("mootdx K线客户端初始化成功")
    except Exception as e:
        logger.warning("mootdx K线初始化失败: %s", e)
        _mootdx_client = None
    return _mootdx_client


def _tencent_prefix(code: str) -> str:
    """腾讯API需要的前缀: sh/sz"""
    code = _pure_code(code)
    if code.startswith(('6', '9')):
        return f"sh{code}"
    return f"sz{code}"


def _get_tencent_minute(code: str) -> list[dict]:
    """
    从腾讯API获取当日分时数据。
    返回格式: [{date: "YYYY-MM-DD HH:MM", open, high, low, close, volume, amount}, ...]
    """
    code = _pure_code(code)
    full_code = _tencent_prefix(code)
    try:
        resp = requests.get(
            TENCENT_MINUTE_URL,
            params={"_var": f"min_data_{full_code}", "code": full_code},
            headers={"User-Agent": UA},
            timeout=10,
            proxies={"http": "", "https": ""},
        )
        text = resp.text
        # 格式: min_data_sh601138={"code":0,"data":{"sh601138":{"data":{"data":["0930 66.11 14543 ..."], "date":"20260522"}}}}
        # 提取JSON部分
        m = re.search(r'=(\{.*\})', text)
        if not m:
            return []
        import json
        data = json.loads(m.group(1))
        stock_data = data.get("data", {}).get(full_code, {}).get("data", {})
        lines = stock_data.get("data", [])
        date_str = stock_data.get("date", "")  # "20260522"
        if not lines or not date_str:
            return []

        # 格式: "0930 66.11 14543 96143772.56"
        # = HHMM price cumulative_volume cumulative_amount
        dt_prefix = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        records = []
        prev_vol = 0
        prev_amount = 0
        for line in lines:
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            hhmm, price_str, cum_vol_str, cum_amt_str = parts[0], parts[1], parts[2], parts[3]
            try:
                price = float(price_str)
                cum_vol = float(cum_vol_str)
                cum_amt = float(cum_amt_str)
            except ValueError:
                continue

            # 分钟级volume = 累积量差值
            vol = max(0, cum_vol - prev_vol)
            amt = max(0, cum_amt - prev_amount)
            prev_vol = cum_vol
            prev_amount = cum_amt

            # HHMM → HH:MM
            time_str = f"{hhmm[:2]}:{hhmm[2:]}"
            dt = f"{dt_prefix} {time_str}"

            # 分时图: OHLC全用同一价格（单tick = 平蜡烛）
            records.append({
                "date": dt,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": vol,
                "amount": amt,
            })
        return records
    except Exception as e:
        logger.warning("get_tencent_minute(%s) error: %s", code, e)
        return []


def _aggregate_minutes(records: list[dict], interval: int) -> list[dict]:
    """将1分钟数据聚合成N分钟K线"""
    if interval <= 1 or not records:
        return records

    result = []
    i = 0
    while i < len(records):
        chunk = records[i:i + interval]
        o = chunk[0]["open"]
        c = chunk[-1]["close"]
        h = max(r["high"] for r in chunk)
        lo = min(r["low"] for r in chunk)
        vol = sum(r["volume"] for r in chunk)
        amt = sum(r["amount"] for r in chunk)
        result.append({
            "date": chunk[0]["date"],
            "open": o, "high": h, "low": lo, "close": c,
            "volume": vol, "amount": amt,
        })
        i += interval
    return result


def get_kline(code: str, period: str = "day", count: int = 120) -> list[dict]:
    """
    获取K线数据。
    分钟级(m1/m5/15/30/60): 腾讯分时API（当日数据）
    日/周/月: mootdx TCP

    返回 list[dict]，每条含 date/open/high/low/close/volume/amount。
    """
    code = _pure_code(code)

    # 分钟级 → 腾讯API
    if period in INTRADAY_PERIODS:
        records = _get_tencent_minute(code)
        if not records:
            return []
        # 聚合间隔
        interval_map = {"m1": 1, "m5": 5, "15": 15, "30": 30, "60": 60}
        interval = interval_map.get(period, 1)
        if interval > 1:
            records = _aggregate_minutes(records, interval)
        return records[-count:] if count else records

    # 日/周/月 → mootdx
    client = _get_mootdx_client()
    if client is None:
        return []

    category = CATEGORY_MAP.get(period, 4)
    try:
        df = client.bars(category=category, symbol=code, offset=count)
        if df is None or df.empty:
            return []

        records = []
        for _, row in df.iterrows():
            records.append({
                "date": str(row.get("datetime", "")),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("vol", 0) or row.get("volume", 0)),
                "amount": float(row.get("amount", 0)),
            })
        return records
    except Exception as e:
        logger.warning("get_kline(%s, %s) error: %s", code, period, e)
        return []


async def get_kline_with_ma(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """
    通过百度 API 获取日 K 线 + MA（均线）数据。
    返回 dict：
      {kline: [{date, open, high, low, close, volume}, ...],
       ma5: [...], ma10: [...], ma20: [...]}
    失败返回空 dict。
    """
    from data.helpers import get_session

    code = _pure_code(code)
    cached = cache.read('klines', code)
    if cached is not None:
        return cached
    prefix = get_prefix(code)
    # 百度需要全码如 sh600519
    full_code = f"{prefix}{code}"

    params = {
        "srcid": "5353",
        "all": 1,
        "is498": 1,
        "isBk": "false",
        "isBlock": "false",
        "isFutures": "false",
        "isStock": "true",
        "newFormat": 1,
        "group": "quotation_kline_ab",
        "code": full_code,
        "market_type": "ab" if prefix in ("sh", "sz") else "hk",
        "finClientType": "pc",
    }

    _session = session or await get_session()
    try:
        async with _session.get(
            BAIDU_STOCK_URL,
            params=params,
            headers={"User-Agent": UA},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

            result = {"kline": [], "ma5": [], "ma10": [], "ma20": []}

            # 百度返回结构中 Result 里有各种数据
            Result = data.get("Result", {})
            # Result 本身可能是 list 或 dict
            if isinstance(Result, list):
                for item in Result:
                    if isinstance(item, dict):
                        Result = item
                        break
                else:
                    return result

            # 尝试提取 K 线
            kline_data = Result.get("DisplayData", {}).get("resultData", {})
            if not kline_data:
                # 另一种结构
                kline_data = Result.get("pankouinfos", {})

            # 尝试从 Result 中直接找 K 线列表
            for key in ("priceinfos", "klineinfos", "OriginalData"):
                if key in Result and isinstance(Result[key], list):
                    for row in Result[key]:
                        if isinstance(row, dict):
                            result["kline"].append({
                                "date": str(row.get("date", row.get("time", ""))),
                                "open": float(row.get("open", 0)),
                                "high": float(row.get("high", 0)),
                                "low": float(row.get("low", 0)),
                                "close": float(row.get("close", 0) or row.get("price", 0)),
                                "volume": float(row.get("volume", row.get("vol", 0))),
                            })
                    break

            # 提取均线
            for key in Result:
                if "ma5" in str(key).lower():
                    vals = Result[key]
                    if isinstance(vals, list):
                        result["ma5"] = [float(v) if v else 0 for v in vals]
                elif "ma10" in str(key).lower():
                    vals = Result[key]
                    if isinstance(vals, list):
                        result["ma10"] = [float(v) if v else 0 for v in vals]
                elif "ma20" in str(key).lower():
                    vals = Result[key]
                    if isinstance(vals, list):
                        result["ma20"] = [float(v) if v else 0 for v in vals]

            cache.write('klines', code, result)
            return result
    except Exception as e:
        logger.warning("get_kline_with_ma(%s) error: %s", code, e)
        return {}
