"""Self-evolving recommendation feedback loop for AI report context."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import models.database as database
from services import trade_memory_service

SELF_EVOLUTION_VERSION = "self-evolution-v3"


def _db_path(path: Path | None = None) -> Path:
    return path or database.DB_PATH


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path(db_path)))
    conn.row_factory = sqlite3.Row
    return conn


def _loads(value: Any, fallback: Any):
    if value in ("", None):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed is not None else fallback


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("T", " ").split(".")[0]
    for candidate, fmt in ((text[:19], "%Y-%m-%d %H:%M:%S"), (text[:10], "%Y-%m-%d")):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _win_rate(values: list[float]) -> float | None:
    return round(sum(1 for value in values if value > 0) / len(values) * 100, 3) if values else None


def _completed_trade_cycles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cycles: list[dict[str, Any]] = []
    state: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (str(item.get("account_id") or "default"), str(item.get("code") or ""), str(item.get("trade_time") or ""), int(item.get("id") or 0))):
        key = (str(row.get("account_id") or "default"), str(row.get("code") or ""))
        direction = str(row.get("direction") or "").lower()
        shares = _num(row.get("shares"))
        amount = _num(row.get("amount"))
        fee = _num(row.get("commission")) + _num(row.get("stamp_tax")) + _num(row.get("transfer_fee"))
        current = state.setdefault(key, {"shares": 0.0, "cost": 0.0, "buy_amount": 0.0, "pnl": 0.0, "trade_ids": [], "name": row.get("name") or key[1], "opened_at": None, "closed_at": None})
        if direction == "buy" and shares > 0:
            if current["shares"] <= 0:
                current.update({"shares": 0.0, "cost": 0.0, "buy_amount": 0.0, "pnl": 0.0, "trade_ids": [], "name": row.get("name") or key[1], "opened_at": row.get("trade_time"), "closed_at": None})
            current["shares"] += shares
            current["cost"] += amount + fee
            current["buy_amount"] += amount
            current["trade_ids"].append(int(row.get("id") or 0))
        elif direction == "sell" and shares > 0 and current["shares"] > 0:
            avg_cost = current["cost"] / current["shares"] if current["shares"] else 0.0
            matched = min(shares, current["shares"])
            proceeds = amount * (matched / shares)
            current["pnl"] += proceeds - fee - avg_cost * matched
            current["shares"] = max(0.0, current["shares"] - matched)
            current["cost"] = avg_cost * current["shares"]
            current["trade_ids"].append(int(row.get("id") or 0))
            current["closed_at"] = row.get("trade_time")
            if current["shares"] <= 0.000001 and current["buy_amount"] > 0:
                cycles.append({
                    "code": key[1],
                    "name": current.get("name") or key[1],
                    "trade_ids": list(current.get("trade_ids") or []),
                    "opened_at": current.get("opened_at"),
                    "closed_at": current.get("closed_at"),
                    "realized_pnl": round(current["pnl"], 3),
                    "realized_pnl_pct": round(current["pnl"] / current["buy_amount"] * 100, 3),
                })
                state[key] = {"shares": 0.0, "cost": 0.0, "buy_amount": 0.0, "pnl": 0.0, "trade_ids": [], "name": row.get("name") or key[1], "opened_at": None, "closed_at": None}
    return cycles


def _research_signal_layer(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT st.signal, st.pnl_pct, st.excess_return, ar.raw_state, ar.confidence, ar.risk_score
        FROM signal_tracking st
        LEFT JOIN analysis_reports ar ON ar.id = st.report_id
        WHERE st.pnl_pct IS NOT NULL
        ORDER BY st.created_at DESC, st.id DESC
        """
    ).fetchall()
    pnl_values = [_num(row["pnl_pct"]) for row in rows]
    by_signal: dict[str, list[float]] = {}
    research_signal_count = 0
    account_signal_count = 0
    for row in rows:
        signal = str(row["signal"] or "UNKNOWN").upper()
        by_signal.setdefault(signal, []).append(_num(row["pnl_pct"]))
        raw = _loads(row["raw_state"], {})
        if raw.get("research_signal"):
            research_signal_count += 1
        if raw.get("account_signal"):
            account_signal_count += 1
    buckets = [
        {
            "signal": signal,
            "count": len(values),
            "win_rate": _win_rate(values),
            "avg_pnl_pct": _avg(values),
        }
        for signal, values in sorted(by_signal.items())
    ]
    return {
        "sample_count": len(rows),
        "win_rate": _win_rate(pnl_values),
        "avg_pnl_pct": _avg(pnl_values),
        "by_signal": buckets,
        "has_split_research_account_signal": account_signal_count > 0 and research_signal_count > 0,
    }


def _account_action_layer(conn: sqlite3.Connection) -> dict[str, Any]:
    plan_rows = conn.execute(
        """
        SELECT pp.plan_id, pp.context_strategy, ppi.action, ppi.suggested_amount,
               ppi.position_pct, ppi.source_report_id, st.pnl_pct
        FROM position_plans pp
        LEFT JOIN position_plan_items ppi ON ppi.plan_id = pp.plan_id
        LEFT JOIN signal_tracking st ON st.report_id = ppi.source_report_id
        WHERE pp.adoption_status = 'adopted'
          AND pp.status != 'archived'
        ORDER BY pp.created_at DESC, ppi.id ASC
        """
    ).fetchall()
    plans = sorted({row["plan_id"] for row in plan_rows if row["plan_id"]})
    actionable = [row for row in plan_rows if str(row["action"] or "").lower() in {"buy", "add", "overweight"}]
    tracked = [_num(row["pnl_pct"]) for row in plan_rows if row["pnl_pct"] is not None]
    max_position_pct = max((_num(row["position_pct"]) for row in plan_rows), default=0.0)
    max_amount = max((_num(row["suggested_amount"]) for row in plan_rows), default=0.0)
    return {
        "adopted_plan_count": len(plans),
        "item_count": len([row for row in plan_rows if row["plan_id"]]),
        "actionable_count": len(actionable),
        "tracked_count": len(tracked),
        "win_rate": _win_rate(tracked),
        "avg_pnl_pct": _avg(tracked),
        "max_position_pct": round(max_position_pct, 3),
        "max_suggested_amount": round(max_amount, 3),
    }


def _trade_memory_layer(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT *
        FROM trade_memories
        WHERE status = 'active'
        ORDER BY datetime(updated_at) DESC, id DESC
        """
    ).fetchall()
    memories = [trade_memory_service._decode_memory(row) for row in rows]
    failures = [item for item in memories if item.get("outcome") == "failure"]
    successes = [item for item in memories if item.get("outcome") == "success"]
    vetoes: list[str] = []
    tags: dict[str, int] = {}
    for item in memories:
        for veto in item.get("veto_lessons") or []:
            if veto and veto not in vetoes:
                vetoes.append(str(veto))
        for tag in item.get("lesson_tags") or []:
            tags[str(tag)] = tags.get(str(tag), 0) + 1
    return {
        "active_count": len(memories),
        "success_count": len(successes),
        "failure_count": len(failures),
        "net_realized_pnl": round(sum(_num(item.get("realized_pnl")) for item in memories), 3),
        "top_tags": sorted(tags, key=lambda key: (-tags[key], key))[:8],
        "veto_lessons": vetoes[:5],
    }


def _realized_outcome_layer(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT id, account_id, code, direction, shares, amount,
               commission, stamp_tax, transfer_fee, trade_time
        FROM trades
        ORDER BY account_id ASC, code ASC, datetime(trade_time) ASC, id ASC
        """
    ).fetchall()
    cycles = _completed_trade_cycles([dict(row) for row in rows])
    pnl = [_num(item.get("realized_pnl")) for item in cycles]
    pnl_pct = [_num(item.get("realized_pnl_pct")) for item in cycles]
    return {
        "closed_trade_count": len(cycles),
        "win_rate": _win_rate(pnl),
        "total_realized_pnl": round(sum(pnl), 3),
        "avg_realized_pnl_pct": _avg(pnl_pct),
        "loss_count": sum(1 for value in pnl if value < 0),
        "win_count": sum(1 for value in pnl if value > 0),
    }


def _score_layer(layer: dict[str, Any], default: float = 60.0) -> float:
    if not layer:
        return default
    score = default
    win_rate = layer.get("win_rate")
    avg_pnl = layer.get("avg_pnl_pct") if layer.get("avg_pnl_pct") is not None else layer.get("avg_realized_pnl_pct")
    if win_rate is not None:
        score += (_num(win_rate) - 50) * 0.4
    if avg_pnl is not None:
        score += max(-20, min(20, _num(avg_pnl))) * 1.2
    if layer.get("failure_count"):
        score -= min(15, _num(layer.get("failure_count")) * 3)
    if layer.get("loss_count"):
        score -= min(10, _num(layer.get("loss_count")) * 2)
    return round(max(0, min(100, score)), 3)


def _rules(layers: dict[str, Any]) -> list[dict[str, str]]:
    rules: list[dict[str, Any]] = []
    research = layers.get("research_signal") or {}
    account = layers.get("account_action") or {}
    memory = layers.get("trade_memory") or {}
    realized = layers.get("realized_outcome") or {}
    if _num(research.get("avg_pnl_pct"), 0) < 0:
        rules.append({
            "scope": "research_signal",
            "rule": "研究信号近期平均收益为负时，BUY/OVERWEIGHT 不直接升级为重仓动作，必须二次检查估值、信号冲突和止损。",
            "evidence": [{"source_type": "signal_tracking", "source_id": "aggregate", "metric": "avg_pnl_pct", "value": research.get("avg_pnl_pct")}],
        })
    if account.get("max_position_pct") and _num(account.get("max_position_pct")) >= 0.25:
        rules.append({
            "scope": "account_action",
            "rule": "仓位计划出现单票25%以上建议时，必须给出最大亏损金额和分批执行条件。",
            "evidence": [{"source_type": "position_plan", "source_id": "adopted", "metric": "max_position_pct", "value": account.get("max_position_pct")}],
        })
    if memory.get("failure_count"):
        veto = "；".join((memory.get("veto_lessons") or [])[:2])
        rules.append({
            "scope": "trade_memory",
            "rule": f"失败记忆触发时默认降低仓位或观察；重点否决：{veto or '历史失败场景重复出现'}",
            "evidence": [{"source_type": "trade_memory", "source_id": "active_failures", "metric": "failure_count", "value": memory.get("failure_count")}],
        })
    if _num(realized.get("total_realized_pnl"), 0) < 0:
        rules.append({
            "scope": "realized_outcome",
            "rule": "真实闭环盈亏为负时，下一次推荐必须降低首仓比例，并明确不执行条件。",
            "evidence": [{"source_type": "trade_cycle", "source_id": "closed_trades", "metric": "total_realized_pnl", "value": realized.get("total_realized_pnl")}],
        })
    if not rules:
        rules.append({
            "scope": "system",
            "rule": "继续保持研究信号、账户动作、交易记忆和真实盈亏分层输出，禁止把单一信号直接变成交易指令。",
            "evidence": [{"source_type": "signal_tracking", "source_id": "aggregate", "metric": "sample_count", "value": research.get("sample_count", 0)}],
        })
    return rules


def _text_terms(text: str) -> set[str]:
    terms = set(trade_memory_service.extract_scenario_tags(text))
    for token in ("涨停", "追入", "追涨", "高估值", "低估值", "仓位", "信号冲突", "主线", "左侧", "回踩", "止盈", "止损"):
        if token in text:
            terms.add(token)
    return terms


def semantic_memory_search(
    query: str,
    *,
    limit: int = 10,
    embedding_provider: Any | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    related = trade_memory_service.related_trade_memories(
        report_text=str(query or ""),
        limit=limit,
        embedding_provider=embedding_provider,
        db_path=db_path,
    )
    matches = []
    for item in related.get("matches") or []:
        matched = item.get("matched_tags") or []
        matches.append(
            {
                **item,
                "score": item.get("match_score") or item.get("score") or 0,
                "matched_terms": matched,
            }
        )
    return {
        "query": query,
        "retrieval_mode": related.get("retrieval_mode") or "rules",
        "query_terms": related.get("scenario_tags") or sorted(_text_terms(str(query or ""))),
        "count": len(matches[:limit]),
        "matches": matches[:max(1, min(int(limit or 10), 50))],
    }


def _recommendation_attributions(conn: sqlite3.Connection, snapshot_id: str) -> list[dict[str, Any]]:
    report_rows = conn.execute(
        """
        SELECT ar.id, ar.code, COALESCE(ar.signal, '') AS signal, ar.confidence,
               ar.risk_score, ar.created_at, st.pnl_pct AS tracking_pnl_pct
        FROM analysis_reports ar
        LEFT JOIN signal_tracking st ON st.report_id = ar.id
        WHERE UPPER(COALESCE(ar.signal, '')) IN ('STRONG_BUY', 'BUY', 'OVERWEIGHT')
        ORDER BY ar.created_at ASC, ar.id ASC
        """
    ).fetchall()
    trade_rows = conn.execute(
        """
        SELECT id, account_id, code, name, direction, shares, amount,
               commission, stamp_tax, transfer_fee, trade_time
        FROM trades
        ORDER BY account_id ASC, code ASC, datetime(trade_time) ASC, id ASC
        """
    ).fetchall()
    memory_rows = conn.execute(
        "SELECT id, code FROM trade_memories WHERE status='active'"
    ).fetchall()
    cycles_by_code: dict[str, list[dict[str, Any]]] = {}
    for cycle in _completed_trade_cycles([dict(row) for row in trade_rows]):
        cycles_by_code.setdefault(cycle["code"], []).append(cycle)
    memory_ids_by_code: dict[str, list[int]] = {}
    for row in memory_rows:
        memory_ids_by_code.setdefault(row["code"], []).append(int(row["id"]))
    reports_by_code: dict[str, list[sqlite3.Row]] = {}
    for row in report_rows:
        reports_by_code.setdefault(row["code"], []).append(row)
    items = []
    for code, rows in reports_by_code.items():
        cycles = cycles_by_code.get(code) or []
        if cycles:
            sorted_cycles = sorted(cycles, key=lambda item: (_dt(item.get("opened_at")) or datetime.max, item.get("trade_ids") or []))
            previous_close: datetime | None = None
            for cycle in sorted_cycles:
                opened_at = _dt(cycle.get("opened_at"))
                if not opened_at:
                    continue
                cycle_rows = [
                    row for row in rows
                    if (created_at := _dt(row["created_at"]))
                    and created_at <= opened_at
                    and (previous_close is None or created_at > previous_close)
                ]
                previous_close = _dt(cycle.get("closed_at")) or previous_close
                if not cycle_rows:
                    continue
                pnl = _num(cycle.get("realized_pnl"))
                pnl_pct_values = [_num(cycle.get("realized_pnl_pct"))]
                tracking_values = [_num(row["tracking_pnl_pct"]) for row in cycle_rows if row["tracking_pnl_pct"] is not None]
                if pnl < 0:
                    outcome = "loss"
                elif pnl > 0:
                    outcome = "win"
                elif any(value < 0 for value in tracking_values):
                    outcome = "loss"
                elif any(value > 0 for value in tracking_values):
                    outcome = "win"
                else:
                    outcome = "neutral"
                items.append(
                    {
                        "snapshot_id": snapshot_id,
                        "code": code,
                        "name": cycle.get("name") or code,
                        "outcome": outcome,
                        "realized_pnl": round(pnl, 3),
                        "realized_pnl_pct": _avg(pnl_pct_values) or 0.0,
                        "tracking_pnl_pct": _avg(tracking_values),
                        "source_report_ids": [int(row["id"]) for row in cycle_rows],
                        "trade_ids": list(cycle.get("trade_ids") or []),
                        "memory_ids": memory_ids_by_code.get(code, []),
                        "evidence": {
                            "signals": [str(row["signal"] or "") for row in cycle_rows],
                            "risk_scores": [_num(row["risk_score"]) for row in cycle_rows],
                            "confidence": [_num(row["confidence"]) for row in cycle_rows],
                        },
                    }
                )
            continue
        else:
            eligible_cycles = []
            eligible_rows = rows
        pnl = sum(_num(cycle.get("realized_pnl")) for cycle in eligible_cycles)
        pnl_pct_values = [_num(cycle.get("realized_pnl_pct")) for cycle in eligible_cycles]
        tracking_values = [_num(row["tracking_pnl_pct"]) for row in eligible_rows if row["tracking_pnl_pct"] is not None]
        if pnl < 0 or any(value < 0 for value in tracking_values):
            outcome = "loss"
        elif pnl > 0 or any(value > 0 for value in tracking_values):
            outcome = "win"
        else:
            outcome = "neutral"
        name = next((cycle.get("name") for cycle in eligible_cycles if cycle.get("name")), code)
        items.append(
            {
                "snapshot_id": snapshot_id,
                "code": code,
                "name": name,
                "outcome": outcome,
                "realized_pnl": round(pnl, 3),
                "realized_pnl_pct": _avg(pnl_pct_values) or 0.0,
                "tracking_pnl_pct": _avg(tracking_values),
                "source_report_ids": [int(row["id"]) for row in eligible_rows],
                "trade_ids": [trade_id for cycle in eligible_cycles for trade_id in (cycle.get("trade_ids") or [])],
                "memory_ids": memory_ids_by_code.get(code, []),
                "evidence": {
                    "signals": [str(row["signal"] or "") for row in eligible_rows],
                    "risk_scores": [_num(row["risk_score"]) for row in eligible_rows],
                    "confidence": [_num(row["confidence"]) for row in eligible_rows],
                },
            }
        )
    return items


def _constraints() -> dict[str, Any]:
    return {
        "scope": "account_action_only",
        "must_keep_separate": ["research_signal", "account_signal", "trade_memory", "realized_outcome"],
        "forbidden": ["overwrite_research_signal", "auto_trade", "hide_failed_lessons"],
        "required_outputs": ["self_evolution_match", "discipline_adjustments"],
    }


def snapshot_context(snapshot: dict[str, Any]) -> str:
    layers = snapshot.get("layers") or {}
    rules = snapshot.get("rules") or []
    lines = [
        "【AI自我进化画像】",
        f"- 版本：{snapshot.get('version') or SELF_EVOLUTION_VERSION}",
        f"- 系统评分：{snapshot.get('system_score')} / 100",
        "- 约束：只校准账户动作、仓位、否决项和复盘问题；不得改写股票研究信号，不得自动下单。",
        "- 分层要求：research_signal、account_action、trade_memory、realized_outcome 必须分开评价。",
    ]
    for key in ("research_signal", "account_action", "trade_memory", "realized_outcome"):
        layer = layers.get(key) or {}
        sample_count = layer.get("sample_count") or layer.get("item_count") or layer.get("active_count") or layer.get("closed_trade_count") or 0
        summary_bits = []
        for field in ("win_rate", "avg_pnl_pct", "avg_realized_pnl_pct", "total_realized_pnl", "failure_count"):
            if layer.get(field) is not None:
                summary_bits.append(f"{field}={layer.get(field)}")
        lines.append(f"- {key}: samples={sample_count}; {'; '.join(summary_bits) or '暂无可评价数据'}")
    for rule in rules[:5]:
        lines.append(f"- 进化规则[{rule.get('scope')}]: {rule.get('rule')}")
    return "\n".join(lines)


def build_snapshot(*, db_path: Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        layers = {
            "research_signal": _research_signal_layer(conn),
            "account_action": _account_action_layer(conn),
            "trade_memory": _trade_memory_layer(conn),
            "realized_outcome": _realized_outcome_layer(conn),
        }
    scores = {
        key: _score_layer(value)
        for key, value in layers.items()
    }
    source_counts = {
        "signal_tracking": layers["research_signal"].get("sample_count", 0),
        "adopted_position_plan_items": layers["account_action"].get("item_count", 0),
        "active_trade_memories": layers["trade_memory"].get("active_count", 0),
        "closed_trades": layers["realized_outcome"].get("closed_trade_count", 0),
    }
    rules = _rules(layers)
    system_score = round(sum(scores.values()) / len(scores), 3)
    snapshot = {
        "snapshot_id": f"sev3-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:6]}",
        "version": SELF_EVOLUTION_VERSION,
        "status": "draft",
        "system_score": system_score,
        "source_counts": source_counts,
        "layer_scores": scores,
        "layers": layers,
        "rules": rules,
        "constraints": _constraints(),
    }
    snapshot["context"] = snapshot_context(snapshot)
    return snapshot


def _decode_snapshot(row: sqlite3.Row | None) -> dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    item["source_counts"] = _loads(item.pop("source_counts_json", None), {})
    item["layers"] = _loads(item.pop("layers_json", None), {})
    item["rules"] = _loads(item.pop("rules_json", None), [])
    item["constraints"] = _loads(item.pop("constraints_json", None), {})
    return item


def _decode_attribution(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["source_report_ids"] = _loads(item.pop("source_report_ids_json", None), [])
    item["trade_ids"] = _loads(item.pop("trade_ids_json", None), [])
    item["memory_ids"] = _loads(item.pop("memory_ids_json", None), [])
    item["evidence"] = _loads(item.pop("evidence_json", None), {})
    return item


def run_cycle(*, db_path: Path | None = None) -> dict[str, Any]:
    snapshot = build_snapshot(db_path=db_path)
    snapshot["status"] = "active"
    snapshot["context"] = snapshot_context(snapshot)
    with _connect(db_path) as conn:
        conn.execute("UPDATE self_evolution_snapshots SET status='archived' WHERE status='active'")
        conn.execute(
            """
            INSERT INTO self_evolution_snapshots
                (snapshot_id, version, status, system_score, source_counts_json,
                 layers_json, rules_json, constraints_json, context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["snapshot_id"],
                snapshot["version"],
                snapshot["status"],
                snapshot["system_score"],
                _dumps(snapshot["source_counts"]),
                _dumps(snapshot["layers"]),
                _dumps(snapshot["rules"]),
                _dumps(snapshot["constraints"]),
                snapshot["context"],
            ),
        )
        conn.execute("DELETE FROM recommendation_attributions WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
        attributions = _recommendation_attributions(conn, snapshot["snapshot_id"])
        conn.executemany(
            """
            INSERT INTO recommendation_attributions
                (snapshot_id, code, name, outcome, realized_pnl, realized_pnl_pct,
                 tracking_pnl_pct, source_report_ids_json, trade_ids_json,
                 memory_ids_json, evidence_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["snapshot_id"],
                    item["code"],
                    item["name"],
                    item["outcome"],
                    item["realized_pnl"],
                    item["realized_pnl_pct"],
                    item["tracking_pnl_pct"],
                    _dumps(item["source_report_ids"]),
                    _dumps(item["trade_ids"]),
                    _dumps(item["memory_ids"]),
                    _dumps(item["evidence"]),
                )
                for item in attributions
            ],
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM self_evolution_snapshots WHERE snapshot_id = ?",
            (snapshot["snapshot_id"],),
        ).fetchone()
    return _decode_snapshot(row)


def latest_snapshot(*, db_path: Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM self_evolution_snapshots
            WHERE status = 'active'
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    return _decode_snapshot(row)


def latest_context(*, db_path: Path | None = None) -> str:
    snapshot = latest_snapshot(db_path=db_path)
    return str(snapshot.get("context") or "")


def list_recommendation_attributions(*, snapshot_id: str | None = None, limit: int = 100, db_path: Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        resolved_snapshot_id = snapshot_id
        if not resolved_snapshot_id:
            row = conn.execute(
                """
                SELECT snapshot_id
                FROM self_evolution_snapshots
                WHERE status = 'active'
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            resolved_snapshot_id = row["snapshot_id"] if row else ""
        if not resolved_snapshot_id:
            return {"count": 0, "items": []}
        rows = conn.execute(
            """
            SELECT *
            FROM recommendation_attributions
            WHERE snapshot_id = ?
            ORDER BY
                CASE outcome WHEN 'loss' THEN 0 WHEN 'neutral' THEN 1 ELSE 2 END,
                ABS(realized_pnl) DESC,
                code ASC
            LIMIT ?
            """,
            (resolved_snapshot_id, max(1, min(int(limit or 100), 500))),
        ).fetchall()
    items = [_decode_attribution(row) for row in rows]
    return {"snapshot_id": resolved_snapshot_id, "count": len(items), "items": items}
