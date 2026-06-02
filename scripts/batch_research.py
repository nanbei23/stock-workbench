#!/usr/bin/env python3
"""Offline batch research runner for watchlist stocks.

This script is intentionally detached from onboarding. It lets the user warm
market data, rank candidates, and then submit a controlled number of AI debate
tasks in small batches.
"""

from __future__ import annotations

import argparse
import asyncio
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
from data.quote import get_batch_quotes  # noqa: E402
from models.database import SCHEMA  # noqa: E402
from scheduler.ai_engine import extract_confidence, extract_risk_score, extract_signal, extract_target_price  # noqa: E402
from services import ai_analysis_service, ai_task_service  # noqa: E402


TERMINAL_STATUS = {"completed", "failed", "timeout", "cancelled"}
SNAPSHOT_LAYERS = ("market", "social", "news", "fundamentals", "policy", "hot_money", "lockup")
POSITIVE_SIGNALS = {"STRONG_BUY", "BUY", "OVERWEIGHT", "ACCUMULATE", "ADD"}
WATCH_SIGNALS = {"HOLD", "WATCH", "NEUTRAL"}


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
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "snapshot": json.loads(row["snapshot_json"] or "{}"),
        "validation": json.loads(row["validation_json"] or "{}"),
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
) -> int:
    normalized = _normalise_snapshot_result(result)
    raw_state = {
        "source": "snapshot_report",
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
                "snapshot",
                "snapshot_report",
            ),
        )
        conn.commit()
        report_id = conn.execute("SELECT id FROM analysis_reports WHERE task_id = ?", (task_id,)).fetchone()["id"]
    return int(report_id)


async def _invoke_tool(tool: Any, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(tool.invoke, payload)
        return {"ok": True, "payload": _clip_text(result)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def fetch_seven_layer_snapshot(stock: StockCandidate, *, trade_date: str | None = None) -> dict[str, Any]:
    """Fetch the seven data layers used by the AI research pipeline."""
    from data.helpers import tencent_quote_batch
    from tradingagents.agents.utils.core_stock_tools import get_stock_data
    from tradingagents.agents.utils.fundamental_data_tools import (
        get_balance_sheet,
        get_cashflow,
        get_fundamentals,
    )
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
            return {"ok": True, "payload": quotes.get(code, {})}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    stock_data, indicators, quote, news, global_news, fundamentals, balance_sheet, cashflow, insider = await asyncio.gather(
        _invoke_tool(get_stock_data, {"symbol": code, "start_date": start, "end_date": today}),
        _invoke_tool(get_indicators, {"symbol": code, "indicator": "all", "curr_date": today}),
        quote_layer(),
        _invoke_tool(get_news, {"ticker": code, "start_date": start, "end_date": today}),
        _invoke_tool(get_global_news, {"curr_date": today}),
        _invoke_tool(get_fundamentals, {"ticker": code, "curr_date": today}),
        _invoke_tool(get_balance_sheet, {"ticker": code}),
        _invoke_tool(get_cashflow, {"ticker": code}),
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
                if isinstance(item, dict) and item.get("ok") is False:
                    errors.append(f"{key}: {item.get('error') or 'unknown error'}")
        if errors:
            layer_errors[layer] = errors
    return {
        "ok": not missing_layers and not empty_layers and not layer_errors,
        "missing_layers": missing_layers,
        "empty_layers": empty_layers,
        "layer_errors": layer_errors,
        "checked_layers": list(SNAPSHOT_LAYERS),
    }


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


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _cash_balance(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT value FROM settings WHERE key = 'cash_balance_default'").fetchone()
    value = _float_or_none(row["value"] if row else None)
    return round(value or 0.0, 3)


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
        reports = _latest_reports(conn, [stock.code for stock in stocks])

    items = [_report_to_plan_item(stock, reports.get(stock.code)) for stock in stocks]
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


def _position_discussion_prompt(
    *,
    role_name: str,
    role_goal: str,
    cash: float,
    top_n: int,
    report_context: str,
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
- 只能基于下方已入库的完整 AI 报告内容和已有角色观点判断。
- 不允许自行联网，不允许编造报告没有提供的数据。
- 当前可用现金：{cash:.3f}
- 最多给出 {top_n} 只买入候选。
- 建仓建议不写库、不下单，只生成研究建议。
- 金额和百分比保留三位小数。
{final_instruction}

已有角色讨论：
{previous}

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
    items = [
        _report_to_plan_item(
            StockCandidate(row["code"], row["watch_name"] or row["code"], row["group_name"] or "默认", 0),
            row,
        )
        for row in rows
    ]
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
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    with _connect(db_path) as conn:
        cash = _cash_balance(conn)
    rows = _load_position_reports(db_path, stocks, report_ids)
    if not rows:
        raise RuntimeError("缺少可用于多角色建仓建议的完整报告")
    deterministic_plan = _build_position_plan_from_report_rows(db_path, rows, top_n=top_n)
    report_context = "\n\n".join(_report_context_block(row) for row in rows)
    role_discussion: list[dict[str, str]] = []
    llm_config = config or _snapshot_llm_config(db_path, model_tier="deep")
    for role_key, role_name, role_goal in POSITION_PLAN_ROLES:
        role = {"role_key": role_key, "role_name": role_name, "role_goal": role_goal}
        prompt = _position_discussion_prompt(
            role_name=role_name,
            role_goal=role_goal,
            cash=cash,
            top_n=top_n,
            report_context=report_context,
            previous_discussion=role_discussion,
        )
        content = await _call_position_plan_role_llm(role, prompt, llm_config, timeout_seconds=timeout_seconds)
        role_discussion.append({"role_key": role_key, "role_name": role_name, "content": content})
    final_payload = _parse_position_plan_final(role_discussion[-1]["content"])
    recommendations = _multi_role_plan_items(rows, final_payload, deterministic_plan)
    return {
        **deterministic_plan,
        "multi_role": True,
        "selected_report_ids": [int(row["id"]) for row in rows],
        "available_reports": len(rows),
        "missing_reports": 0,
        "summary": final_payload.get("summary") or "",
        "risk_controls": final_payload.get("risk_controls") or [],
        "role_discussion": role_discussion,
        "recommendations": recommendations,
        "notes": [
            "建仓建议由组合经理、风控经理、交易员、反方审查和最终裁决多角色顺序讨论生成。",
            "上下文仅包含已勾选并入库的完整 AI 报告内容，不自动写入交易流水或条件单。",
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
    timeout_seconds: int = 1800,
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
        if analysis_mode == "snapshot" and not data_only and not refresh_snapshots:
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
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--snapshot-concurrency", type=int, default=3, help="七层数据快照并发数")
    parser.add_argument(
        "--analysis-mode",
        default="snapshot",
        choices=["snapshot", "tradingagents"],
        help="snapshot=读取已入库七层快照生成报告；tradingagents=走原生 TradingAgents 在线链路",
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
