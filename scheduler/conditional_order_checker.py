"""条件单自动检查器 — 每30秒检查一次"""
import datetime
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "workbench.db"


def _get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def _expire_old_orders():
    """Set status='expired' for orders past their expires_at timestamp."""
    db = _get_db()
    try:
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor = db.execute(
            "UPDATE conditional_orders SET status = 'expired' "
            "WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at < ?",
            (now,)
        )
        if cursor.rowcount > 0:
            db.commit()
            logger.info("⏰ 自动过期 %d 个条件单", cursor.rowcount)
    except Exception as e:
        logger.error("过期条件单检查失败: %s", e)
    finally:
        db.close()


async def _check_conditional_orders():
    """异步检查条件单"""
    from data.quote import get_batch_quotes

    db = _get_db()
    try:
        # 1. 获取所有活跃条件单
        rows = db.execute(
            "SELECT * FROM conditional_orders WHERE status = 'active'"
        ).fetchall()

        if not rows:
            return []

        orders = [dict(r) for r in rows]
        codes = list(set(o['code'] for o in orders if o.get('code')))
        if not codes:
            return []

        # 2. 批量获取实时行情（异步）
        quotes = await get_batch_quotes(codes)

        # 3. 逐单检查触发条件
        triggered = []
        for order in orders:
            code = order['code']
            q = quotes.get(code)
            if not q:
                continue

            price = q.get('price', 0)
            change_pct = q.get('change_pct', 0)
            ct = order['condition_type']
            tv = order['target_price']
            hit = False

            if ct == 'price_gte' and price >= tv:
                hit = True
            elif ct == 'price_lte' and price <= tv:
                hit = True
            elif ct == 'change_pct_gte' and change_pct >= tv:
                hit = True
            elif ct == 'change_pct_lte' and change_pct <= tv:
                hit = True

            if hit:
                triggered.append(order)

        # 4. 更新被触发的条件单状态
        if triggered:
            ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            for order in triggered:
                db.execute(
                    "UPDATE conditional_orders SET status = 'triggered', triggered_at = ? WHERE id = ?",
                    (ts, order['id'])
                )
                logger.info(
                    "🔔 条件单触发: #%s %s %s %.2f → 已触发",
                    order['id'], order['code'],
                    order['condition_type'], order['target_price']
                )
            db.commit()

        return triggered

    except Exception as e:
        logger.error("检查条件单失败: %s", e)
        return []
    finally:
        db.close()


async def _trigger_l2_for_triggered_orders(triggered: list):
    """Auto-trigger L2 analysis for triggered conditional orders."""
    from api.ai_api import trigger_l2_for_stock
    trade_date = datetime.datetime.now().strftime('%Y-%m-%d')
    for order in triggered:
        task_id = await trigger_l2_for_stock(order['code'], trade_date)
        if task_id:
            logger.info("🔬 条件单触发L2: %s", order['code'])


async def check_conditional_orders():
    """异步入口 — 检查条件单是否触发"""
    try:
        # 1. Expire old orders (fast sync DB call)
        _expire_old_orders()
        # 2. Check triggers (async)
        triggered = await _check_conditional_orders()
        if triggered:
            logger.info("📋 本轮共触发 %d 个条件单", len(triggered))
            # Auto-trigger L2 for triggered orders
            await _trigger_l2_for_triggered_orders(triggered)
    except Exception as e:
        logger.error("条件单检查任务异常: %s", e)
