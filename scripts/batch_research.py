#!/usr/bin/env python3
"""Offline batch research runner for watchlist stocks.

This script is intentionally detached from onboarding. It lets the user warm
market data, rank candidates, and then submit a controlled number of AI debate
tasks in small batches.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import math
import os
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DB_PATH  # noqa: E402
from data.kline import get_kline  # noqa: E402
from data.quote import get_batch_quotes  # noqa: E402
from models.database import SCHEMA  # noqa: E402
from scheduler.ai_engine import extract_confidence, extract_risk_score, extract_signal, extract_target_price  # noqa: E402
from services import ai_analysis_service, ai_task_service  # noqa: E402


TERMINAL_STATUS = {"completed", "failed", "timeout", "cancelled"}
SNAPSHOT_LAYERS = ("market", "social", "news", "fundamentals", "policy", "hot_money", "lockup")
POSITIVE_SIGNALS = {"STRONG_BUY", "BUY", "OVERWEIGHT", "ACCUMULATE", "ADD"}
WATCH_SIGNALS = {"HOLD", "WATCH", "NEUTRAL"}
SINA_FINANCIAL_API = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
EASTMONEY_DATACENTER_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SINA_REPORT_SOURCES = {
    "资产负债表": ("fzb", "Balance Sheet"),
    "现金流量表": ("llb", "Cash Flow"),
    "利润表": ("lrb", "Income Statement"),
}
EASTMONEY_FINANCIAL_REPORTS = {
    "资产负债表": ("RPT_DMSK_FN_BALANCE", "Balance Sheet"),
    "现金流量表": ("RPT_DMSK_FN_CASHFLOW", "Cash Flow"),
    "利润表": ("RPT_DMSK_FN_INCOME", "Income Statement"),
}
SEMANTIC_TOOL_FAILURE_PATTERNS = (
    "no quote data found",
    "no balance sheet data found",
    "no cash flow data found",
    "no cashflow data found",
    "no income statement data found",
    "error retrieving",
    "no available vendor",
)


@dataclass(frozen=True)
class StockCandidate:
    code: str
    name: str
    group_name: str
    sort_order: int = 0


@dataclass
class RankedCandidate:
    code: str
    name: str
    group_name: str
    sort_order: int
    score: float
    quote: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    status: str = "planned"
    error: str | None = None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _loads(value: Any, fallback: Any):
    if value in ("", None):
        return fallback
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return parsed if parsed is not None else fallback
    except (TypeError, ValueError):
        return fallback


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def _recent_report_codes(conn: sqlite3.Connection, days: int) -> set[str]:
    if days <= 0:
        return set()
    rows = conn.execute(
        """
        SELECT code
        FROM analysis_reports
        WHERE created_at >= datetime('now', ?)
        """,
        (f"-{days} days",),
    ).fetchall()
    return {row["code"] for row in rows}


def recent_report_codes(db_path: Path, days: int) -> set[str]:
    with _connect(db_path) as conn:
        return _recent_report_codes(conn, days)


def load_candidates(
    db_path: Path,
    *,
    group: str = "默认",
    include_observation: bool = False,
    skip_recent_days: int = 7,
) -> list[StockCandidate]:
    with _connect(db_path) as conn:
        recent = _recent_report_codes(conn, skip_recent_days)
        params: list[Any] = []
        where = ""
        if group not in {"all", "全部", "*"}:
            if include_observation and group == "默认":
                where = "WHERE COALESCE(group_name, '默认') IN (?, ?)"
                params.extend(["默认", "观察池"])
            else:
                where = "WHERE COALESCE(group_name, '默认') = ?"
                params.append(group)
        rows = conn.execute(
            f"""
            SELECT code, name, COALESCE(group_name, '默认') AS group_name, COALESCE(sort_order, 0) AS sort_order
            FROM watchlist
            {where}
            ORDER BY CASE WHEN COALESCE(group_name, '默认') = '观察池' THEN 1 ELSE 0 END,
                     sort_order ASC,
                     added_at ASC
            """,
            params,
        ).fetchall()
    return [
        StockCandidate(row["code"], row["name"] or row["code"], row["group_name"], int(row["sort_order"] or 0))
        for row in rows
        if row["code"] not in recent
    ]


def _quote_score(quote: dict[str, Any]) -> float:
    change = float(quote.get("change_pct") or 0)
    amount = float(quote.get("amount") or quote.get("turnover") or 0)
    amount_score = min(12.0, math.log10(amount + 1) if amount > 0 else 0)
    return max(-8.0, min(12.0, change)) * 1.4 + amount_score


def rank_candidates(
    stocks: list[StockCandidate],
    quotes: dict[str, dict[str, Any]],
    *,
    top_n: int = 15,
) -> list[RankedCandidate]:
    ranked: list[RankedCandidate] = []
    for stock in stocks:
        quote = quotes.get(stock.code, {}) or {}
        group_score = 20.0 if stock.group_name != "观察池" else 0.0
        order_score = max(0.0, 10.0 - (stock.sort_order / 20.0))
        score = round(group_score + order_score + _quote_score(quote), 3)
        ranked.append(
            RankedCandidate(
                code=stock.code,
                name=stock.name,
                group_name=stock.group_name,
                sort_order=stock.sort_order,
                score=score,
                quote=quote,
            )
        )
    ranked.sort(key=lambda item: (item.score, -item.sort_order), reverse=True)
    return ranked[:top_n] if top_n > 0 else ranked


async def fetch_quotes_for(stocks: list[StockCandidate], chunk_size: int = 50) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    codes = [stock.code for stock in stocks]
    for idx in range(0, len(codes), max(1, chunk_size)):
        chunk = codes[idx : idx + chunk_size]
        try:
            result.update(await get_batch_quotes(chunk))
        except Exception as exc:
            for code in chunk:
                result.setdefault(code, {"error": str(exc)})
    return result


def _json_default(value: Any) -> str:
    return str(value)


def _clip_text(value: Any, limit: int = 20000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=_json_default)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _pure_stock_code(code: str) -> str:
    match = re.search(r"(\d{6})", str(code or ""))
    return match.group(1) if match else str(code or "").strip()


def _sina_paper_code(code: str) -> str:
    pure = _pure_stock_code(code)
    if pure.startswith(("4", "8")):
        return f"bj{pure}"
    return f"{'sh' if pure.startswith('6') else 'sz'}{pure}"


def _report_date_allowed(date_key: str, freq: str, curr_date: str | None) -> bool:
    key = re.sub(r"\D", "", str(date_key or ""))
    if curr_date:
        cutoff = re.sub(r"\D", "", curr_date)
        if cutoff and key and key > cutoff:
            return False
    if str(freq or "").lower() == "annual" and not key.endswith("1231"):
        return False
    return True


def _csv_text(headers: list[str], rows: list[list[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue()


def _format_sina_financial_report(
    code: str,
    report_type: str,
    freq: str,
    payload: dict[str, Any],
    *,
    curr_date: str | None = None,
    source: str = "sina direct HTTP",
    retrieved_at: str | None = None,
) -> str:
    source_key, english_title = SINA_REPORT_SOURCES.get(report_type, ("lrb", report_type))
    data = (((payload or {}).get("result") or {}).get("data") or {})
    report_list = data.get("report_list") or {}
    report_dates = {
        str(item.get("date_value")): item.get("date_description") or str(item.get("date_value"))
        for item in data.get("report_date") or []
        if isinstance(item, dict)
    }
    if not isinstance(report_list, dict) or not report_list:
        return f"No {english_title.lower()} data found for A-stock '{_pure_stock_code(code)}'"

    ordered_dates = [str(item.get("date_value")) for item in data.get("report_date") or [] if isinstance(item, dict)]
    ordered_dates.extend([key for key in report_list if key not in ordered_dates])
    rows: list[list[Any]] = []
    for date_key in ordered_dates:
        if not _report_date_allowed(date_key, freq, curr_date):
            continue
        report = report_list.get(date_key) or {}
        items = report.get("data") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("item_title") or item.get("item_name") or item.get("item_field") or ""
            value = item.get("item_value")
            if title == "" and value in ("", None):
                continue
            rows.append([
                date_key,
                report_dates.get(str(date_key), str(date_key)),
                report.get("rType") or "",
                report.get("rCurrency") or "",
                title,
                "" if value is None else value,
                item.get("item_field") or "",
                item.get("item_tongbi") if item.get("item_tongbi") is not None else "",
            ])
        if len({row[0] for row in rows}) >= 8:
            break

    if not rows:
        return f"No {english_title.lower()} data found for A-stock '{_pure_stock_code(code)}'"

    headers = ["report_date", "report_name", "rType", "rCurrency", "item_title", "item_value", "item_field", "item_yoy"]
    header = f"# {english_title} for {_pure_stock_code(code)} (A-stock, {freq})\n"
    header += f"# Data source: {source}\n"
    header += f"# Data retrieved on: {retrieved_at or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    return header + _csv_text(headers, rows)


def _format_eastmoney_financial_report(
    code: str,
    report_type: str,
    rows_payload: list[dict[str, Any]],
    *,
    curr_date: str | None = None,
    source: str = "eastmoney datacenter fallback",
    retrieved_at: str | None = None,
) -> str:
    _report_name, english_title = EASTMONEY_FINANCIAL_REPORTS.get(report_type, ("", report_type))
    rows = [row for row in rows_payload or [] if isinstance(row, dict)]
    if curr_date:
        cutoff = re.sub(r"\D", "", curr_date)
        rows = [
            row for row in rows
            if not re.sub(r"\D", "", str(row.get("REPORT_DATE") or "")) or re.sub(r"\D", "", str(row.get("REPORT_DATE") or "")) <= cutoff
        ]
    rows.sort(key=lambda row: str(row.get("REPORT_DATE") or ""), reverse=True)
    rows = rows[:8]
    if not rows:
        return f"No {english_title.lower()} data found for A-stock '{_pure_stock_code(code)}'"

    metadata_keys = {
        "SECUCODE",
        "SECURITY_CODE",
        "SECURITY_NAME_ABBR",
        "ORG_CODE",
        "INDUSTRY_CODE",
        "INDUSTRY_NAME",
        "MARKET",
        "SECURITY_TYPE_CODE",
        "TRADE_MARKET_CODE",
        "DATE_TYPE_CODE",
        "REPORT_TYPE_CODE",
        "DATA_STATE",
    }
    csv_rows: list[list[Any]] = []
    for row in rows:
        report_date = row.get("REPORT_DATE") or ""
        notice_date = row.get("NOTICE_DATE") or ""
        for key, value in row.items():
            if key in metadata_keys or key in {"REPORT_DATE", "NOTICE_DATE"}:
                continue
            if value in (None, ""):
                continue
            csv_rows.append([
                report_date,
                notice_date,
                row.get("SECURITY_NAME_ABBR") or "",
                key,
                value,
            ])
    if not csv_rows:
        return f"No {english_title.lower()} data found for A-stock '{_pure_stock_code(code)}'"

    headers = ["report_date", "notice_date", "security_name", "item_field", "item_value"]
    header = f"# {english_title} for {_pure_stock_code(code)} (A-stock)\n"
    header += f"# Data source: {source}\n"
    header += f"# Data retrieved on: {retrieved_at or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    return header + _csv_text(headers, csv_rows)


def _fetch_eastmoney_financial_report(
    code: str,
    report_type: str,
    *,
    curr_date: str | None = None,
) -> str:
    report_name, english_title = EASTMONEY_FINANCIAL_REPORTS.get(report_type, ("", report_type))
    if not report_name:
        return f"No {english_title.lower()} data found for A-stock '{_pure_stock_code(code)}'"
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
            resp = client.get(
                EASTMONEY_DATACENTER_API,
                params={
                    "reportName": report_name,
                    "columns": "ALL",
                    "filter": f'(SECURITY_CODE="{_pure_stock_code(code)}")',
                    "sortColumns": "REPORT_DATE",
                    "sortTypes": "-1",
                    "pageNumber": "1",
                    "pageSize": "8",
                    "source": "WEB",
                    "client": "WEB",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        rows = (((payload or {}).get("result") or {}).get("data") or []) if payload.get("success") else []
        return _format_eastmoney_financial_report(code, report_type, rows, curr_date=curr_date)
    except Exception as exc:
        return f"Error retrieving {english_title.lower()} for {_pure_stock_code(code)} from Eastmoney: {exc}"


def _fetch_sina_financial_report(
    code: str,
    report_type: str,
    *,
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> str:
    source_key, english_title = SINA_REPORT_SOURCES.get(report_type, ("lrb", report_type))
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
            resp = client.get(
                SINA_FINANCIAL_API,
                params={
                    "paperCode": _sina_paper_code(code),
                    "source": source_key,
                    "type": "0",
                    "page": "1",
                    "num": "20",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        rendered = _format_sina_financial_report(code, report_type, freq, payload, curr_date=curr_date)
        if _looks_like_semantic_tool_failure(rendered):
            fallback = _fetch_eastmoney_financial_report(code, report_type, curr_date=curr_date)
            if not _looks_like_semantic_tool_failure(fallback):
                return fallback
            return rendered + "\n# Eastmoney fallback also returned no usable data."
        return rendered
    except Exception as exc:
        fallback = _fetch_eastmoney_financial_report(code, report_type, curr_date=curr_date)
        if not _looks_like_semantic_tool_failure(fallback):
            return fallback
        return f"Error retrieving {english_title.lower()} for {_pure_stock_code(code)}: {exc}"


def _looks_like_semantic_tool_failure(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("ok") is False:
            return True
        return any(_looks_like_semantic_tool_failure(item) for item in value.values())
    if isinstance(value, list):
        return any(_looks_like_semantic_tool_failure(item) for item in value)
    text = str(value or "").strip().lower()
    return any(pattern in text for pattern in SEMANTIC_TOOL_FAILURE_PATTERNS)


def _semantic_tool_error(key: str, item: Any) -> str | None:
    if isinstance(item, dict) and item.get("ok") is False:
        return f"{key}: {item.get('error') or 'unknown error'}"
    if isinstance(item, dict) and _looks_like_semantic_tool_failure(item.get("payload")):
        payload = str(item.get("payload") or "").replace("\r", " ").splitlines()
        message = payload[0] if payload else "semantic data failure"
        return f"{key}: {message[:220]}"
    if _looks_like_semantic_tool_failure(item):
        message = str(item or "").replace("\r", " ").splitlines()[0]
        return f"{key}: {message[:220]}"
    return None


def _chat_completions_url(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _settings_map(db_path: Path) -> dict[str, str]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def _snapshot_llm_config(db_path: Path, *, model_tier: str = "deep") -> dict[str, str]:
    settings = _settings_map(db_path)
    provider = (settings.get("llm_provider") or "deepseek").upper()
    api_key = (
        settings.get("api_key")
        or os.environ.get(f"{provider}_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    )
    model = settings.get("deep_think_model" if model_tier == "deep" else "quick_think_model") or ""
    if not model:
        model = settings.get("quick_think_model") or settings.get("deep_think_model") or ""
    return {
        "base_url": settings.get("custom_endpoint") or "",
        "api_key": api_key,
        "model": model,
    }


def _verification_llm_config(db_path: Path) -> dict[str, str]:
    settings = _settings_map(db_path)
    return {
        "base_url": settings.get("verification_endpoint") or "",
        "api_key": settings.get("verification_api_key") or "",
        "model": settings.get("verification_model") or "",
    }


def _latest_snapshot(db_path: Path, code: str) -> dict[str, Any] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, code, name, snapshot_json, validation_json, summary_json, created_at
            FROM stock_data_snapshots
            WHERE code = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (code,),
        ).fetchone()
    if not row:
        return None
    snapshot = json.loads(row["snapshot_json"] or "{}")
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "snapshot": snapshot,
        "validation": validate_snapshot(snapshot),
        "summary": json.loads(row["summary_json"] or "{}"),
        "created_at": row["created_at"],
    }


def _has_complete_snapshot(db_path: Path, code: str) -> bool:
    row = _latest_snapshot(db_path, code)
    return bool(row and (row.get("validation") or {}).get("ok"))


def _snapshot_prompt(stock: RankedCandidate, snapshot_row: dict[str, Any]) -> str:
    snapshot = snapshot_row["snapshot"]
    validation = snapshot_row["validation"]
    payload = {
        "stock": {"code": stock.code, "name": stock.name, "group": stock.group_name},
        "quote": stock.quote,
        "snapshot_id": snapshot_row["id"],
        "validation": validation,
        "layers": {
            layer: snapshot.get(layer)
            for layer in SNAPSHOT_LAYERS
        },
    }
    return f"""你是一个严谨的 A 股研究 Agent。请只基于下方已入库七层数据快照生成分析报告，禁止自行联网，禁止编造快照没有提供的数据。

输出必须是 JSON 对象，不要 Markdown，不要解释。字段如下：
{{
  "signal": "STRONG_BUY|BUY|OVERWEIGHT|HOLD|UNDERWEIGHT|SELL|STRONG_SELL",
  "confidence": 0.0,
  "risk_score": 0.0,
  "market_report": "技术和价格行为分析",
  "sentiment_report": "情绪和社交舆情分析",
  "news_report": "新闻舆情分析",
  "fundamentals_report": "基本面分析",
  "policy_report": "政策和宏观相关性分析",
  "hot_money_report": "资金和游资线索分析",
  "lockup_report": "解禁、减持、股东变化风险分析",
  "investment_debate": "多空双方辩论摘要",
  "risk_debate": "激进、保守、中性三类风控观点",
  "trader_plan": "如果需要建仓，给出分批、触发、止损、失效条件；如果不建仓，说明等待条件",
  "final_decision": "最终裁决，必须明确给出信号、置信度、风险评分和理由"
}}

约束：
- confidence 为 0 到 1。
- risk_score 为 0 到 100，数值越高风险越高。
- 如果快照完整性校验不是 ok，必须降低置信度并在 final_decision 中说明缺失项。
- 不允许出现“根据最新网络数据”等未提供来源的话。

七层数据快照：
{_clip_text(payload, 28000)}
"""


def _parse_llm_json(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", stripped)
        if not match:
            return {"final_decision": stripped}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"final_decision": stripped}


async def _call_snapshot_llm(prompt: str, config: dict[str, str], *, timeout_seconds: int) -> dict[str, Any]:
    if not config.get("base_url"):
        raise RuntimeError("AI 引擎 Base URL 未配置，无法生成快照报告")
    if not config.get("api_key"):
        raise RuntimeError("AI 引擎 API Key 未配置，无法生成快照报告")
    if not config.get("model"):
        raise RuntimeError("AI 引擎模型未配置，无法生成快照报告")
    async with httpx.AsyncClient(timeout=max(30, timeout_seconds)) as client:
        resp = await client.post(
            _chat_completions_url(config["base_url"]),
            headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
            json={
                "model": config["model"],
                "messages": [
                    {"role": "system", "content": "你是严谨的 A 股研究报告生成器，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 4000,
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"快照报告模型请求失败 HTTP {resp.status_code}: {resp.text[:240]}")
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return _parse_llm_json(content)


SNAPSHOT_DEBATE_ROLES = [
    ("market_analyst", "市场/技术分析师", "只基于 market 层和行情摘要，分析价格结构、趋势、量能和关键触发位。"),
    ("fundamental_analyst", "基本面分析师", "只基于 fundamentals 层，分析财务质量、估值、成长性和基本面风险。"),
    ("event_sentiment_analyst", "事件/情绪/资金分析师", "基于 social、news、policy、hot_money、lockup 层，分析催化、舆情、政策、资金和解禁减持风险。"),
    ("bull_researcher", "多头研究员", "基于前三位分析师观点，提出支持买入或增持的最强论据和触发条件。"),
    ("bear_researcher", "空头研究员", "基于快照和已有观点，反驳多头观点，指出弱证据、下行风险和不建仓理由。"),
    ("risk_manager", "风控经理", "综合多空观点，给出仓位、止损、失效条件、回撤和风险评分意见。"),
    ("final_trader", "交易员/最终裁决", "综合所有角色观点，输出最终 JSON 交易裁决。"),
]


def _snapshot_debate_prompt(
    stock: RankedCandidate,
    snapshot_row: dict[str, Any],
    *,
    role_name: str,
    role_goal: str,
    previous_discussion: list[dict[str, str]],
) -> str:
    previous = "\n\n".join(f"## {item['role_name']}\n{item['content']}" for item in previous_discussion) or "暂无，当前为第一位角色。"
    final_instruction = ""
    if role_name == "交易员/最终裁决":
        final_instruction = """

最终裁决必须输出 JSON 对象，不要 Markdown。格式：
{
  "signal": "STRONG_BUY|BUY|OVERWEIGHT|HOLD|UNDERWEIGHT|SELL|STRONG_SELL",
  "confidence": 0.0,
  "risk_score": 0.0,
  "trader_plan": "分批、触发、止损、失效条件；如果不建仓，说明等待条件",
  "final_decision": "最终裁决，必须明确给出信号、置信度、风险评分和理由"
}
"""
    payload = {
        "stock": {"code": stock.code, "name": stock.name, "group": stock.group_name},
        "quote": stock.quote,
        "snapshot_id": snapshot_row["id"],
        "validation": snapshot_row["validation"],
        "layers": {layer: snapshot_row["snapshot"].get(layer) for layer in SNAPSHOT_LAYERS},
    }
    return f"""你正在参加 A 股单票研究委员会。当前角色：{role_name}。
角色目标：{role_goal}

硬性约束：
- 只能基于下方已入库七层数据快照和已有角色观点。
- 禁止联网，禁止调用外部数据，禁止编造快照没有提供的数据。
- 如果快照完整性校验不是 ok，必须降低置信度并说明缺失项。
- 输出要具体、可审计，区分事实、推断和不确定性。
{final_instruction}

已有角色讨论：
{previous}

七层数据快照：
{_clip_text(payload, 28000)}
"""


async def _call_snapshot_debate_role_llm(role: dict[str, str], prompt: str, config: dict[str, str], *, timeout_seconds: int) -> str:
    if not config.get("base_url"):
        raise RuntimeError("AI 引擎 Base URL 未配置，无法生成快照多角色辩论报告")
    if not config.get("api_key"):
        raise RuntimeError("AI 引擎 API Key 未配置，无法生成快照多角色辩论报告")
    if not config.get("model"):
        raise RuntimeError("AI 引擎模型未配置，无法生成快照多角色辩论报告")
    async with httpx.AsyncClient(timeout=max(30, timeout_seconds)) as client:
        resp = await client.post(
            _chat_completions_url(config["base_url"]),
            headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
            json={
                "model": config["model"],
                "messages": [
                    {"role": "system", "content": f"你是{role['role_name']}，参与基于已入库七层快照的离线多角色研究辩论。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2 if role["role_key"] == "final_trader" else 0.28,
                "max_tokens": 4000,
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"快照多角色辩论模型请求失败 HTTP {resp.status_code}: {resp.text[:240]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _snapshot_debate_result(role_discussion: list[dict[str, str]]) -> dict[str, Any]:
    by_key = {item["role_key"]: item["content"] for item in role_discussion}
    final_payload = _parse_llm_json(by_key.get("final_trader", ""))
    return {
        **final_payload,
        "market_report": by_key.get("market_analyst", ""),
        "fundamentals_report": by_key.get("fundamental_analyst", ""),
        "sentiment_report": by_key.get("event_sentiment_analyst", ""),
        "news_report": by_key.get("event_sentiment_analyst", ""),
        "policy_report": by_key.get("event_sentiment_analyst", ""),
        "hot_money_report": by_key.get("event_sentiment_analyst", ""),
        "lockup_report": by_key.get("event_sentiment_analyst", ""),
        "investment_debate": "\n\n".join(
            part
            for part in [
                f"多头研究员：{by_key.get('bull_researcher', '')}",
                f"空头研究员：{by_key.get('bear_researcher', '')}",
            ]
            if part.strip()
        ),
        "risk_debate": by_key.get("risk_manager", ""),
        "role_discussion": role_discussion,
    }


SNAPSHOT_TRADINGAGENTS_ROLES = [
    ("market_analyst", "市场/技术分析师", "market_report", "只读取 market 层，输出技术、趋势、量能、关键价位和交易触发位。"),
    ("social_analyst", "情绪分析师", "sentiment_report", "只读取 social 层，输出情绪、社交讨论和市场关注度判断。"),
    ("news_analyst", "新闻分析师", "news_report", "只读取 news 层，输出新闻催化、负面事件和信息时效判断。"),
    ("fundamentals_analyst", "基本面分析师", "fundamentals_report", "只读取 fundamentals 层，输出财务质量、估值、成长性和基本面风险。"),
    ("policy_analyst", "政策分析师", "policy_report", "只读取 policy 层，输出政策、监管、宏观和行业方向影响。"),
    ("hot_money_analyst", "游资/资金分析师", "hot_money_report", "只读取 hot_money 层，输出资金流、游资、题材热度和拥挤风险。"),
    ("lockup_analyst", "解禁/减持分析师", "lockup_report", "只读取 lockup 层，输出解禁、减持、股东变化和供给压力。"),
    ("quality_gate", "质量门控", "data_quality_summary", "审核七份分析师报告的数据完整性、时效、互相矛盾和可信度。"),
    ("bull_researcher", "多头研究员", "bull_history", "基于七份报告和质量门控，提出最强多头论证和买入触发条件。"),
    ("bear_researcher", "空头研究员", "bear_history", "基于七份报告、多头观点和质量门控，提出最强反方论证和不建仓理由。"),
    ("research_manager", "Research Manager", "investment_plan", "综合多空辩论，形成明确的投资计划和评级。"),
    ("trader", "Trader", "trader_plan", "把投资计划转成 A 股可执行交易计划，包括分批、触发、止损和失效条件。"),
    ("aggressive_risk", "激进风控", "aggressive_history", "从机会成本和进攻性仓位角度审查交易计划。"),
    ("conservative_risk", "保守风控", "conservative_history", "从回撤、流动性、T+1、价格限制和资金保护角度审查交易计划。"),
    ("neutral_risk", "中性风控", "neutral_history", "平衡进攻和防守，给出中性风险判断与仓位约束。"),
    ("portfolio_manager", "Portfolio Manager", "final_trade_decision", "综合所有报告、辩论和风控观点，输出最终 JSON 交易裁决。"),
]


def _snapshot_tradingagents_prompt(
    stock: RankedCandidate,
    snapshot_row: dict[str, Any],
    *,
    role_key: str,
    role_name: str,
    role_goal: str,
    output_key: str,
    previous_discussion: list[dict[str, str]],
) -> str:
    snapshot = snapshot_row["snapshot"]
    validation = snapshot_row["validation"]
    previous = "\n\n".join(f"## {item['role_name']}\n{item['content']}" for item in previous_discussion) or "暂无，当前为第一位角色。"
    layer_instruction = ""
    if role_key.endswith("_analyst") and role_key != "hot_money_analyst":
        layer = role_key.replace("_analyst", "")
        if layer == "fundamentals":
            layer = "fundamentals"
        layer_instruction = f"\n本角色主要输入层：{layer}。\n本层数据：{_clip_text(snapshot.get(layer, {}), 16000)}"
    elif role_key == "hot_money_analyst":
        layer_instruction = f"\n本角色主要输入层：hot_money。\n本层数据：{_clip_text(snapshot.get('hot_money', {}), 16000)}"
    elif role_key == "lockup_analyst":
        layer_instruction = f"\n本角色主要输入层：lockup。\n本层数据：{_clip_text(snapshot.get('lockup', {}), 16000)}"

    final_instruction = ""
    if role_key == "portfolio_manager":
        final_instruction = """

最终裁决必须输出 JSON 对象，不要 Markdown。格式：
{
  "signal": "STRONG_BUY|BUY|OVERWEIGHT|HOLD|UNDERWEIGHT|SELL|STRONG_SELL",
  "confidence": 0.0,
  "risk_score": 0.0,
  "trader_plan": "分批、触发、止损、失效条件；如果不建仓，说明等待条件",
  "final_decision": "最终裁决，必须明确给出信号、置信度、风险评分和理由"
}
"""
    return f"""你正在执行快照版 TradingAgentsGraph。当前节点：{role_name}。
节点输出字段：{output_key}
节点目标：{role_goal}

硬性约束：
- 只能基于已入库七层快照和本轮前序节点输出。
- 禁止联网，禁止调用外部数据，禁止编造快照没有提供的数据。
- 快照完整性校验：{json.dumps(validation, ensure_ascii=False)}
- 如果快照不完整或数据不足，必须降低置信度并明确说明。
- 报告需可审计，区分事实、推断和不确定性。
{final_instruction}

标的：{stock.name} {stock.code}
快照ID：{snapshot_row["id"]}
行情摘要：{_clip_text(stock.quote, 2000)}
{layer_instruction}

前序节点输出：
{previous}
"""


async def _call_snapshot_tradingagents_role_llm(role: dict[str, str], prompt: str, config: dict[str, str], *, timeout_seconds: int) -> str:
    return await _call_snapshot_debate_role_llm(role, prompt, config, timeout_seconds=timeout_seconds)


def _initial_snapshot_agent_state(stock: RankedCandidate, snapshot_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "messages": [{"role": "human", "content": f"{stock.name} {stock.code}"}],
        "company_of_interest": f"{stock.name} {stock.code}",
        "trade_date": str(datetime.now().date()),
        "past_context": "",
        "snapshot_id": snapshot_row["id"],
        "snapshot_validation": snapshot_row.get("validation") or {},
        "market_report": "",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "policy_report": "",
        "hot_money_report": "",
        "lockup_report": "",
        "data_quality_summary": "",
        "investment_plan": "",
        "trader_investment_plan": "",
        "final_trade_decision": "",
        "investment_debate_state": {
            "bull_history": "",
            "bear_history": "",
            "history": "",
            "current_response": "",
            "judge_decision": "",
            "count": 0,
        },
        "risk_debate_state": {
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "history": "",
            "latest_speaker": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "judge_decision": "",
            "count": 0,
        },
    }


def _snapshot_report_context(state: dict[str, Any]) -> str:
    labels = [
        ("market_report", "Market research report"),
        ("sentiment_report", "Social media sentiment report"),
        ("news_report", "Latest news report"),
        ("fundamentals_report", "Company fundamentals report"),
        ("policy_report", "Policy analysis report"),
        ("hot_money_report", "Hot money / capital flow report"),
        ("lockup_report", "Lockup expiry / insider reduction report"),
    ]
    return "\n\n".join(f"{label}:\n{state.get(key) or '[尚未生成]'}" for key, label in labels)


def _snapshot_tradingagents_state_prompt(
    stock: RankedCandidate,
    snapshot_row: dict[str, Any],
    *,
    role_key: str,
    role_name: str,
    role_goal: str,
    output_key: str,
    state: dict[str, Any],
) -> str:
    snapshot = snapshot_row["snapshot"]
    validation = snapshot_row["validation"]
    base = f"""你正在执行快照版 TradingAgentsGraph。当前节点：{role_name}。
节点输出字段：{output_key}
节点目标：{role_goal}

硬性约束：
- 只能基于已入库七层快照和当前 AgentState 字段。
- 禁止联网，禁止调用外部数据，禁止编造快照没有提供的数据。
- 快照完整性校验：{json.dumps(validation, ensure_ascii=False)}
- 如果快照不完整或数据不足，必须降低置信度并明确说明。
- 报告需可审计，区分事实、推断和不确定性。

标的：{stock.name} {stock.code}
快照ID：{snapshot_row["id"]}
行情摘要：{_clip_text(stock.quote, 2000)}
"""
    layer_by_role = {
        "market_analyst": "market",
        "social_analyst": "social",
        "news_analyst": "news",
        "fundamentals_analyst": "fundamentals",
        "policy_analyst": "policy",
        "hot_money_analyst": "hot_money",
        "lockup_analyst": "lockup",
    }
    if role_key in layer_by_role:
        layer = layer_by_role[role_key]
        return f"""{base}
本节点等价于 TradingAgents 的分析师工具循环结果：工具调用已由七层快照预取替代。
本角色主要输入层：{layer}
本层数据：
{_clip_text(snapshot.get(layer, {}), 16000)}

请输出该节点的完整分析报告。"""
    if role_key == "quality_gate":
        return f"""{base}
七份分析师报告：
{_snapshot_report_context(state)}

请按 TradingAgents Quality Gate 语义审核：数据完整性、时效性、矛盾项、可信度等级、低质量字段，并输出 data_quality_summary。"""
    if role_key == "bull_researcher":
        debate = state["investment_debate_state"]
        return f"""{base}
{_snapshot_report_context(state)}
Data quality assessment:
{state.get("data_quality_summary") or "[无]"}
Conversation history of the debate:
{debate.get("history") or "[无]"}
Last bear argument:
{debate.get("current_response") or "[无]"}

请作为 Bull Analyst 输出强多头论证，并直接回应空头担忧。"""
    if role_key == "bear_researcher":
        debate = state["investment_debate_state"]
        return f"""{base}
{_snapshot_report_context(state)}
Data quality assessment:
{state.get("data_quality_summary") or "[无]"}
Conversation history of the debate:
{debate.get("history") or "[无]"}
Last bull argument:
{debate.get("current_response") or "[无]"}

请作为 Bear Analyst 输出强反方论证，并直接反驳多头观点。"""
    if role_key == "research_manager":
        return f"""{base}
七份分析师报告：
{_snapshot_report_context(state)}
Investment debate history:
{state["investment_debate_state"].get("history") or "[无]"}

请作为 Research Manager 综合多空辩论，输出明确 investment_plan。"""
    if role_key == "trader":
        return f"""{base}
Research Manager's investment plan:
{state.get("investment_plan") or "[无]"}

政策、游资、解禁补充上下文：
Policy: {state.get("policy_report") or "[无]"}
Hot money: {state.get("hot_money_report") or "[无]"}
Lockup: {state.get("lockup_report") or "[无]"}

请作为 Trader 输出可执行交易计划。"""
    if role_key in {"aggressive_risk", "conservative_risk", "neutral_risk"}:
        risk = state["risk_debate_state"]
        return f"""{base}
Trader's decision:
{state.get("trader_investment_plan") or "[无]"}

七份分析师报告：
{_snapshot_report_context(state)}

Risk debate history:
{risk.get("history") or "[无]"}
Current aggressive response: {risk.get("current_aggressive_response") or "[无]"}
Current conservative response: {risk.get("current_conservative_response") or "[无]"}
Current neutral response: {risk.get("current_neutral_response") or "[无]"}

请以 {role_name} 身份参与风险辩论。"""
    if role_key == "portfolio_manager":
        return f"""{base}
Research Manager's investment plan:
{state.get("investment_plan") or "[无]"}
Trader's transaction proposal:
{state.get("trader_investment_plan") or "[无]"}
Risk Analysts Debate History:
{state["risk_debate_state"].get("history") or "[无]"}
Lessons from prior decisions:
{state.get("past_context") or "[无]"}

最终裁决必须输出 JSON 对象，不要 Markdown。格式：
{{
  "signal": "STRONG_BUY|BUY|OVERWEIGHT|HOLD|UNDERWEIGHT|SELL|STRONG_SELL",
  "confidence": 0.0,
  "risk_score": 0.0,
  "trader_plan": "分批、触发、止损、失效条件；如果不建仓，说明等待条件",
  "final_decision": "最终裁决，必须明确给出信号、置信度、风险评分和理由"
}}"""
    return base


def _append_investment_debate(state: dict[str, Any], *, speaker: str, content: str) -> None:
    debate = state["investment_debate_state"]
    argument = f"{speaker}: {content}"
    key = "bull_history" if speaker.startswith("Bull") else "bear_history"
    debate["history"] = (debate.get("history", "") + "\n" + argument).strip()
    debate[key] = (debate.get(key, "") + "\n" + argument).strip()
    debate["current_response"] = argument
    debate["count"] = int(debate.get("count") or 0) + 1


def _append_risk_debate(state: dict[str, Any], *, speaker: str, content: str) -> None:
    risk = state["risk_debate_state"]
    argument = f"{speaker} Analyst: {content}"
    speaker_key = speaker.lower()
    history_key = f"{speaker_key}_history"
    current_key = f"current_{speaker_key}_response"
    risk["history"] = (risk.get("history", "") + "\n" + argument).strip()
    risk[history_key] = (risk.get(history_key, "") + "\n" + argument).strip()
    risk[current_key] = argument
    risk["latest_speaker"] = speaker
    risk["count"] = int(risk.get("count") or 0) + 1


async def _run_snapshot_tradingagents_graph(
    stock: RankedCandidate,
    snapshot_row: dict[str, Any],
    config: dict[str, str],
    *,
    timeout_seconds: int,
    debate_rounds: int = 1,
    risk_rounds: int = 1,
) -> dict[str, Any]:
    state = _initial_snapshot_agent_state(stock, snapshot_row)
    role_discussion: list[dict[str, str]] = []

    async def call_role(role_key: str, role_name: str, output_key: str, role_goal: str) -> str:
        role = {"role_key": role_key, "role_name": role_name, "role_goal": role_goal, "output_key": output_key}
        prompt = _snapshot_tradingagents_state_prompt(
            stock,
            snapshot_row,
            role_key=role_key,
            role_name=role_name,
            role_goal=role_goal,
            output_key=output_key,
            state=state,
        )
        content = await _call_snapshot_tradingagents_role_llm(role, prompt, config, timeout_seconds=timeout_seconds)
        role_discussion.append({"role_key": role_key, "role_name": role_name, "output_key": output_key, "content": content})
        return content

    for role_key, role_name, output_key, role_goal in SNAPSHOT_TRADINGAGENTS_ROLES[:7]:
        state[output_key] = await call_role(role_key, role_name, output_key, role_goal)

    quality = SNAPSHOT_TRADINGAGENTS_ROLES[7]
    state["data_quality_summary"] = await call_role(*quality)

    bull = SNAPSHOT_TRADINGAGENTS_ROLES[8]
    bear = SNAPSHOT_TRADINGAGENTS_ROLES[9]
    for _round in range(max(1, debate_rounds)):
        bull_content = await call_role(*bull)
        _append_investment_debate(state, speaker="Bull Analyst", content=bull_content)
        bear_content = await call_role(*bear)
        _append_investment_debate(state, speaker="Bear Analyst", content=bear_content)

    research_manager = SNAPSHOT_TRADINGAGENTS_ROLES[10]
    state["investment_plan"] = await call_role(*research_manager)
    state["investment_debate_state"]["judge_decision"] = state["investment_plan"]
    state["investment_debate_state"]["current_response"] = state["investment_plan"]

    trader = SNAPSHOT_TRADINGAGENTS_ROLES[11]
    state["trader_investment_plan"] = await call_role(*trader)

    risk_roles = SNAPSHOT_TRADINGAGENTS_ROLES[12:15]
    risk_speakers = {
        "aggressive_risk": "Aggressive",
        "conservative_risk": "Conservative",
        "neutral_risk": "Neutral",
    }
    for _round in range(max(1, risk_rounds)):
        for role_key, role_name, output_key, role_goal in risk_roles:
            content = await call_role(role_key, role_name, output_key, role_goal)
            _append_risk_debate(state, speaker=risk_speakers[role_key], content=content)

    portfolio_manager = SNAPSHOT_TRADINGAGENTS_ROLES[15]
    state["final_trade_decision"] = await call_role(*portfolio_manager)
    state["risk_debate_state"]["judge_decision"] = state["final_trade_decision"]
    state["risk_debate_state"]["latest_speaker"] = "Judge"
    return _snapshot_tradingagents_result(role_discussion, state=state)


def _snapshot_tradingagents_result(role_discussion: list[dict[str, str]], state: dict[str, Any] | None = None) -> dict[str, Any]:
    by_key = {item["role_key"]: item["content"] for item in role_discussion}
    final_payload = _parse_llm_json(by_key.get("portfolio_manager", ""))
    state = state or {}
    investment_debate_state = state.get("investment_debate_state") or {
        "bull_history": by_key.get("bull_researcher", ""),
        "bear_history": by_key.get("bear_researcher", ""),
        "history": "\n\n".join([by_key.get("bull_researcher", ""), by_key.get("bear_researcher", "")]).strip(),
        "current_response": by_key.get("bear_researcher", ""),
        "judge_decision": by_key.get("research_manager", ""),
        "count": 2 if by_key.get("bull_researcher") or by_key.get("bear_researcher") else 0,
    }
    risk_debate_state = state.get("risk_debate_state") or {
        "aggressive_history": by_key.get("aggressive_risk", ""),
        "conservative_history": by_key.get("conservative_risk", ""),
        "neutral_history": by_key.get("neutral_risk", ""),
        "history": "\n\n".join(
            [by_key.get("aggressive_risk", ""), by_key.get("conservative_risk", ""), by_key.get("neutral_risk", "")]
        ).strip(),
        "latest_speaker": "Judge",
        "current_aggressive_response": by_key.get("aggressive_risk", ""),
        "current_conservative_response": by_key.get("conservative_risk", ""),
        "current_neutral_response": by_key.get("neutral_risk", ""),
        "judge_decision": by_key.get("portfolio_manager", ""),
        "count": sum(1 for key in ("aggressive_risk", "conservative_risk", "neutral_risk") if by_key.get(key)),
    }
    return {
        **final_payload,
        "market_report": state.get("market_report") or by_key.get("market_analyst", ""),
        "sentiment_report": state.get("sentiment_report") or by_key.get("social_analyst", ""),
        "news_report": state.get("news_report") or by_key.get("news_analyst", ""),
        "fundamentals_report": state.get("fundamentals_report") or by_key.get("fundamentals_analyst", ""),
        "policy_report": state.get("policy_report") or by_key.get("policy_analyst", ""),
        "hot_money_report": state.get("hot_money_report") or by_key.get("hot_money_analyst", ""),
        "lockup_report": state.get("lockup_report") or by_key.get("lockup_analyst", ""),
        "data_quality_summary": state.get("data_quality_summary") or by_key.get("quality_gate", ""),
        "investment_debate": json.dumps(investment_debate_state, ensure_ascii=False),
        "risk_debate": json.dumps(risk_debate_state, ensure_ascii=False),
        "trader_plan": state.get("trader_investment_plan") or final_payload.get("trader_plan") or by_key.get("trader", ""),
        "final_decision": final_payload.get("final_decision") or state.get("final_trade_decision") or by_key.get("portfolio_manager", ""),
        "role_discussion": role_discussion,
        "snapshot_tradingagents_state": {
            "investment_debate_state": investment_debate_state,
            "risk_debate_state": risk_debate_state,
            "investment_plan": state.get("investment_plan") or by_key.get("research_manager", ""),
            "trader_investment_plan": state.get("trader_investment_plan") or by_key.get("trader", ""),
            "final_trade_decision": state.get("final_trade_decision") or by_key.get("portfolio_manager", ""),
            "snapshot_id": state.get("snapshot_id"),
            "snapshot_validation": state.get("snapshot_validation"),
        },
    }


def _text_value(result: dict[str, Any], key: str) -> str:
    value = result.get(key)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "")


def _normalise_snapshot_result(result: dict[str, Any]) -> dict[str, Any]:
    final_decision = _text_value(result, "final_decision")
    trader_plan = _text_value(result, "trader_plan")
    parse_text = "\n".join([final_decision, trader_plan, json.dumps(result, ensure_ascii=False, default=_json_default)])
    signal = str(result.get("signal") or "").upper().strip()
    valid_signals = POSITIVE_SIGNALS | WATCH_SIGNALS | {"SELL", "STRONG_SELL", "UNDERWEIGHT"}
    if signal not in valid_signals:
        signal = extract_signal(parse_text)
    confidence = _float_or_none(result.get("confidence"))
    if confidence is None:
        confidence = extract_confidence(parse_text)
    if confidence is not None and confidence > 1:
        confidence = round(confidence / 100, 3)
    risk_score = _float_or_none(result.get("risk_score"))
    if risk_score is None:
        risk_score = extract_risk_score(parse_text)
    if risk_score is not None and risk_score <= 1:
        risk_score = round(risk_score * 100, 3)
    return {
        **result,
        "signal": signal or "HOLD",
        "confidence": round(confidence, 3) if confidence is not None else None,
        "risk_score": round(risk_score, 3) if risk_score is not None else None,
        "target_price": extract_target_price(parse_text),
    }


def _save_snapshot_report(
    db_path: Path,
    item: RankedCandidate,
    result: dict[str, Any],
    snapshot_row: dict[str, Any],
    *,
    run_id: str,
    duration_seconds: float,
    model: str,
    report_source: str = "snapshot_report",
    depth: str = "snapshot",
    model_mode: str = "snapshot_report",
) -> int:
    normalized = _normalise_snapshot_result(result)
    raw_state = {
        "source": report_source,
        "run_id": run_id,
        "code": item.code,
        "name": item.name,
        "signal": normalized["signal"],
        "confidence": normalized.get("confidence"),
        "risk_score": normalized.get("risk_score"),
        "target_price": normalized.get("target_price"),
        "snapshot_id": snapshot_row["id"],
        "snapshot_created_at": snapshot_row["created_at"],
        "model": model,
    }
    if result.get("role_discussion"):
        raw_state["role_discussion"] = result["role_discussion"]
    if result.get("snapshot_tradingagents_state"):
        raw_state["snapshot_tradingagents_state"] = result["snapshot_tradingagents_state"]
    if result.get("data_quality_summary"):
        raw_state["data_quality_summary"] = result["data_quality_summary"]
    market_snapshot = {
        "snapshot_id": snapshot_row["id"],
        "validation": snapshot_row["validation"],
        "summary": snapshot_row["summary"],
    }
    task_id = f"snapshot-{run_id}-{item.code}"
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO analysis_reports
                (task_id, code, signal, confidence, risk_score,
                 market_report, sentiment_report, news_report, fundamentals_report,
                 policy_report, hot_money_report, lockup_report,
                 investment_debate, risk_debate, final_decision, trader_plan,
                 raw_state, duration_seconds, market_snapshot, fact_check,
                 depth, model_mode)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                item.code,
                normalized["signal"],
                normalized.get("confidence"),
                normalized.get("risk_score"),
                _text_value(normalized, "market_report"),
                _text_value(normalized, "sentiment_report"),
                _text_value(normalized, "news_report"),
                _text_value(normalized, "fundamentals_report"),
                _text_value(normalized, "policy_report"),
                _text_value(normalized, "hot_money_report"),
                _text_value(normalized, "lockup_report"),
                _text_value(normalized, "investment_debate"),
                _text_value(normalized, "risk_debate"),
                _text_value(normalized, "final_decision"),
                _text_value(normalized, "trader_plan"),
                json.dumps(raw_state, ensure_ascii=False),
                round(duration_seconds, 3),
                json.dumps(market_snapshot, ensure_ascii=False, default=_json_default),
                json.dumps(
                    {
                        "source": "stock_data_snapshots",
                        "snapshot_id": snapshot_row["id"],
                        "validation": snapshot_row["validation"],
                    },
                    ensure_ascii=False,
                ),
                depth,
                model_mode,
            ),
        )
        conn.commit()
        report_id = conn.execute("SELECT id FROM analysis_reports WHERE task_id = ?", (task_id,)).fetchone()["id"]
    return int(report_id)


async def _invoke_tool(tool: Any, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(tool.invoke, payload)
        clipped = _clip_text(result)
        if _looks_like_semantic_tool_failure(clipped):
            return {"ok": False, "error": clipped.splitlines()[0][:220], "payload": clipped}
        return {"ok": True, "payload": clipped}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _invoke_sina_financial_report(
    code: str,
    report_type: str,
    *,
    freq: str = "quarterly",
    curr_date: str | None = None,
) -> dict[str, Any]:
    result = await asyncio.to_thread(
        _fetch_sina_financial_report,
        code,
        report_type,
        freq=freq,
        curr_date=curr_date,
    )
    clipped = _clip_text(result)
    if _looks_like_semantic_tool_failure(clipped):
        return {"ok": False, "error": clipped.splitlines()[0][:220], "payload": clipped}
    return {"ok": True, "payload": clipped}


async def fetch_seven_layer_snapshot(stock: StockCandidate, *, trade_date: str | None = None) -> dict[str, Any]:
    """Fetch the seven data layers used by the AI research pipeline."""
    from data.helpers import tencent_quote_batch
    from tradingagents.agents.utils.core_stock_tools import get_stock_data
    from tradingagents.agents.utils.fundamental_data_tools import get_fundamentals
    from tradingagents.agents.utils.news_data_tools import (
        get_global_news,
        get_insider_transactions,
        get_news,
    )
    from tradingagents.agents.utils.technical_indicators_tools import get_indicators

    today = trade_date or date.today().isoformat()
    start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    code = stock.code

    async def quote_layer() -> dict[str, Any]:
        try:
            quotes = await tencent_quote_batch([code])
            quote = quotes.get(code) or {}
            if not quote:
                return {
                    "ok": False,
                    "error": f"No quote data found for A-stock '{_pure_stock_code(code)}'; code may be invalid or unsupported by quote vendor",
                    "payload": quote,
                }
            return {"ok": True, "payload": quote}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    stock_data, indicators, quote, news, global_news, fundamentals, balance_sheet, cashflow, insider = await asyncio.gather(
        _invoke_tool(get_stock_data, {"symbol": code, "start_date": start, "end_date": today}),
        _invoke_tool(get_indicators, {"symbol": code, "indicator": "all", "curr_date": today}),
        quote_layer(),
        _invoke_tool(get_news, {"ticker": code, "start_date": start, "end_date": today}),
        _invoke_tool(get_global_news, {"curr_date": today}),
        _invoke_tool(get_fundamentals, {"ticker": code, "curr_date": today}),
        _invoke_sina_financial_report(code, "资产负债表", curr_date=today),
        _invoke_sina_financial_report(code, "现金流量表", curr_date=today),
        _invoke_tool(get_insider_transactions, {"ticker": code}),
    )

    return {
        "market": {"stock_data": stock_data, "indicators": indicators, "quote": quote},
        "social": {"news": news},
        "news": {"stock_news": news, "global_news": global_news},
        "fundamentals": {"fundamentals": fundamentals, "balance_sheet": balance_sheet, "cashflow": cashflow},
        "policy": {"global_news": global_news},
        "hot_money": {"stock_data": stock_data, "news": news, "insider_transactions": insider},
        "lockup": {"insider_transactions": insider, "fundamentals": fundamentals},
    }


def validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    missing_layers: list[str] = []
    empty_layers: list[str] = []
    layer_errors: dict[str, list[str]] = {}
    for layer in SNAPSHOT_LAYERS:
        value = snapshot.get(layer)
        if value is None:
            missing_layers.append(layer)
            continue
        if value in ({}, [], ""):
            empty_layers.append(layer)
            continue
        errors: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                error = _semantic_tool_error(key, item)
                if error:
                    errors.append(error)
        if errors:
            layer_errors[layer] = errors
    return {
        "ok": not missing_layers and not empty_layers and not layer_errors,
        "missing_layers": missing_layers,
        "empty_layers": empty_layers,
        "layer_errors": layer_errors,
        "checked_layers": list(SNAPSHOT_LAYERS),
    }


def snapshot_validation_error_summary(validation: dict[str, Any]) -> str:
    if not validation or validation.get("ok"):
        return ""
    parts: list[str] = []
    if validation.get("missing_layers"):
        parts.append("缺失层：" + "、".join(validation.get("missing_layers") or []))
    if validation.get("empty_layers"):
        parts.append("空数据层：" + "、".join(validation.get("empty_layers") or []))
    layer_errors = validation.get("layer_errors") or {}
    if isinstance(layer_errors, dict):
        for layer, errors in layer_errors.items():
            if not errors:
                continue
            first = str(errors[0])[:180]
            extra = f" 等{len(errors)}项" if len(errors) > 1 else ""
            parts.append(f"{layer}: {first}{extra}")
    return "；".join(parts) or json.dumps(validation, ensure_ascii=False)


def _snapshot_summary(snapshot: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    layer_bytes = {
        layer: len(json.dumps(snapshot.get(layer, {}), ensure_ascii=False, default=_json_default))
        for layer in SNAPSHOT_LAYERS
    }
    return {
        "ok": validation["ok"],
        "layers": list(SNAPSHOT_LAYERS),
        "layer_bytes": layer_bytes,
        "total_bytes": sum(layer_bytes.values()),
    }


def save_data_snapshot(
    db_path: Path,
    stock: StockCandidate,
    snapshot: dict[str, Any],
    *,
    run_id: str,
    source: str = "batch_research",
) -> dict[str, Any]:
    validation = validate_snapshot(snapshot)
    summary = _snapshot_summary(snapshot, validation)
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO stock_data_snapshots
                (code, name, snapshot_json, validation_json, summary_json, source, run_id)
            VALUES
                (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stock.code,
                stock.name,
                json.dumps(snapshot, ensure_ascii=False, default=_json_default),
                json.dumps(validation, ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
                source,
                run_id,
            ),
        )
        conn.commit()
    return {"id": cursor.lastrowid, "code": stock.code, "ok": validation["ok"], "validation": validation, "summary": summary}


async def prefetch_seven_layer_snapshots(
    db_path: Path,
    stocks: list[StockCandidate],
    *,
    run_id: str,
    trade_date: str | None,
    concurrency: int,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    saved: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    async def worker(stock: StockCandidate) -> None:
        async with semaphore:
            try:
                snapshot = await fetch_seven_layer_snapshot(stock, trade_date=trade_date)
                saved.append(save_data_snapshot(db_path, stock, snapshot, run_id=run_id))
            except Exception as exc:
                failed.append({"code": stock.code, "name": stock.name, "error": str(exc)})

    await asyncio.gather(*(worker(stock) for stock in stocks))
    incomplete = [item for item in saved if not item["ok"]]
    return {
        "requested": len(stocks),
        "saved": len(saved),
        "complete": len(saved) - len(incomplete),
        "incomplete": len(incomplete),
        "failed": len(failed),
        "failed_items": failed,
        "incomplete_items": [
            {"code": item["code"], "validation": item["validation"]}
            for item in incomplete[:20]
        ],
        "run_id": run_id,
        "concurrency": max(1, concurrency),
    }


async def _wait_for_tasks(task_ids: list[str], poll_interval: float, timeout_seconds: int) -> dict[str, dict]:
    started = datetime.now()
    snapshots: dict[str, dict] = {}
    while task_ids:
        remaining = []
        for task_id in task_ids:
            snapshot = await ai_task_service.get_task_snapshot(task_id)
            if snapshot:
                snapshots[task_id] = snapshot
            status = (snapshot or {}).get("status")
            queue_status = (snapshot or {}).get("queue_status")
            if status not in TERMINAL_STATUS and queue_status not in TERMINAL_STATUS:
                remaining.append(task_id)
        task_ids = remaining
        if not task_ids:
            break
        if (datetime.now() - started).total_seconds() >= timeout_seconds:
            for task_id in task_ids:
                snapshots.setdefault(task_id, {"task_id": task_id, "status": "timeout", "error": "批处理等待超时"})
            break
        await asyncio.sleep(max(0.2, poll_interval))
    return snapshots


async def submit_batches(
    candidates: list[RankedCandidate],
    *,
    batch_size: int,
    trade_date: str,
    depth: str,
    debate_rounds: int | None,
    risk_rounds: int | None,
    poll_interval: float,
    timeout_seconds: int,
) -> list[RankedCandidate]:
    completed: list[RankedCandidate] = []
    for idx in range(0, len(candidates), max(1, batch_size)):
        batch = candidates[idx : idx + max(1, batch_size)]
        task_ids: list[str] = []
        for item in batch:
            try:
                response = await ai_analysis_service.start_analysis(
                    code=item.code,
                    trade_date=trade_date,
                    depth=depth,
                    debate_rounds=debate_rounds,
                    risk_rounds=risk_rounds,
                )
                item.task_id = response.get("task_id")
                item.status = response.get("status") or "submitted"
                if item.task_id:
                    task_ids.append(item.task_id)
            except Exception as exc:
                item.status = "failed"
                item.error = str(exc)
        snapshots = await _wait_for_tasks(task_ids, poll_interval, timeout_seconds) if task_ids else {}
        for item in batch:
            if item.task_id and item.task_id in snapshots:
                snapshot = snapshots[item.task_id]
                item.status = snapshot.get("status") or item.status
                item.error = snapshot.get("error")
            completed.append(item)
    return completed


async def submit_snapshot_reports(
    db_path: Path,
    candidates: list[RankedCandidate],
    *,
    run_id: str,
    concurrency: int,
    model_tier: str,
    timeout_seconds: int,
) -> list[RankedCandidate]:
    config = _snapshot_llm_config(db_path, model_tier=model_tier)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def worker(item: RankedCandidate) -> None:
        async with semaphore:
            snapshot_row = _latest_snapshot(db_path, item.code)
            if not snapshot_row:
                item.status = "failed"
                item.error = "缺少已入库七层快照，请先执行 --data-only --apply"
                return
            validation = snapshot_row.get("validation") or {}
            if not validation.get("ok"):
                item.status = "failed"
                item.error = f"七层快照不完整：{json.dumps(validation, ensure_ascii=False)}"
                return
            started = datetime.now()
            try:
                result = await _call_snapshot_llm(
                    _snapshot_prompt(item, snapshot_row),
                    config,
                    timeout_seconds=timeout_seconds,
                )
                report_id = _save_snapshot_report(
                    db_path,
                    item,
                    result,
                    snapshot_row,
                    run_id=run_id,
                    duration_seconds=(datetime.now() - started).total_seconds(),
                    model=config.get("model", ""),
                )
                item.task_id = f"report:{report_id}"
                item.status = "completed"
                item.error = None
            except Exception as exc:
                item.status = "failed"
                item.error = str(exc)

    await asyncio.gather(*(worker(item) for item in candidates))
    return candidates


async def submit_snapshot_debate_reports(
    db_path: Path,
    candidates: list[RankedCandidate],
    *,
    run_id: str,
    concurrency: int,
    model_tier: str,
    timeout_seconds: int,
) -> list[RankedCandidate]:
    config = _snapshot_llm_config(db_path, model_tier=model_tier)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def worker(item: RankedCandidate) -> None:
        async with semaphore:
            snapshot_row = _latest_snapshot(db_path, item.code)
            if not snapshot_row:
                item.status = "failed"
                item.error = "缺少已入库七层快照，snapshot-debate 不会联网补抓数据"
                return
            validation = snapshot_row.get("validation") or {}
            if not validation.get("ok"):
                item.status = "failed"
                item.error = f"七层快照不完整：{json.dumps(validation, ensure_ascii=False)}"
                return
            started = datetime.now()
            try:
                role_discussion: list[dict[str, str]] = []
                for role_key, role_name, role_goal in SNAPSHOT_DEBATE_ROLES:
                    role = {"role_key": role_key, "role_name": role_name, "role_goal": role_goal}
                    prompt = _snapshot_debate_prompt(
                        item,
                        snapshot_row,
                        role_name=role_name,
                        role_goal=role_goal,
                        previous_discussion=role_discussion,
                    )
                    content = await _call_snapshot_debate_role_llm(role, prompt, config, timeout_seconds=timeout_seconds)
                    role_discussion.append({"role_key": role_key, "role_name": role_name, "content": content})
                report_id = _save_snapshot_report(
                    db_path,
                    item,
                    _snapshot_debate_result(role_discussion),
                    snapshot_row,
                    run_id=run_id,
                    duration_seconds=(datetime.now() - started).total_seconds(),
                    model=config.get("model", ""),
                    report_source="snapshot_debate",
                    depth="snapshot_debate",
                    model_mode="snapshot_debate",
                )
                item.task_id = f"report:{report_id}"
                item.status = "completed"
                item.error = None
            except Exception as exc:
                item.status = "failed"
                item.error = str(exc)

    await asyncio.gather(*(worker(item) for item in candidates))
    return candidates


async def submit_snapshot_tradingagents_reports(
    db_path: Path,
    candidates: list[RankedCandidate],
    *,
    run_id: str,
    concurrency: int,
    model_tier: str,
    timeout_seconds: int,
) -> list[RankedCandidate]:
    config = _snapshot_llm_config(db_path, model_tier=model_tier)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def worker(item: RankedCandidate) -> None:
        async with semaphore:
            snapshot_row = _latest_snapshot(db_path, item.code)
            if not snapshot_row:
                item.status = "failed"
                item.error = "缺少已入库七层快照，snapshot-tradingagents 不会联网补抓数据"
                return
            validation = snapshot_row.get("validation") or {}
            if not validation.get("ok"):
                item.status = "failed"
                item.error = f"七层快照不完整：{json.dumps(validation, ensure_ascii=False)}"
                return
            started = datetime.now()
            try:
                graph_result = await _run_snapshot_tradingagents_graph(
                    item,
                    snapshot_row,
                    config,
                    timeout_seconds=timeout_seconds,
                )
                report_id = _save_snapshot_report(
                    db_path,
                    item,
                    graph_result,
                    snapshot_row,
                    run_id=run_id,
                    duration_seconds=(datetime.now() - started).total_seconds(),
                    model=config.get("model", ""),
                    report_source="snapshot_tradingagents",
                    depth="snapshot_tradingagents",
                    model_mode="snapshot_tradingagents",
                )
                item.task_id = f"report:{report_id}"
                item.status = "completed"
                item.error = None
            except Exception as exc:
                item.status = "failed"
                item.error = str(exc)

    await asyncio.gather(*(worker(item) for item in candidates))
    return candidates


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _cash_balance(conn: sqlite3.Connection) -> float:
    return _cash_context(conn)["total_cash"]


def _cash_context(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT key, value
        FROM settings
        WHERE key = 'cash_balance' OR key = 'cash_balance_default' OR key LIKE 'cash_balance_%'
        ORDER BY key
        """
    ).fetchall()
    balances: dict[str, float] = {}
    for row in rows:
        key = row["key"]
        if key in {"cash_balance", "cash_balance_default"}:
            account_id = "default"
        else:
            account_id = key.replace("cash_balance_", "") or "default"
        balances[account_id] = _float_or_none(row["value"]) or 0.0
    total_cash = round(sum(balances.values()), 3)
    return {"balances": balances, "total_cash": total_cash}


def _portfolio_context(conn: sqlite3.Connection, *, cash: float) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT code, name, total_shares, available_shares, avg_cost, current_price,
               market_value, unrealized_pnl, unrealized_pnl_pct, account_id, updated_at
        FROM portfolio
        WHERE total_shares > 0
        ORDER BY account_id, market_value DESC, code
        """
    ).fetchall()
    positions = []
    market_value = 0.0
    for row in rows:
        shares = _float_or_none(row["total_shares"]) or 0.0
        current_price = _float_or_none(row["current_price"]) or 0.0
        row_market_value = _float_or_none(row["market_value"]) or 0.0
        if not row_market_value and current_price and shares:
            row_market_value = round(current_price * shares, 3)
        market_value += row_market_value
        positions.append(
            {
                "code": row["code"],
                "name": row["name"] or row["code"],
                "shares": round(shares, 3),
                "available_shares": _float_or_none(row["available_shares"]) or 0.0,
                "avg_cost": _float_or_none(row["avg_cost"]) or 0.0,
                "current_price": current_price,
                "market_value": round(row_market_value, 3),
                "unrealized_pnl": _float_or_none(row["unrealized_pnl"]) or 0.0,
                "unrealized_pnl_pct": _float_or_none(row["unrealized_pnl_pct"]) or 0.0,
                "account_id": row["account_id"] or "default",
                "updated_at": row["updated_at"],
            }
        )
    market_value = round(market_value, 3)
    total_assets = round(cash + market_value, 3)
    by_code = {item["code"]: item for item in positions}
    for item in positions:
        item["position_pct_of_assets"] = round(item["market_value"] / total_assets * 100, 3) if total_assets else 0.0
    return {
        "positions": positions,
        "positions_by_code": by_code,
        "position_count": len(positions),
        "market_value": market_value,
        "cash": round(cash, 3),
        "total_assets": total_assets,
        "invested_pct": round(market_value / total_assets * 100, 3) if total_assets else 0.0,
        "cash_pct": round(cash / total_assets * 100, 3) if total_assets else 0.0,
    }


def _attach_current_positions(items: list[dict[str, Any]], portfolio_context: dict[str, Any]) -> None:
    positions_by_code = portfolio_context.get("positions_by_code") or {}
    for item in items:
        position = positions_by_code.get(item.get("code")) or {}
        if position:
            item["current_position"] = {
                "shares": position.get("shares") or 0.0,
                "available_shares": position.get("available_shares") or 0.0,
                "avg_cost": position.get("avg_cost") or 0.0,
                "current_price": position.get("current_price") or 0.0,
                "market_value": position.get("market_value") or 0.0,
                "position_pct_of_assets": position.get("position_pct_of_assets") or 0.0,
                "account_id": position.get("account_id") or "default",
            }
        else:
            item["current_position"] = {
                "shares": 0.0,
                "available_shares": 0.0,
                "avg_cost": 0.0,
                "current_price": 0.0,
                "market_value": 0.0,
                "position_pct_of_assets": 0.0,
                "account_id": "default",
            }


def _latest_reports(conn: sqlite3.Connection, codes: list[str]) -> dict[str, sqlite3.Row]:
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"""
        SELECT ar.*
        FROM analysis_reports ar
        JOIN (
            SELECT code, MAX(created_at) AS created_at
            FROM analysis_reports
            WHERE code IN ({placeholders})
            GROUP BY code
        ) latest ON latest.code = ar.code AND latest.created_at = ar.created_at
        """,
        codes,
    ).fetchall()
    return {row["code"]: row for row in rows}


def _signal_score(signal: str | None) -> float:
    signal = (signal or "").upper()
    if signal == "STRONG_BUY":
        return 100.0
    if signal in POSITIVE_SIGNALS:
        return 82.0
    if signal in WATCH_SIGNALS:
        return 48.0
    if signal in {"UNDERWEIGHT", "REDUCE"}:
        return 24.0
    if signal in {"SELL", "STRONG_SELL"}:
        return 5.0
    return 35.0


def _extract_target_price(text: str) -> float | None:
    patterns = [
        r"(?:目标价|目标价格|目标位|看到|上看)[^\d]{0,8}(\d+(?:\.\d+)?)",
        r"target[_\s-]?price[^\d]{0,8}(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _float_or_none(match.group(1))
    return None


def _report_to_plan_item(stock: StockCandidate, row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return {
            "code": stock.code,
            "name": stock.name,
            "group_name": stock.group_name,
            "action": "missing_report",
            "score": 0.0,
            "reason": "缺少分析报告，暂不进入建仓建议",
        }
    signal = row["signal"] or "UNKNOWN"
    confidence = _float_or_none(row["confidence"]) or 0.0
    risk_score = _float_or_none(row["risk_score"])
    risk_penalty = (risk_score or 50.0) * 0.35
    group_bonus = 4.0 if stock.group_name != "观察池" else 0.0
    score = round(_signal_score(signal) + confidence * 18.0 + group_bonus - risk_penalty, 3)
    action = "buy" if signal.upper() in POSITIVE_SIGNALS and score >= 58 else "watch"
    text = "\n".join(str(row[col] or "") for col in ["final_decision", "trader_plan", "investment_debate"])
    return {
        "report_id": row["id"],
        "task_id": row["task_id"],
        "code": stock.code,
        "name": stock.name,
        "group_name": stock.group_name,
        "signal": signal,
        "confidence": confidence,
        "risk_score": risk_score,
        "score": score,
        "action": action,
        "target_price": _extract_target_price(text),
        "reason": (row["final_decision"] or row["trader_plan"] or "")[:240],
        "created_at": row["created_at"],
    }


def build_position_plan(db_path: Path, stocks: list[StockCandidate], *, top_n: int = 10) -> dict[str, Any]:
    with _connect(db_path) as conn:
        cash = _cash_balance(conn)
        cash_context = _cash_context(conn)
        portfolio_context = _portfolio_context(conn, cash=cash)
        reports = _latest_reports(conn, [stock.code for stock in stocks])

    items = [_report_to_plan_item(stock, reports.get(stock.code)) for stock in stocks]
    _attach_current_positions(items, portfolio_context)
    available = [item for item in items if item.get("report_id")]
    missing = [item for item in items if item["action"] == "missing_report"]
    buyable = sorted(
        [item for item in available if item["action"] == "buy"],
        key=lambda item: item["score"],
        reverse=True,
    )[:top_n]
    watchers = sorted(
        [item for item in available if item["action"] != "buy"],
        key=lambda item: item["score"],
        reverse=True,
    )

    score_sum = sum(max(item["score"], 1.0) for item in buyable) or 1.0
    max_single_amount = cash * 0.15 if cash > 0 else 0.0
    for item in buyable:
        raw_amount = cash * max(item["score"], 1.0) / score_sum
        item["suggested_amount"] = round(min(raw_amount, max_single_amount), 3)
        item["position_pct"] = round(item["suggested_amount"] / cash * 100, 3) if cash else 0.0
    for item in watchers:
        item["suggested_amount"] = 0.0
        item["position_pct"] = 0.0
    for item in missing:
        item["suggested_amount"] = 0.0
        item["position_pct"] = 0.0

    recommendations = buyable + watchers + missing
    return {
        "cash": cash,
        "cash_context": cash_context,
        "portfolio_context": {key: value for key, value in portfolio_context.items() if key != "positions_by_code"},
        "candidate_count": len(stocks),
        "available_reports": len(available),
        "missing_reports": len(missing),
        "top_n": top_n,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "recommendations": recommendations,
        "notes": [
            "建仓建议只基于已入库 AI 分析报告生成，不自动写入交易流水或条件单。",
            "单票建议金额默认不超过可用现金 15%，用于空仓后的分批建仓参考。",
        ],
    }


POSITION_PLAN_ROLES = [
    ("portfolio_manager", "组合经理", "从收益目标、候选优先级和资金利用效率给出组合构建意见。"),
    ("risk_manager", "风控经理", "从回撤、集中度、行业/风格拥挤和现金缓冲角度审查方案。"),
    ("trader", "交易员", "把研究结论转成可执行的分批、触发、止损和失效条件。"),
    ("skeptic", "反方审查", "主动寻找报告中的弱证据、冲突信号和可能被高估的确定性。"),
    ("chair", "最终裁决", "综合前面角色观点，输出最终 JSON 建仓建议。"),
]


FULL_REPORT_COLUMNS = [
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "policy_report",
    "hot_money_report",
    "lockup_report",
    "investment_debate",
    "risk_debate",
    "final_decision",
    "trader_plan",
    "raw_state",
    "market_snapshot",
    "fact_check",
    "bystander_verify",
]


def _load_position_reports(db_path: Path, stocks: list[StockCandidate], report_ids: list[int] | None) -> list[sqlite3.Row]:
    with _connect(db_path) as conn:
        if report_ids:
            clean_ids = [int(report_id) for report_id in report_ids if int(report_id) > 0]
            if not clean_ids:
                return []
            placeholders = ",".join("?" for _ in clean_ids)
            order_expr = "CASE " + " ".join(f"WHEN ar.id = ? THEN {idx}" for idx, _ in enumerate(clean_ids)) + " END"
            return conn.execute(
                f"""
                SELECT ar.*, COALESCE(w.name, ar.code) AS watch_name, COALESCE(w.group_name, '默认') AS group_name
                FROM analysis_reports ar
                LEFT JOIN watchlist w ON w.code = ar.code
                WHERE ar.id IN ({placeholders})
                ORDER BY {order_expr}
                """,
                clean_ids + clean_ids,
            ).fetchall()
        codes = [stock.code for stock in stocks]
        if not codes:
            return []
        placeholders = ",".join("?" for _ in codes)
        rows = conn.execute(
            f"""
            SELECT ar.*, COALESCE(w.name, ar.code) AS watch_name, COALESCE(w.group_name, '默认') AS group_name
            FROM analysis_reports ar
            LEFT JOIN watchlist w ON w.code = ar.code
            JOIN (
                SELECT code, MAX(created_at) AS created_at
                FROM analysis_reports
                WHERE code IN ({placeholders})
                GROUP BY code
            ) latest ON latest.code = ar.code AND latest.created_at = ar.created_at
            """,
            codes,
        ).fetchall()
        by_code = {row["code"]: row for row in rows}
        return [by_code[stock.code] for stock in stocks if by_code.get(stock.code)]


def _report_context_block(row: sqlite3.Row) -> str:
    lines = [
        f"## {row['watch_name'] or row['code']} {row['code']} 报告 #{row['id']}",
        f"- 信号：{row['signal'] or 'UNKNOWN'}",
        f"- 置信度：{row['confidence']}",
        f"- 风险评分：{row['risk_score']}",
        f"- 深度/模式：{row['depth']} / {row['model_mode']}",
        f"- 生成时间：{row['created_at']}",
    ]
    for column in FULL_REPORT_COLUMNS:
        value = row[column] if column in row.keys() else ""
        if value:
            lines.extend(["", f"### {column}", str(value)])
    return "\n".join(lines)


def _report_summary_block(row: sqlite3.Row) -> str:
    core = {
        "report_id": row["id"],
        "code": row["code"],
        "name": row["watch_name"] or row["code"],
        "group_name": row["group_name"],
        "signal": row["signal"],
        "confidence": row["confidence"],
        "risk_score": row["risk_score"],
        "depth": row["depth"],
        "model_mode": row["model_mode"],
        "final_decision": _clip_text(row["final_decision"] or "", 1200),
        "trader_plan": _clip_text(row["trader_plan"] or "", 900),
        "investment_debate": _clip_text(row["investment_debate"] or "", 900),
        "risk_debate": _clip_text(row["risk_debate"] or "", 900),
        "fact_check": _clip_text(row["fact_check"] or "", 500),
    }
    return json.dumps(core, ensure_ascii=False, default=_json_default)


def _choose_context_strategy(report_count: int, raw_text_chars: int, requested: str | None = "auto") -> str:
    requested = (requested or "auto").strip()
    if requested in {"full_text", "summary_plus_evidence", "candidate_screening"}:
        return requested
    if report_count <= 8 and raw_text_chars <= 60000:
        return "full_text"
    if report_count <= 30 and raw_text_chars <= 180000:
        return "summary_plus_evidence"
    return "candidate_screening"


def _position_report_context(rows: list[sqlite3.Row], deterministic_plan: dict[str, Any], strategy: str, *, top_n: int) -> str:
    if strategy == "full_text":
        return "\n\n".join(_report_context_block(row) for row in rows)
    summaries = [_report_summary_block(row) for row in rows]
    if strategy == "summary_plus_evidence":
        evidence = []
        for row in rows[: max(1, min(len(rows), top_n * 2))]:
            evidence.append(
                "\n".join(
                    [
                        f"## 关键证据 {row['watch_name'] or row['code']} {row['code']} #{row['id']}",
                        f"### final_decision\n{_clip_text(row['final_decision'] or '', 1600)}",
                        f"### trader_plan\n{_clip_text(row['trader_plan'] or '', 1200)}",
                    ]
                )
            )
        return "## 结构化候选摘要\n" + "\n".join(summaries) + "\n\n## 关键原文证据\n" + "\n\n".join(evidence)
    ranked = deterministic_plan.get("recommendations") or []
    filtered = [
        item
        for item in ranked
        if (item.get("action") or "").lower() in {"buy", "watch", "overweight", "add"}
    ][: max(top_n * 3, top_n)]
    return _clip_text(
        json.dumps(
            {
                "context_strategy": "candidate_screening",
                "candidate_summaries": summaries,
                "screened_candidates": filtered,
                "screening_rules": [
                    "剔除明显卖出/减持和缺少报告的标的。",
                    "高置信度、低风险、明确交易计划的标的优先。",
                    "最终建仓仍需考虑组合集中度、现金缓冲和执行触发条件。",
                ],
            },
            ensure_ascii=False,
            default=_json_default,
        ),
        70000,
    )


def _safe_num(value: Any, default: float | None = None) -> float | None:
    try:
        return round(float(str(value).replace(",", "")), 3)
    except (TypeError, ValueError):
        return default


def _kline_summary(klines: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [_safe_num(item.get("close")) for item in klines if _safe_num(item.get("close")) is not None]
    volumes = [_safe_num(item.get("volume"), 0.0) or 0.0 for item in klines]
    if not closes:
        return {"count": len(klines), "status": "empty"}
    last_close = closes[-1]
    recent20 = closes[-20:]
    recent5 = closes[-5:]
    avg_volume5 = sum(volumes[-5:]) / min(5, len(volumes)) if volumes else 0.0
    prev_volume5 = sum(volumes[-10:-5]) / 5 if len(volumes) >= 10 else 0.0
    return {
        "count": len(klines),
        "last_date": klines[-1].get("date") if isinstance(klines[-1], dict) else "",
        "last_close": last_close,
        "return_5d_pct": round((last_close / recent5[0] - 1) * 100, 3) if recent5 and recent5[0] else 0.0,
        "return_20d_pct": round((last_close / recent20[0] - 1) * 100, 3) if recent20 and recent20[0] else 0.0,
        "high_20": round(max(recent20), 3),
        "low_20": round(min(recent20), 3),
        "ma20": round(sum(recent20) / len(recent20), 3),
        "above_ma20": bool(last_close >= (sum(recent20) / len(recent20))),
        "volume_ratio_5d": round(avg_volume5 / prev_volume5, 3) if prev_volume5 else None,
    }


async def collect_position_plan_market_context(
    stocks: list[StockCandidate],
    *,
    kline_periods: list[tuple[str, int]] | None = None,
    kline_concurrency: int = 8,
) -> dict[str, Any]:
    """Collect execution-time quotes and K-line summaries for position planning."""
    kline_periods = kline_periods or [("day", 60), ("60", 32)]
    captured_at = datetime.now().isoformat(timespec="seconds")
    codes = list(dict.fromkeys(stock.code for stock in stocks if stock.code))
    context: dict[str, Any] = {
        "captured_at": captured_at,
        "source": "tencent_quote_and_local_kline",
        "status": "empty" if not codes else "ok",
        "items": {},
        "summary": [],
    }
    if not codes:
        return context
    try:
        quotes = await get_batch_quotes(codes)
    except Exception as exc:  # noqa: BLE001 - keep planning usable when quotes are temporarily unavailable
        quotes = {}
        context["status"] = "partial"
        context["quote_error"] = str(exc)

    semaphore = asyncio.Semaphore(max(1, int(kline_concurrency or 1)))

    async def fetch_period(code: str, period: str, count: int) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            try:
                rows = await asyncio.to_thread(get_kline, code, period, count)
                return period, {"status": "ok", "count": len(rows), "summary": _kline_summary(rows)}
            except Exception as exc:  # noqa: BLE001
                return period, {"status": "failed", "error": str(exc)}

    kline_tasks = {
        (code, period): asyncio.create_task(fetch_period(code, period, count))
        for code in codes
        for period, count in kline_periods
    }
    kline_map: dict[str, dict[str, Any]] = {code: {} for code in codes}
    for (code, _period), task in kline_tasks.items():
        period, data = await task
        kline_map.setdefault(code, {})[period] = data

    for code in codes:
        quote = quotes.get(code) or {}
        klines = kline_map.get(code) or {}
        item_status = "ok" if quote or any(data.get("status") == "ok" for data in klines.values()) else "failed"
        if item_status != "ok":
            context["status"] = "partial"
        item = {
            "status": item_status,
            "quote": quote,
            "kline_summary": klines,
        }
        if not quote:
            item["error"] = "实时行情为空"
        context["items"][code] = item
        context["summary"].append(
            {
                "code": code,
                "name": quote.get("name") or code,
                "status": item_status,
                "price": _safe_num(quote.get("price")),
                "change_pct": _safe_num(quote.get("change_pct")),
                "turnover_rate": _safe_num(quote.get("turnover_rate")),
                "amount": _safe_num(quote.get("amount")),
                "day": klines.get("day", {}).get("summary", {}),
                "intraday": klines.get("60", {}).get("summary", {}),
                "error": item.get("error", ""),
            }
        )
    return context


def _position_market_context_block(context: dict[str, Any] | None) -> str:
    if not context:
        return "未采集。"
    lines = [
        "## 决策实时行情快照",
        f"- 采集时间：{context.get('captured_at') or '未知'}",
        f"- 状态：{context.get('status') or 'unknown'}",
        "- 用途：只用于校准建仓执行时点，不覆盖原始单股报告结论。",
        "",
        "| 股票 | 状态 | 现价 | 涨跌幅% | 5日收益% | 20日收益% | MA20 | 备注 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in context.get("summary") or []:
        day = item.get("day") if isinstance(item.get("day"), dict) else {}
        lines.append(
            "| {code} | {status} | {price:.3f} | {change:.3f} | {ret5:.3f} | {ret20:.3f} | {ma20:.3f} | {note} |".format(
                code=item.get("code") or "",
                status=item.get("status") or "",
                price=_safe_num(item.get("price"), 0.0) or 0.0,
                change=_safe_num(item.get("change_pct"), 0.0) or 0.0,
                ret5=_safe_num(day.get("return_5d_pct"), 0.0) or 0.0,
                ret20=_safe_num(day.get("return_20d_pct"), 0.0) or 0.0,
                ma20=_safe_num(day.get("ma20"), 0.0) or 0.0,
                note=item.get("error") or ("缺失实时行情" if item.get("status") != "ok" else ""),
            )
        )
    if any((item.get("status") != "ok") for item in context.get("summary") or []):
        lines.append("")
        lines.append("存在缺失实时行情的股票，最终裁决必须标注不确定性，不能把缺失行情的标的作为可直接执行买入。")
    return "\n".join(lines)


def _position_portfolio_context_block(context: dict[str, Any] | None) -> str:
    if not context:
        return "## 当前组合与资金快照\n- 未采集。"
    lines = [
        "## 当前组合与资金快照",
        f"- 可用现金：{_safe_num(context.get('cash'), 0.0) or 0.0:.3f}",
        f"- 持仓市值：{_safe_num(context.get('market_value'), 0.0) or 0.0:.3f}",
        f"- 总资产：{_safe_num(context.get('total_assets'), 0.0) or 0.0:.3f}",
        f"- 当前仓位：{_safe_num(context.get('invested_pct'), 0.0) or 0.0:.3f}%",
        f"- 现金比例：{_safe_num(context.get('cash_pct'), 0.0) or 0.0:.3f}%",
        "- 约束：建仓建议必须是调仓建议，不允许默认全仓重建；suggested_amount 只代表新增买入金额，不包含已有持仓市值。",
    ]
    positions = context.get("positions") or []
    if not positions:
        lines.append("- 当前空仓：可以基于可用现金分批建仓，但仍需保留现金缓冲。")
        return "\n".join(lines)
    lines.extend(
        [
            "",
            "| 股票 | 账户 | 持股 | 可用 | 成本 | 现价 | 市值 | 仓位% | 浮盈亏% |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in positions:
        lines.append(
            "| {name} {code} | {account} | {shares:.3f} | {available:.3f} | {avg_cost:.3f} | {price:.3f} | {value:.3f} | {pct:.3f} | {pnl_pct:.3f} |".format(
                name=item.get("name") or item.get("code") or "",
                code=item.get("code") or "",
                account=item.get("account_id") or "default",
                shares=_safe_num(item.get("shares"), 0.0) or 0.0,
                available=_safe_num(item.get("available_shares"), 0.0) or 0.0,
                avg_cost=_safe_num(item.get("avg_cost"), 0.0) or 0.0,
                price=_safe_num(item.get("current_price"), 0.0) or 0.0,
                value=_safe_num(item.get("market_value"), 0.0) or 0.0,
                pct=_safe_num(item.get("position_pct_of_assets"), 0.0) or 0.0,
                pnl_pct=_safe_num(item.get("unrealized_pnl_pct"), 0.0) or 0.0,
            )
        )
    return "\n".join(lines)


def _position_discussion_prompt(
    *,
    role_name: str,
    role_goal: str,
    cash: float,
    top_n: int,
    report_context: str,
    market_context: dict[str, Any] | None,
    portfolio_context: dict[str, Any] | None,
    previous_discussion: list[dict[str, str]],
) -> str:
    previous = "\n\n".join(f"## {item['role_name']}\n{item['content']}" for item in previous_discussion) or "暂无，当前为第一位角色。"
    final_instruction = ""
    if role_name == "最终裁决":
        final_instruction = """

最终裁决必须输出 JSON 对象，不要 Markdown。格式：
{
  "summary": "组合层面的最终结论",
  "actions": [
    {"code": "000001", "action": "buy|watch|avoid|sell", "suggested_amount": 0.0, "position_pct": 0.0, "reason": "理由", "entry_plan": "分批/触发/失效条件"}
  ],
  "risk_controls": ["组合级风险约束"]
}
"""
    return f"""你正在参加 A 股组合建仓委员会。当前角色：{role_name}。
角色目标：{role_goal}

约束：
- 只能基于下方已入库的完整 AI 报告内容、决策实时行情快照和已有角色观点判断。
- 不允许自行联网，不允许编造报告没有提供的数据。
- 当前可用现金：{cash:.3f}
- 最多给出 {top_n} 只买入候选。
- 建仓建议不写库、不下单，只生成研究建议。
- 金额和百分比保留三位小数。
- 建仓建议必须结合当前仓位和可用资金，输出调仓建议；不能把当前组合当作空仓，也不能默认全仓买入。
- 如果实时行情/K线与旧报告判断冲突，优先把它作为执行时点校准：可降低仓位、延后建仓或改为观察，但不能改写旧报告事实。
{final_instruction}

已有角色讨论：
{previous}

{_position_market_context_block(market_context)}

{_position_portfolio_context_block(portfolio_context)}

完整报告上下文：
{_clip_text(report_context, 60000)}
"""


async def _call_position_plan_role_llm(role: dict[str, str], prompt: str, config: dict[str, str], *, timeout_seconds: int) -> str:
    if not config.get("base_url"):
        raise RuntimeError("AI 引擎 Base URL 未配置，无法生成多角色建仓建议")
    if not config.get("api_key"):
        raise RuntimeError("AI 引擎 API Key 未配置，无法生成多角色建仓建议")
    if not config.get("model"):
        raise RuntimeError("AI 引擎模型未配置，无法生成多角色建仓建议")
    async with httpx.AsyncClient(timeout=max(30, timeout_seconds)) as client:
        resp = await client.post(
            _chat_completions_url(config["base_url"]),
            headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
            json={
                "model": config["model"],
                "messages": [
                    {"role": "system", "content": f"你是{role['role_name']}，参与组合级多角色建仓讨论。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.25 if role["role_key"] != "chair" else 0.15,
                "max_tokens": 5000,
            },
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"多角色建仓建议模型请求失败 HTTP {resp.status_code}: {resp.text[:240]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _role_model_config(
    role_key: str,
    *,
    model_strategy: str,
    default_config: dict[str, str],
    verification_config: dict[str, str],
    role_models: dict[str, Any] | None,
) -> dict[str, str]:
    role_models = role_models or {}
    custom = role_models.get(role_key)
    if isinstance(custom, dict) and custom.get("base_url") and custom.get("api_key") and custom.get("model"):
        return {
            "base_url": custom.get("base_url") or "",
            "api_key": custom.get("api_key") or "",
            "model": custom.get("model") or "",
        }
    if model_strategy == "dual" and role_key == "chair" and all(verification_config.get(key) for key in ("base_url", "api_key", "model")):
        return verification_config
    return default_config


def _resolve_role_model_configs(db_path: Path, role_models: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    """Resolve public role model selections to private provider configs."""
    role_models = role_models or {}
    if not role_models:
        return {}
    with _connect(db_path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'model_providers'").fetchone()
    providers = _loads(row["value"] if row else "[]", [])
    providers_by_id = {str(provider.get("id")): provider for provider in providers if isinstance(provider, dict)}
    resolved: dict[str, dict[str, str]] = {}
    for role_key, spec in role_models.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("base_url") and spec.get("api_key") and spec.get("model"):
            resolved[role_key] = {
                "base_url": spec.get("base_url") or "",
                "api_key": spec.get("api_key") or "",
                "model": spec.get("model") or "",
                "_profile": spec.get("name") or "custom",
            }
            continue
        provider_id = str(spec.get("provider_id") or "")
        provider = providers_by_id.get(provider_id)
        if not provider:
            continue
        model = spec.get("model") or provider.get("default_model") or provider.get("deep_model") or provider.get("quick_model") or ""
        if not provider.get("base_url") or not provider.get("api_key") or not model:
            continue
        resolved[role_key] = {
            "base_url": provider.get("base_url") or "",
            "api_key": provider.get("api_key") or "",
            "model": model,
            "_profile": provider.get("name") or provider_id,
        }
    return resolved


def _parse_position_plan_final(text: str) -> dict[str, Any]:
    parsed = _parse_llm_json(text)
    return parsed if isinstance(parsed, dict) else {"summary": str(text or ""), "actions": [], "risk_controls": []}


def _multi_role_plan_items(
    rows: list[sqlite3.Row],
    final_payload: dict[str, Any],
    deterministic_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    by_code = {item["code"]: item for item in deterministic_plan.get("recommendations", [])}
    actions = final_payload.get("actions") if isinstance(final_payload.get("actions"), list) else []
    output: list[dict[str, Any]] = []
    used: set[str] = set()
    for action in actions:
        if not isinstance(action, dict):
            continue
        code = str(action.get("code") or "").strip()
        if not code:
            continue
        base = dict(by_code.get(code) or {"code": code, "name": code, "score": 0.0})
        used.add(code)
        try:
            suggested_amount = round(float(action.get("suggested_amount") or 0), 3)
        except (TypeError, ValueError):
            suggested_amount = 0.0
        try:
            position_pct = round(float(action.get("position_pct") or 0), 3)
        except (TypeError, ValueError):
            position_pct = 0.0
        base.update(
            {
                "action": action.get("action") or base.get("action") or "watch",
                "suggested_amount": suggested_amount,
                "position_pct": position_pct,
                "reason": action.get("reason") or base.get("reason") or "",
                "entry_plan": action.get("entry_plan") or action.get("plan") or "",
            }
        )
        output.append(base)
    for row in rows:
        if row["code"] not in used and row["code"] in by_code:
            output.append(by_code[row["code"]])
    return output


def _build_position_plan_from_report_rows(db_path: Path, rows: list[sqlite3.Row], *, top_n: int) -> dict[str, Any]:
    with _connect(db_path) as conn:
        cash = _cash_balance(conn)
        cash_context = _cash_context(conn)
        portfolio_context = _portfolio_context(conn, cash=cash)
    items = [
        _report_to_plan_item(
            StockCandidate(row["code"], row["watch_name"] or row["code"], row["group_name"] or "默认", 0),
            row,
        )
        for row in rows
    ]
    _attach_current_positions(items, portfolio_context)
    buyable = sorted(
        [item for item in items if item.get("action") == "buy"],
        key=lambda item: item["score"],
        reverse=True,
    )[:top_n]
    watchers = sorted(
        [item for item in items if item.get("action") != "buy"],
        key=lambda item: item["score"],
        reverse=True,
    )
    score_sum = sum(max(item["score"], 1.0) for item in buyable) or 1.0
    max_single_amount = cash * 0.15 if cash > 0 else 0.0
    for item in buyable:
        raw_amount = cash * max(item["score"], 1.0) / score_sum
        item["suggested_amount"] = round(min(raw_amount, max_single_amount), 3)
        item["position_pct"] = round(item["suggested_amount"] / cash * 100, 3) if cash else 0.0
    for item in watchers:
        item["suggested_amount"] = 0.0
        item["position_pct"] = 0.0
    return {
        "cash": cash,
        "cash_context": cash_context,
        "portfolio_context": {key: value for key, value in portfolio_context.items() if key != "positions_by_code"},
        "candidate_count": len(rows),
        "available_reports": len(rows),
        "missing_reports": 0,
        "top_n": top_n,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "recommendations": buyable + watchers,
        "notes": [
            "建仓建议只基于已勾选并入库的 AI 分析报告生成，不自动写入交易流水或条件单。",
            "单票建议金额默认不超过可用现金 15%，用于空仓后的分批建仓参考。",
        ],
    }


async def build_multi_role_position_plan(
    db_path: Path,
    stocks: list[StockCandidate],
    *,
    report_ids: list[int] | None = None,
    top_n: int = 10,
    config: dict[str, str] | None = None,
    timeout_seconds: int = 3600,
    context_strategy: str = "auto",
    model_strategy: str = "single",
    role_models: dict[str, Any] | None = None,
    decision_market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _connect(db_path) as conn:
        cash = _cash_balance(conn)
    rows = _load_position_reports(db_path, stocks, report_ids)
    if not rows:
        raise RuntimeError("缺少可用于多角色建仓建议的完整报告")
    deterministic_plan = _build_position_plan_from_report_rows(db_path, rows, top_n=top_n)
    raw_text_chars = sum(len(_report_context_block(row)) for row in rows)
    resolved_context_strategy = _choose_context_strategy(len(rows), raw_text_chars, context_strategy)
    report_context = _position_report_context(rows, deterministic_plan, resolved_context_strategy, top_n=top_n)
    role_discussion: list[dict[str, str]] = []
    llm_config = config or _snapshot_llm_config(db_path, model_tier="deep")
    verification_config = _verification_llm_config(db_path)
    resolved_role_models = _resolve_role_model_configs(db_path, role_models)
    role_model_trace: dict[str, dict[str, str]] = {}
    for role_key, role_name, role_goal in POSITION_PLAN_ROLES:
        role = {"role_key": role_key, "role_name": role_name, "role_goal": role_goal}
        role_config = _role_model_config(
            role_key,
            model_strategy=model_strategy,
            default_config=llm_config,
            verification_config=verification_config,
            role_models=resolved_role_models,
        )
        role_model_trace[role_key] = {
            "model": role_config.get("model", ""),
            "base_url": role_config.get("base_url", ""),
            "profile": role_config.get("_profile") or ("verification" if role_config == verification_config else "ai"),
        }
        prompt = _position_discussion_prompt(
            role_name=role_name,
            role_goal=role_goal,
            cash=cash,
            top_n=top_n,
            report_context=report_context,
            market_context=decision_market_context,
            portfolio_context=deterministic_plan.get("portfolio_context") or {},
            previous_discussion=role_discussion,
        )
        content = await _call_position_plan_role_llm(role, prompt, role_config, timeout_seconds=timeout_seconds)
        role_discussion.append({"role_key": role_key, "role_name": role_name, "content": content})
    final_payload = _parse_position_plan_final(role_discussion[-1]["content"])
    recommendations = _multi_role_plan_items(rows, final_payload, deterministic_plan)
    return {
        **deterministic_plan,
        "multi_role": True,
        "selected_report_ids": [int(row["id"]) for row in rows],
        "context_strategy": resolved_context_strategy,
        "raw_text_chars": raw_text_chars,
        "model_strategy": model_strategy,
        "model_config": role_model_trace,
        "decision_market_snapshot": decision_market_context or {},
        "available_reports": len(rows),
        "missing_reports": 0,
        "summary": final_payload.get("summary") or "",
        "risk_controls": final_payload.get("risk_controls") or [],
        "role_discussion": role_discussion,
        "recommendations": recommendations,
        "notes": [
            "建仓建议由组合经理、风控经理、交易员、反方审查和最终裁决多角色顺序讨论生成。",
            "上下文包含已勾选并入库的完整 AI 报告内容，以及生成建仓建议当下采集的实时行情/K线快照。",
            "建仓建议不自动写入交易流水或条件单。",
        ],
    }


def write_position_plan(output_dir: Path, plan: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = "multi_role_position_plan" if plan.get("multi_role") else "position_plan"
    json_path = output_dir / f"{prefix}_{stamp}.json"
    md_path = output_dir / f"{prefix}_{stamp}.md"
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [
        "# 批量建仓建议",
        "",
        f"- 可用现金：{plan['cash']:.3f}",
        f"- 候选股票：{plan['candidate_count']}",
        f"- 可用报告：{plan['available_reports']}",
        f"- 缺失报告：{plan['missing_reports']}",
        "",
        "## 建议列表",
        "",
        "| 排名 | 动作 | 股票 | 代码 | 信号 | 评分 | 建议金额 | 仓位占比 | 报告 |",
        "|---:|---|---|---|---|---:|---:|---:|---|",
    ]
    for idx, item in enumerate(plan["recommendations"], start=1):
        lines.append(
            f"| {idx} | {item['action']} | {item['name']} | {item['code']} | {item.get('signal', '')} | {item['score']} | {item['suggested_amount']:.3f} | {item['position_pct']:.3f}% | {item.get('report_id', '')} |"
        )
    lines.extend(["", "## 说明", ""])
    lines.extend(f"- {note}" for note in plan["notes"])
    market_context = plan.get("decision_market_snapshot") or {}
    if market_context:
        lines.extend(
            [
                "",
                "## 决策实时行情快照",
                "",
                f"- 采集时间：{market_context.get('captured_at') or '--'}",
                f"- 状态：{market_context.get('status') or '--'}",
                "",
                "| 股票 | 状态 | 现价 | 涨跌幅% | 5日收益% | 20日收益% |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for item in market_context.get("summary") or []:
            day = item.get("day") if isinstance(item.get("day"), dict) else {}
            lines.append(
                f"| {item.get('code') or ''} | {item.get('status') or ''} | {(_safe_num(item.get('price'), 0.0) or 0.0):.3f} | {(_safe_num(item.get('change_pct'), 0.0) or 0.0):.3f} | {(_safe_num(day.get('return_5d_pct'), 0.0) or 0.0):.3f} | {(_safe_num(day.get('return_20d_pct'), 0.0) or 0.0):.3f} |"
            )
    if plan.get("multi_role"):
        if plan.get("summary"):
            lines.extend(["", "## 最终裁决摘要", "", str(plan["summary"])])
        if plan.get("risk_controls"):
            lines.extend(["", "## 组合风控约束", ""])
            lines.extend(f"- {item}" for item in plan["risk_controls"])
        lines.extend(["", "## 多角色讨论", ""])
        for item in plan.get("role_discussion") or []:
            lines.extend([f"### {item.get('role_name')}", "", str(item.get("content") or ""), ""])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def write_outputs(output_dir: Path, summary: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"batch_research_{stamp}.json"
    md_path = output_dir / f"batch_research_{stamp}.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [
        "# 批量研究任务摘要",
        "",
        f"- 模式：{summary['mode']}",
        f"- 候选股票：{summary['candidate_count']}",
        f"- 计划分析：{summary['planned_count']}",
        f"- 跳过已有报告：{summary.get('skipped_existing_reports', 0)}",
        f"- 已提交：{summary['submitted_count']}",
        f"- 七层快照：复用 {summary['snapshots'].get('reused', 0)} / 保存 {summary['snapshots']['saved']} / 完整 {summary['snapshots']['complete']} / 不完整 {summary['snapshots']['incomplete']} / 失败 {summary['snapshots']['failed']}",
        "",
        "## 候选列表",
        "",
        "| 排名 | 股票 | 代码 | 分组 | 评分 | 状态 | 任务 |",
        "|---:|---|---|---|---:|---|---|",
    ]
    for idx, item in enumerate(summary["candidates"], start=1):
        lines.append(
            f"| {idx} | {item['name']} | {item['code']} | {item['group_name']} | {item['score']} | {item['status']} | {item.get('task_id') or ''} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


async def run_batch_research(
    *,
    db_path: Path,
    group: str,
    include_observation: bool,
    limit: int,
    top_n: int,
    batch_size: int,
    data_only: bool,
    dry_run: bool,
    skip_recent_days: int,
    output_dir: Path,
    depth: str = "standard",
    debate_rounds: int | None = 1,
    risk_rounds: int | None = 1,
    trade_date: str | None = None,
    poll_interval: float = 5.0,
    timeout_seconds: int = 3600,
    snapshot_concurrency: int = 3,
    analysis_mode: str = "snapshot",
    analysis_concurrency: int = 1,
    snapshot_model_tier: str = "deep",
    refresh_snapshots: bool = False,
    plan_top_n: int = 10,
) -> dict[str, Any]:
    ensure_schema(db_path)
    run_id = f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    all_stocks = load_candidates(
        db_path,
        group=group,
        include_observation=include_observation,
        skip_recent_days=0,
    )
    if limit > 0:
        all_stocks = all_stocks[:limit]
    quotes = await fetch_quotes_for(all_stocks)
    ranked_scope = rank_candidates(all_stocks, quotes, top_n=top_n)
    recent_codes = recent_report_codes(db_path, skip_recent_days)
    candidates = [item for item in ranked_scope if item.code not in recent_codes]
    skipped_existing_reports = len(ranked_scope) - len(candidates)
    selected_stocks = [
        StockCandidate(item.code, item.name, item.group_name, item.sort_order)
        for item in candidates
    ]
    plan_stocks = [
        StockCandidate(item.code, item.name, item.group_name, item.sort_order)
        for item in ranked_scope
    ]
    mode = "dry_run" if dry_run else "data_only" if data_only else f"{analysis_mode}_analysis"
    snapshot_summary = {
        "requested": 0,
        "saved": 0,
        "complete": 0,
        "incomplete": 0,
        "failed": 0,
        "run_id": run_id,
        "concurrency": max(1, snapshot_concurrency),
        "reused": 0,
    }
    if not dry_run and selected_stocks:
        snapshot_targets = selected_stocks
        reused_snapshots = 0
        if analysis_mode in {"snapshot-debate", "snapshot-tradingagents"} and not data_only:
            complete_snapshots = sum(1 for stock in selected_stocks if _has_complete_snapshot(db_path, stock.code))
            snapshot_targets = []
            reused_snapshots = complete_snapshots
            snapshot_summary = {
                "requested": len(selected_stocks),
                "saved": 0,
                "complete": complete_snapshots,
                "incomplete": len(selected_stocks) - complete_snapshots,
                "failed": 0,
                "failed_items": [],
                "incomplete_items": [],
                "run_id": run_id,
                "concurrency": 0,
                "reused": complete_snapshots,
            }
        elif analysis_mode == "snapshot" and not data_only and not refresh_snapshots:
            snapshot_targets = [stock for stock in selected_stocks if not _has_complete_snapshot(db_path, stock.code)]
            reused_snapshots = len(selected_stocks) - len(snapshot_targets)
        if snapshot_targets:
            snapshot_summary = await prefetch_seven_layer_snapshots(
                db_path,
                snapshot_targets,
                run_id=run_id,
                trade_date=trade_date or date.today().isoformat(),
                concurrency=snapshot_concurrency,
            )
            snapshot_summary["reused"] = reused_snapshots
        else:
            snapshot_summary = {
                "requested": len(selected_stocks),
                "saved": 0,
                "complete": reused_snapshots,
                "incomplete": 0,
                "failed": 0,
                "failed_items": [],
                "incomplete_items": [],
                "run_id": run_id,
                "concurrency": max(1, snapshot_concurrency),
                "reused": reused_snapshots,
            }
    if not dry_run and not data_only and candidates:
        if analysis_mode == "tradingagents":
            candidates = await submit_batches(
                candidates,
                batch_size=batch_size,
                trade_date=trade_date or date.today().isoformat(),
                depth=depth,
                debate_rounds=debate_rounds,
                risk_rounds=risk_rounds,
                poll_interval=poll_interval,
                timeout_seconds=timeout_seconds,
            )
        elif analysis_mode == "snapshot-debate":
            candidates = await submit_snapshot_debate_reports(
                db_path,
                candidates,
                run_id=run_id,
                concurrency=analysis_concurrency,
                model_tier=snapshot_model_tier,
                timeout_seconds=timeout_seconds,
            )
        elif analysis_mode == "snapshot-tradingagents":
            candidates = await submit_snapshot_tradingagents_reports(
                db_path,
                candidates,
                run_id=run_id,
                concurrency=analysis_concurrency,
                model_tier=snapshot_model_tier,
                timeout_seconds=timeout_seconds,
            )
        else:
            candidates = await submit_snapshot_reports(
                db_path,
                candidates,
                run_id=run_id,
                concurrency=analysis_concurrency,
                model_tier=snapshot_model_tier,
                timeout_seconds=timeout_seconds,
            )

    position_plan = None
    position_plan_outputs = {}
    if not dry_run and not data_only and plan_stocks:
        position_plan = build_position_plan(db_path, plan_stocks, top_n=plan_top_n)
        position_plan_outputs = write_position_plan(output_dir, position_plan)

    summary = {
        "mode": mode,
        "db_path": str(db_path),
        "run_id": run_id,
        "group": group,
        "include_observation": include_observation,
        "skip_recent_days": skip_recent_days,
        "candidate_count": len(all_stocks),
        "ranked_scope_count": len(ranked_scope),
        "planned_count": len(candidates),
        "skipped_existing_reports": skipped_existing_reports,
        "submitted_count": sum(1 for item in candidates if item.task_id),
        "depth": depth,
        "debate_rounds": debate_rounds,
        "risk_rounds": risk_rounds,
        "batch_size": batch_size,
        "analysis_mode": analysis_mode,
        "analysis_concurrency": analysis_concurrency,
        "snapshot_model_tier": snapshot_model_tier,
        "refresh_snapshots": refresh_snapshots,
        "snapshots": snapshot_summary,
        "position_plan": position_plan,
        "position_plan_outputs": position_plan_outputs,
        "candidates": [asdict(item) for item in candidates],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    summary["outputs"] = write_outputs(output_dir, summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch prewarm market data and submit controlled AI research tasks.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--group", default="默认", help="默认、观察池或 all")
    parser.add_argument("--include-observation", action="store_true", help="group=默认 时同时纳入观察池")
    parser.add_argument("--limit", type=int, default=0, help="最多读取多少只候选，0 表示不限")
    parser.add_argument("--top-n", type=int, default=0, help="粗筛后实际计划分析多少只，0 表示全部")
    parser.add_argument("--batch-size", type=int, default=2, help="每批提交多少个 AI 任务")
    parser.add_argument("--depth", default="standard", choices=["quick", "standard", "deep"])
    parser.add_argument("--debate-rounds", type=int, default=1)
    parser.add_argument("--risk-rounds", type=int, default=1)
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--skip-recent-days", type=int, default=7)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--snapshot-concurrency", type=int, default=3, help="七层数据快照并发数")
    parser.add_argument(
        "--analysis-mode",
        default="snapshot",
        choices=["snapshot", "snapshot-debate", "snapshot-tradingagents", "tradingagents"],
        help="snapshot=单次读取已入库七层快照生成报告；snapshot-debate=7角色离线辩论；snapshot-tradingagents=快照版TradingAgents完整流程；tradingagents=原生在线链路",
    )
    parser.add_argument("--analysis-concurrency", type=int, default=1, help="snapshot 模式下并发生成报告数")
    parser.add_argument("--snapshot-model-tier", default="deep", choices=["quick", "deep"], help="snapshot 模式使用快速或深度模型")
    parser.add_argument("--refresh-snapshots", action="store_true", help="snapshot 分析前强制重新拉取七层快照")
    parser.add_argument("--plan-top-n", type=int, default=10, help="建仓建议最多给多少只买入候选")
    parser.add_argument("--data-only", action="store_true", help="只拉行情和生成候选报告，不提交 AI")
    parser.add_argument("--apply", action="store_true", help="实际提交 AI 任务；不传默认 dry-run")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "batch_research")
    return parser


async def async_main() -> int:
    args = build_parser().parse_args()
    summary = await run_batch_research(
        db_path=args.db,
        group=args.group,
        include_observation=args.include_observation,
        limit=args.limit,
        top_n=args.top_n,
        batch_size=args.batch_size,
        data_only=args.data_only,
        dry_run=not args.apply,
        skip_recent_days=args.skip_recent_days,
        output_dir=args.output_dir,
        depth=args.depth,
        debate_rounds=args.debate_rounds,
        risk_rounds=args.risk_rounds,
        trade_date=args.trade_date,
        poll_interval=args.poll_interval,
        timeout_seconds=args.timeout_seconds,
        snapshot_concurrency=args.snapshot_concurrency,
        analysis_mode=args.analysis_mode,
        analysis_concurrency=args.analysis_concurrency,
        snapshot_model_tier=args.snapshot_model_tier,
        refresh_snapshots=args.refresh_snapshots,
        plan_top_n=args.plan_top_n,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    if not args.apply:
        print("预览模式：未提交 AI 任务。确认后加 --apply 执行。")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
