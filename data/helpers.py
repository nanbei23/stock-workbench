"""
炒股小牛马工作台 — 共享工具
aiohttp 异步 HTTP，零第三方封装（除 mootdx TCP）
"""
import asyncio
import json
import logging
import re
import socket
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ── 公共常量 ──────────────────────────────────────────────
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

HEADERS = {"User-Agent": UA}

# 东方财富数据中心统一接口
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# 东方财富推送接口
PUSH2_URL = "https://push2.eastmoney.com/api/qt/stock/get"
PUSH2_HIS_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
PUSH2_MINUTE_URL = "https://push2.eastmoney.com/api/qt/stock/trends2/get"

# 腾讯行情
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
TENCENT_BATCH_URL = "https://qt.gtimg.cn/q="

# aiohttp 全局 session
_session: Optional[aiohttp.ClientSession] = None
_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def get_session() -> aiohttp.ClientSession:
    """获取或创建全局 aiohttp session（惰性初始化，强制IPv4避免macOS IPv6问题）。"""
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        _session = aiohttp.ClientSession(
            timeout=_TIMEOUT,
            headers=HEADERS,
            connector=connector,
        )
    return _session


async def close_session():
    """关闭全局 session（应用关闭时调用）。"""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


# ── 市场前缀 ──────────────────────────────────────────────
def get_prefix(code: str) -> str:
    """
    根据股票代码返回市场前缀。
    60x/688 → sh, 00x/30x → sz, 北交所 8x/4x → bj
    也处理已带前缀的情况。
    """
    code = str(code).strip()
    if code.startswith(("sh", "sz", "bj")):
        return code[:2]
    # 沪市主板 + 科创板
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh"
    # 深市主板 + 创业板
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz"
    # 北交所
    if code.startswith(("8", "4")):
        return "bj"
    # 默认沪
    return "sh"


def _pure_code(code: str) -> str:
    """去掉 sh/sz/bj 前缀，返回纯数字代码。"""
    c = str(code).strip()
    if c.startswith(("sh", "sz", "bj")):
        return c[2:]
    return c


def _secid(code: str) -> str:
    """生成东方财富 secid 格式：1.600519 / 0.000001"""
    c = _pure_code(code)
    p = get_prefix(c)
    market = "1" if p == "sh" else "0"
    return f"{market}.{c}"


def _tencent_code(code: str) -> str:
    """生成腾讯行情代码格式：sh600519 / sz000001"""
    c = _pure_code(code)
    return f"{get_prefix(c)}{c}"


# ── 东方财富数据中心统一查询 ────────────────────────────────
async def eastmoney_datacenter(
    report_name: str,
    columns: str = "ALL",
    filter_str: str = "",
    sort_columns: str = "",
    sort_types: str = "-1",
    page_number: int = 1,
    page_size: int = 50,
    session: Optional[aiohttp.ClientSession] = None,
    **extra_params,
) -> list[dict]:
    """
    统一查询东方财富数据中心，返回结果列表。
    失败时返回空列表。
    """
    params = {
        "reportName": report_name,
        "columns": columns,
        "pageNumber": page_number,
        "pageSize": page_size,
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    if filter_str:
        params["filter"] = filter_str
    params.update(extra_params)

    _session = session or await get_session()
    try:
        async with _session.get(DATACENTER_URL, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            if data.get("success"):
                return data.get("result", {}).get("data", []) or []
            return []
    except Exception as e:
        logger.warning("eastmoney_datacenter(%s) error: %s", report_name, e)
        return []


# ── 腾讯批量行情 ──────────────────────────────────────────
async def tencent_quote_batch(
    codes: list[str],
    session: Optional[aiohttp.ClientSession] = None,
) -> dict[str, dict]:
    """
    批量获取腾讯实时行情，返回 {code: {name, price, change, ...}} 字典。
    codes 为纯代码列表，如 ['600519', '000001']。
    """
    if not codes:
        return {}
    tc_codes = [_tencent_code(c) for c in codes]
    url = TENCENT_QUOTE_URL + ",".join(tc_codes)
    _session = session or await get_session()
    try:
        async with _session.get(url) as resp:
            raw_text = await resp.read()
            text = raw_text.decode("gbk", errors="replace")
            result = {}
            for line in text.strip().split("\n"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                # v_sh600519="1~贵州茅台~600519~..."
                var_part, _, val_part = line.partition("=")
                raw_code = var_part.split("_")[-1]  # sh600519
                pure = raw_code[2:]  # 600519
                fields = val_part.strip('" ;').split("~")
                if len(fields) < 46:
                    continue
                result[pure] = {
                    "name": fields[1],
                    "code": pure,
                    "price": _safe_float(fields[3]),
                    "prev_close": _safe_float(fields[4]),
                    "open": _safe_float(fields[5]),
                    "volume": _safe_float(fields[6]),     # 手
                    "buy_volume": _safe_float(fields[7]),
                    "sell_volume": _safe_float(fields[8]),
                    "bid1": _safe_float(fields[9]),
                    "bid1_vol": _safe_float(fields[10]),
                    "ask1": _safe_float(fields[19]),
                    "ask1_vol": _safe_float(fields[20]),
                    "change": _safe_float(fields[31]),
                    "change_pct": _safe_float(fields[32]),
                    "high": _safe_float(fields[33]),
                    "low": _safe_float(fields[34]),
                    "amount": _safe_float(fields[37]),     # 万元
                    "turnover": _safe_float(fields[38]),   # 换手率%
                    "pe": _safe_float(fields[39]),
                    "amplitude": _safe_float(fields[43]),  # 振幅%
                    "circ_market_cap": _safe_float(fields[44]),  # 流通市值
                    "total_market_cap": _safe_float(fields[45]),  # 总市值
                    "raw": fields,
                }
            return result
    except Exception as e:
        logger.warning("tencent_quote_batch error: %s", e)
        return {}


def _safe_float(s: str, default: float = 0.0) -> float:
    try:
        return float(s) if s and s != "" else default
    except (ValueError, TypeError):
        return default
