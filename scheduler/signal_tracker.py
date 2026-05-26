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
        stop_loss = round(entry_price * 0.9, 2) if entry_price else None
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

    # 计算收益率
    pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price else 0

    db.execute("""
        UPDATE signal_tracking SET
            status='closed', exit_price=?, exit_date=?, exit_reason=?,
            pnl_pct=?, hold_days=?, updated_at=datetime('now')
        WHERE id=?
    """, (exit_price, date.today().isoformat(), exit_reason, pnl_pct, hold_days, tracking_id))


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

            if stop_loss and price <= stop_loss:
                _close_tracking(db, tracking_id, price, "stop_loss")
                closed += 1
            elif target and price >= target and signal in BUY_SIGNALS:
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


def get_tracking_list(status: str = None, signal: str = None, code: str = None) -> List[Dict]:
    """获取跟踪列表"""
    db = _get_db()
    try:
        sql = "SELECT * FROM signal_tracking WHERE 1=1"
        params = []
        if status:
            sql += " AND status=?"
            params.append(status)
        if signal:
            sql += " AND signal=?"
            params.append(signal)
        if code:
            sql += " AND code=?"
            params.append(code)
        sql += " ORDER BY created_at DESC"

        rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_stats() -> Dict:
    """获取绩效统计"""
    db = _get_db()
    try:
        total = db.execute("SELECT COUNT(*) FROM signal_tracking").fetchone()[0]
        open_count = db.execute("SELECT COUNT(*) FROM signal_tracking WHERE status='open'").fetchone()[0]
        closed_rows = db.execute("SELECT * FROM signal_tracking WHERE status='closed'").fetchall()
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

        # 按信号分组
        by_signal = {}
        all_signals = ["STRONG_BUY", "BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL", "STRONG_SELL"]
        for sig in all_signals:
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
            "best_trade": {"code": best["code"], "name": best["name"], "pnl_pct": best["pnl_pct"]} if best else None,
            "worst_trade": {"code": worst["code"], "name": worst["name"], "pnl_pct": worst["pnl_pct"]} if worst else None,
            "by_signal": by_signal,
            "monthly_returns": monthly_returns,
        }
    finally:
        db.close()
