"""信号跟踪与绩效验证 — 前向验证AI信号有效性"""
import sqlite3
import logging
from datetime import datetime, date
from typing import Optional, Dict, List
from config import DB_PATH

logger = logging.getLogger(__name__)

# 信号方向分组
BUY_SIGNALS = {"STRONG_BUY", "BUY", "OVERWEIGHT"}
SELL_SIGNALS = {"STRONG_SELL", "SELL", "UNDERWEIGHT"}
NEUTRAL_SIGNALS = {"HOLD"}
ALL_SIGNALS = ["STRONG_BUY", "BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL", "STRONG_SELL"]


def _get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def create_tracking(report_id: int, code: str, name: str, signal: str,
                    entry_price: float, target_price: float = None) -> Optional[int]:
    """报告生成时自动创建跟踪记录"""
    if not signal or signal not in (BUY_SIGNALS | SELL_SIGNALS | NEUTRAL_SIGNALS):
        logger.info("信号 %s 不跟踪", signal)
        return None

    db = _get_db()
    try:
        # 检查是否已有同一股票的 open 记录，如有则先关闭（信号更新）
        existing = db.execute(
            "SELECT id, signal FROM signal_tracking WHERE code=? AND status='open'",
            (code,)
        ).fetchone()

        if existing:
            old_signal = existing["signal"]
            # 信号方向反转时关闭旧记录
            if _is_signal_reversal(old_signal, signal):
                _close_tracking(db, existing["id"], entry_price, "signal_change")
                logger.info("信号反转 %s %s→%s，关闭旧跟踪 #%d", code, old_signal, signal, existing["id"])
            else:
                # 同方向更新，关闭旧记录但不计为反转
                _close_tracking(db, existing["id"], entry_price, "signal_update")
                logger.info("信号更新 %s %s→%s，关闭旧跟踪 #%d", code, old_signal, signal, existing["id"])

        # 创建新跟踪
        direction = _signal_direction(signal)
        stop_loss = _default_stop_loss(entry_price, direction)
        today = date.today().isoformat()

        cursor = db.execute("""
            INSERT INTO signal_tracking
            (report_id, code, name, signal, signal_date, entry_price,
             target_price, stop_loss_price, current_price, highest_price, lowest_price, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """, (report_id, code, name, signal, today, entry_price,
              target_price, stop_loss, entry_price, entry_price, entry_price))

        db.commit()
        tracking_id = cursor.lastrowid
        logger.info("创建信号跟踪 #%d: %s %s @ ¥%.2f", tracking_id, name, signal, entry_price)
        return tracking_id
    except Exception as e:
        logger.error("创建信号跟踪失败: %s", e)
        return None
    finally:
        db.close()


def _is_signal_reversal(old_signal: str, new_signal: str) -> bool:
    """判断信号是否方向反转"""
    old_dir = _signal_direction(old_signal)
    new_dir = _signal_direction(new_signal)
    if old_dir == "neutral" or new_dir == "neutral":
        return False
    return old_dir != new_dir


def _signal_direction(signal: str) -> str:
    if signal in BUY_SIGNALS:
        return "buy"
    elif signal in SELL_SIGNALS:
        return "sell"
    return "neutral"


def _default_stop_loss(entry_price: float, direction: str):
    if not entry_price:
        return None
    if direction == "sell":
        return round(entry_price * 1.1, 2)
    return round(entry_price * 0.9, 2)


def _directional_pnl_pct(signal: str, entry_price: float, exit_price: float) -> float:
    if not entry_price:
        return 0.0
    raw = (exit_price - entry_price) / entry_price * 100
    return round(-raw if _signal_direction(signal) == "sell" else raw, 2)


def _tracking_where(status=None, signal=None, code=None, window: str = "all", model_mode=None, depth=None):
    where = ["1=1"]
    params = []
    if status:
        where.append("st.status=?")
        params.append(status)
    if signal:
        where.append("st.signal=?")
        params.append(signal)
    if code:
        where.append("st.code=?")
        params.append(code)
    if window and window != "all":
        try:
            days = max(1, min(int(window), 3650))
            where.append("date(st.signal_date) >= date('now', ?)")
            params.append(f"-{days} day")
        except (TypeError, ValueError):
            pass
    if model_mode:
        where.append("COALESCE(ar.model_mode, 'manual')=?")
        params.append(model_mode)
    if depth:
        where.append("COALESCE(ar.depth, 'manual')=?")
        params.append(depth)
    return " AND ".join(where), params


def _close_tracking(db, tracking_id: int, exit_price: float, exit_reason: str):
    """关闭跟踪记录"""
    row = db.execute("SELECT * FROM signal_tracking WHERE id=?", (tracking_id,)).fetchone()
    if not row:
        return

    entry_price = row["entry_price"]
    signal_date = row["signal_date"]

    # 计算持有天数
    try:
        d0 = datetime.strptime(signal_date, "%Y-%m-%d").date()
        hold_days = (date.today() - d0).days
    except Exception:
        hold_days = 0

    pnl_pct = _directional_pnl_pct(row["signal"], entry_price, exit_price)
    benchmark_return = row["benchmark_return"] if row["benchmark_return"] is not None else 0
    excess_return = round(pnl_pct - benchmark_return, 2)

    db.execute("""
        UPDATE signal_tracking SET
            status='closed', exit_price=?, exit_date=?, exit_reason=?,
            pnl_pct=?, hold_days=?, benchmark_return=?, excess_return=?, updated_at=datetime('now')
        WHERE id=?
    """, (
        exit_price,
        date.today().isoformat(),
        exit_reason,
        pnl_pct,
        hold_days,
        benchmark_return,
        excess_return,
        tracking_id,
    ))


def update_prices(price_map: Dict[str, float]):
    """每日更新所有 open 记录的价格"""
    db = _get_db()
    try:
        rows = db.execute("SELECT id, code, entry_price FROM signal_tracking WHERE status='open'").fetchall()
        updated = 0
        closed = 0

        for row in rows:
            code = row["code"]
            price = price_map.get(code)
            if price is None:
                continue

            tracking_id = row["id"]
            entry_price = row["entry_price"]

            # 获取当前记录的最高/最低价
            current = db.execute(
                "SELECT highest_price, lowest_price, stop_loss_price, target_price, signal FROM signal_tracking WHERE id=?",
                (tracking_id,)
            ).fetchone()

            highest = max(current["highest_price"] or price, price)
            lowest = min(current["lowest_price"] or price, price)

            db.execute("""
                UPDATE signal_tracking SET
                    current_price=?, highest_price=?, lowest_price=?, updated_at=datetime('now')
                WHERE id=?
            """, (price, highest, lowest, tracking_id))
            updated += 1

            # 检查出场条件
            signal = current["signal"]
            stop_loss = current["stop_loss_price"]
            target = current["target_price"]

            direction = _signal_direction(signal)
            if direction == "sell" and stop_loss and price >= stop_loss:
                _close_tracking(db, tracking_id, price, "stop_loss")
                closed += 1
            elif direction != "sell" and stop_loss and price <= stop_loss:
                _close_tracking(db, tracking_id, price, "stop_loss")
                closed += 1
            elif direction == "sell" and target and price <= target:
                _close_tracking(db, tracking_id, price, "target_hit")
                closed += 1
            elif direction != "sell" and target and price >= target and signal in BUY_SIGNALS:
                _close_tracking(db, tracking_id, price, "target_hit")
                closed += 1

        db.commit()
        logger.info("信号跟踪价格更新: %d 更新, %d 出场", updated, closed)
        return {"updated": updated, "closed": closed}
    except Exception as e:
        logger.error("更新信号跟踪价格失败: %s", e)
        return {"updated": 0, "closed": 0, "error": str(e)}
    finally:
        db.close()


def get_open_tracking_codes() -> List[str]:
    """Return distinct stock codes that currently have open signal tracking."""
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT DISTINCT code FROM signal_tracking WHERE status='open' ORDER BY code"
        ).fetchall()
        return [row["code"] for row in rows]
    finally:
        db.close()


def close_tracking_manual(tracking_id: int, exit_price: float) -> bool:
    """手动平仓"""
    db = _get_db()
    try:
        row = db.execute("SELECT status FROM signal_tracking WHERE id=?", (tracking_id,)).fetchone()
        if not row or row["status"] != "open":
            return False
        _close_tracking(db, tracking_id, exit_price, "manual")
        db.commit()
        return True
    except Exception as e:
        logger.error("手动平仓失败: %s", e)
        return False
    finally:
        db.close()


def get_tracking_list(status: str = None, signal: str = None, code: str = None,
                      window: str = "all", model_mode: str = None, depth: str = None) -> List[Dict]:
    """获取跟踪列表"""
    db = _get_db()
    try:
        where, params = _tracking_where(status, signal, code, window, model_mode, depth)
        sql = f"""
            SELECT st.*, COALESCE(ar.model_mode, 'manual') AS model_mode, COALESCE(ar.depth, 'manual') AS depth
            FROM signal_tracking st
            LEFT JOIN analysis_reports ar ON ar.id = st.report_id
            WHERE {where}
            ORDER BY st.created_at DESC
        """

        rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_stats(window: str = "all", model_mode: str = None, depth: str = None) -> Dict:
    """获取绩效统计"""
    db = _get_db()
    try:
        base_where, base_params = _tracking_where(window=window, model_mode=model_mode, depth=depth)
        total = db.execute(
            f"""
            SELECT COUNT(*)
            FROM signal_tracking st
            LEFT JOIN analysis_reports ar ON ar.id = st.report_id
            WHERE {base_where}
            """,
            base_params,
        ).fetchone()[0]
        open_where, open_params = _tracking_where(status="open", window=window, model_mode=model_mode, depth=depth)
        open_count = db.execute(
            f"""
            SELECT COUNT(*)
            FROM signal_tracking st
            LEFT JOIN analysis_reports ar ON ar.id = st.report_id
            WHERE {open_where}
            """,
            open_params,
        ).fetchone()[0]
        closed_where, closed_params = _tracking_where(status="closed", window=window, model_mode=model_mode, depth=depth)
        closed_rows = db.execute(
            f"""
            SELECT st.*, COALESCE(ar.model_mode, 'manual') AS model_mode, COALESCE(ar.depth, 'manual') AS depth
            FROM signal_tracking st
            LEFT JOIN analysis_reports ar ON ar.id = st.report_id
            WHERE {closed_where}
            """,
            closed_params,
        ).fetchall()
        closed = [dict(r) for r in closed_rows]

        # 总体统计
        win_count = sum(1 for r in closed if (r["pnl_pct"] or 0) > 0)
        win_rate = round(win_count / len(closed), 4) if closed else 0
        avg_pnl = round(sum(r["pnl_pct"] or 0 for r in closed) / len(closed), 2) if closed else 0
        avg_hold = round(sum(r["hold_days"] or 0 for r in closed) / len(closed), 1) if closed else 0
        avg_excess = round(sum(r["excess_return"] or 0 for r in closed) / len(closed), 2) if closed else 0

        # 最佳/最差
        best = max(closed, key=lambda r: r["pnl_pct"] or 0) if closed else None
        worst = min(closed, key=lambda r: r["pnl_pct"] or 0) if closed else None

        by_signal = {}
        for sig in ALL_SIGNALS:
            sig_rows = [r for r in closed if r["signal"] == sig]
            if sig_rows:
                sig_win = sum(1 for r in sig_rows if (r["pnl_pct"] or 0) > 0)
                by_signal[sig] = {
                    "count": len(sig_rows),
                    "win_rate": round(sig_win / len(sig_rows), 4),
                    "avg_pnl": round(sum(r["pnl_pct"] or 0 for r in sig_rows) / len(sig_rows), 2),
                }
            else:
                by_signal[sig] = {"count": 0, "win_rate": 0, "avg_pnl": 0}

        by_model = {}
        by_depth = {}
        for r in closed:
            for key, target in (("model_mode", by_model), ("depth", by_depth)):
                label = r.get(key) or "manual"
                bucket = target.setdefault(label, {"label": label, "count": 0, "wins": 0, "pnl_sum": 0.0, "excess_sum": 0.0})
                pnl = r["pnl_pct"] or 0
                bucket["count"] += 1
                bucket["wins"] += 1 if pnl > 0 else 0
                bucket["pnl_sum"] += pnl
                bucket["excess_sum"] += r["excess_return"] or 0

        def finalize_bucket(bucket):
            count = bucket["count"]
            return {
                "label": bucket["label"],
                "count": count,
                "win_rate": round(bucket["wins"] / count, 4) if count else 0,
                "avg_pnl": round(bucket["pnl_sum"] / count, 2) if count else 0,
                "avg_excess_return": round(bucket["excess_sum"] / count, 2) if count else 0,
            }

        # 月度收益
        monthly = {}
        for r in closed:
            if r["exit_date"]:
                month = r["exit_date"][:7]  # YYYY-MM
                if month not in monthly:
                    monthly[month] = {"return_pct": 0, "count": 0}
                monthly[month]["return_pct"] += r["pnl_pct"] or 0
                monthly[month]["count"] += 1
        monthly_returns = [{"month": k, "return_pct": round(v["return_pct"], 2), "count": v["count"]}
                          for k, v in sorted(monthly.items())]

        return {
            "total": total,
            "open": open_count,
            "closed": len(closed),
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl,
            "avg_hold_days": avg_hold,
            "avg_excess_return": avg_excess,
            "benchmark_coverage": sum(1 for r in closed if r.get("benchmark_return") is not None),
            "best_trade": {"code": best["code"], "name": best["name"], "pnl_pct": best["pnl_pct"]} if best else None,
            "worst_trade": {"code": worst["code"], "name": worst["name"], "pnl_pct": worst["pnl_pct"]} if worst else None,
            "by_signal": by_signal,
            "by_model_mode": sorted([finalize_bucket(v) for v in by_model.values()], key=lambda item: item["count"], reverse=True),
            "by_depth": sorted([finalize_bucket(v) for v in by_depth.values()], key=lambda item: item["count"], reverse=True),
            "monthly_returns": monthly_returns,
            "filters": {"window": window, "model_mode": model_mode, "depth": depth},
        }
    finally:
        db.close()
