"""AI report and anomaly presentation helpers."""

import json
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from models.database import get_db
from repositories import ai_report_repository as repo

logger = logging.getLogger(__name__)
CN_TZ = ZoneInfo("Asia/Shanghai")


def _loads(value):
    if not value:
        return None
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return None


def _name_from_raw(raw_state):
    raw = _loads(raw_state)
    return raw.get("name", "") if isinstance(raw, dict) else ""


def _to_china_iso(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(CN_TZ).isoformat(timespec="seconds")


def _china_today() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


async def list_reports(code=None, signal=None, limit=20, depth=None, model_mode=None):
    db = await get_db()
    try:
        rows = await repo.list_reports(db, code=code, signal=signal, limit=max(1, min(limit, 500)), depth=depth, model_mode=model_mode)
        names = await repo.watchlist_name_map(db)
    finally:
        await db.close()

    reports = []
    for row in rows:
        row["name"] = _name_from_raw(row.get("raw_state")) or names.get(row.get("code", ""), "")
        fact_check = _loads(row.get("fact_check"))
        bystander = _loads(row.get("bystander_verify"))
        row["fact_accuracy"] = _fact_accuracy(fact_check)
        row["hallucinations"] = _hallucination_count(fact_check, bystander)
        row["has_snapshot"] = bool(row.get("market_snapshot"))
        row.pop("raw_state", None)
        row.pop("fact_check", None)
        row.pop("bystander_verify", None)
        row.pop("market_snapshot", None)
        reports.append(row)
    return {"count": len(reports), "reports": reports}


async def get_report(report_id: int):
    db = await get_db()
    try:
        report = await repo.get_report(db, report_id)
        if not report:
            raise HTTPException(status_code=404, detail="报告不存在")

        if not report.get("name"):
            report["name"] = _name_from_raw(report.get("raw_state"))
        if not report.get("name"):
            report["name"] = await repo.get_watchlist_name(db, report.get("code", ""))
    finally:
        await db.close()

    raw_state = _loads(report.get("raw_state"))
    if raw_state is not None:
        report["result"] = raw_state

    for key in ("risk_debate", "investment_debate"):
        parsed = _loads(report.get(key))
        if parsed is not None:
            report[key] = parsed

    fact_check = _loads(report.get("fact_check"))
    if fact_check is not None:
        report["_fact_check"] = fact_check

    bystander = _loads(report.get("bystander_verify"))
    if bystander is not None:
        report["_bystander_verify"] = bystander

    return report


def _format_anomaly(row):
    return {
        "code": row["code"],
        "name": row.get("name") or row["code"],
        "anomaly_type": row["anomaly_type"],
        "message": row.get("description") or "",
        "level": row.get("severity") or "info",
        "time": _to_china_iso(row["created_at"]),
        "change_pct": 0,
        "price": 0,
    }


def _memory_anomalies(memory_log, limit: int, code: str | None):
    items = memory_log[-limit:]
    if code:
        code6 = code[:6]
        items = [item for item in items if item.get("code", "").startswith(code6)]
    return {"count": len(items), "anomalies": items}


async def get_anomalies(limit: int, code: str | None, memory_log):
    today = _china_today()
    try:
        db = await get_db()
        try:
            await repo.dedupe_anomalies_for_date(db, today)
            rows = await repo.list_anomalies(db, today=today, limit=limit, code=code)
        finally:
            await db.close()
        anomalies = [_format_anomaly(row) for row in rows]
        if anomalies:
            return {"count": len(anomalies), "anomalies": anomalies}
    except Exception as e:
        logger.warning("get_anomalies DB fallback: %s", e)
    return _memory_anomalies(memory_log, limit, code)


async def clear_anomalies_for_date(day: str | None = None) -> int:
    target_day = day or _china_today()
    db = await get_db()
    try:
        return await repo.delete_anomalies_for_date(db, target_day)
    finally:
        await db.close()


async def clear_stale_anomalies(today: str | None = None) -> int:
    target_day = today or _china_today()
    db = await get_db()
    try:
        stale_count = await repo.delete_anomalies_before_date(db, target_day)
        duplicate_count = await repo.dedupe_anomalies_for_date(db, target_day)
        return stale_count + duplicate_count
    finally:
        await db.close()


def _fact_accuracy(fact_check) -> float | None:
    if not isinstance(fact_check, dict):
        return None
    value = fact_check.get("overall_accuracy", fact_check.get("accuracy"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hallucination_count(fact_check, bystander) -> int:
    total = 0
    if isinstance(fact_check, dict):
        total += int(fact_check.get("total_hallucinations") or fact_check.get("mismatched") or 0)
        for stage in (fact_check.get("stages") or {}).values():
            if isinstance(stage, dict):
                total += int(stage.get("mismatched") or 0)
    if isinstance(bystander, dict):
        total += len(bystander.get("hallucinations") or [])
    return total


async def get_quality_summary(limit: int = 50):
    db = await get_db()
    try:
        rows = await repo.list_quality_reports(db, limit=limit)
        tracking_rows = await repo.signal_tracking_summary(db)
        names = await repo.watchlist_name_map(db)
    finally:
        await db.close()

    reports = []
    accuracies = []
    hallucinations = 0
    verified = 0
    by_model: dict[str, dict] = {}
    for row in rows:
        raw_state = _loads(row.get("raw_state")) or {}
        fact_check = _loads(row.get("fact_check"))
        bystander = _loads(row.get("bystander_verify"))
        accuracy = _fact_accuracy(fact_check)
        if accuracy is not None:
            accuracies.append(accuracy)
            verified += 1
        h_count = _hallucination_count(fact_check, bystander)
        hallucinations += h_count
        model_mode = row.get("model_mode") or "unknown"
        bucket = by_model.setdefault(model_mode, {"model_mode": model_mode, "reports": 0, "verified": 0, "accuracy_sum": 0.0, "hallucinations": 0})
        bucket["reports"] += 1
        bucket["hallucinations"] += h_count
        if accuracy is not None:
            bucket["verified"] += 1
            bucket["accuracy_sum"] += accuracy
        reports.append({
            "id": row["id"],
            "code": row["code"],
            "name": _name_from_raw(row.get("raw_state")) or names.get(row.get("code", ""), ""),
            "signal": row.get("signal") or raw_state.get("signal") or "HOLD",
            "confidence": row.get("confidence") or raw_state.get("confidence"),
            "fact_accuracy": accuracy,
            "hallucinations": h_count,
            "bystander_score": bystander.get("overall_score") if isinstance(bystander, dict) else None,
            "model_mode": model_mode,
            "depth": row.get("depth"),
            "created_at": row.get("created_at"),
        })

    closed = [row for row in tracking_rows if row.get("pnl_pct") is not None]
    avg_pnl = sum(float(row.get("pnl_pct") or 0) for row in closed) / len(closed) if closed else 0
    avg_excess = sum(float(row.get("excess_return") or 0) for row in closed) / len(closed) if closed else 0
    wins = sum(1 for row in closed if float(row.get("pnl_pct") or 0) > 0)
    by_signal: dict[str, dict] = {}
    for row in tracking_rows:
        signal = row.get("signal") or "UNKNOWN"
        item = by_signal.setdefault(signal, {"signal": signal, "tracked": 0, "closed": 0, "wins": 0, "pnl_sum": 0.0, "excess_sum": 0.0})
        item["tracked"] += 1
        if row.get("pnl_pct") is not None:
            item["closed"] += 1
            pnl = float(row.get("pnl_pct") or 0)
            item["pnl_sum"] += pnl
            item["excess_sum"] += float(row.get("excess_return") or 0)
            if pnl > 0:
                item["wins"] += 1
    signal_stats = []
    for item in by_signal.values():
        closed_count = item["closed"]
        signal_stats.append({
            "signal": item["signal"],
            "tracked": item["tracked"],
            "closed": closed_count,
            "win_rate": round(item["wins"] / closed_count * 100, 3) if closed_count else 0,
            "avg_pnl_pct": round(item["pnl_sum"] / closed_count, 3) if closed_count else 0,
            "avg_excess_return": round(item["excess_sum"] / closed_count, 3) if closed_count else 0,
        })
    model_stats = []
    for item in by_model.values():
        verified_count = item["verified"]
        model_stats.append({
            "model_mode": item["model_mode"],
            "reports": item["reports"],
            "verified": verified_count,
            "fact_check_pass_rate": round(item["accuracy_sum"] / verified_count, 3) if verified_count else 0,
            "hallucinations": item["hallucinations"],
        })
    best_model = max(model_stats, key=lambda item: (item["fact_check_pass_rate"], -item["hallucinations"], item["reports"]), default=None)
    return {
        "report_count": len(rows),
        "verified_count": verified,
        "fact_check_pass_rate": round(sum(accuracies) / len(accuracies), 3) if accuracies else 0,
        "hallucination_count": hallucinations,
        "signal_after_return": {
            "tracked": len(tracking_rows),
            "closed": len(closed),
            "win_rate": round(wins / len(closed) * 100, 3) if closed else 0,
            "avg_pnl_pct": round(avg_pnl, 3),
            "avg_excess_return": round(avg_excess, 3),
        },
        "by_signal": sorted(signal_stats, key=lambda item: item["tracked"], reverse=True),
        "by_model_mode": sorted(model_stats, key=lambda item: item["reports"], reverse=True),
        "best_model_mode": best_model,
        "reports": reports,
    }
