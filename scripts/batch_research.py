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
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DB_PATH  # noqa: E402
from data.quote import get_batch_quotes  # noqa: E402
from models.database import SCHEMA  # noqa: E402
from scheduler.ta_bridge import PIPELINE_STAGES  # noqa: E402
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


def write_position_plan(output_dir: Path, plan: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"position_plan_{stamp}.json"
    md_path = output_dir / f"position_plan_{stamp}.md"
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
        f"- 已提交：{summary['submitted_count']}",
        f"- 七层快照：保存 {summary['snapshots']['saved']} / 完整 {summary['snapshots']['complete']} / 不完整 {summary['snapshots']['incomplete']} / 失败 {summary['snapshots']['failed']}",
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
    plan_top_n: int = 10,
) -> dict[str, Any]:
    ensure_schema(db_path)
    run_id = f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    stocks = load_candidates(
        db_path,
        group=group,
        include_observation=include_observation,
        skip_recent_days=skip_recent_days,
    )
    if limit > 0:
        stocks = stocks[:limit]
    quotes = await fetch_quotes_for(stocks)
    candidates = rank_candidates(stocks, quotes, top_n=top_n)
    selected_stocks = [
        StockCandidate(item.code, item.name, item.group_name, item.sort_order)
        for item in candidates
    ]
    mode = "dry_run" if dry_run else "data_only" if data_only else "full_chain"
    snapshot_summary = {
        "requested": 0,
        "saved": 0,
        "complete": 0,
        "incomplete": 0,
        "failed": 0,
        "run_id": run_id,
        "concurrency": max(1, snapshot_concurrency),
    }
    if not dry_run and selected_stocks:
        snapshot_summary = await prefetch_seven_layer_snapshots(
            db_path,
            selected_stocks,
            run_id=run_id,
            trade_date=trade_date or date.today().isoformat(),
            concurrency=snapshot_concurrency,
        )
    if not dry_run and not data_only and candidates:
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

    position_plan = None
    position_plan_outputs = {}
    if not dry_run and not data_only and selected_stocks:
        position_plan = build_position_plan(db_path, selected_stocks, top_n=plan_top_n)
        position_plan_outputs = write_position_plan(output_dir, position_plan)

    summary = {
        "mode": mode,
        "db_path": str(db_path),
        "run_id": run_id,
        "group": group,
        "include_observation": include_observation,
        "skip_recent_days": skip_recent_days,
        "candidate_count": len(stocks),
        "planned_count": len(candidates),
        "submitted_count": sum(1 for item in candidates if item.task_id),
        "depth": depth,
        "debate_rounds": debate_rounds,
        "risk_rounds": risk_rounds,
        "batch_size": batch_size,
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
