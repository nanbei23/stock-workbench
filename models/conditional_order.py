"""条件单"""
import datetime
from models.database import get_db

VALID_CONDITIONS = {'price_above', 'price_below', 'change_pct_above', 'change_pct_below'}
VALID_ACTIONS = {'buy', 'sell', 'alert'}


def create_order(code6, condition_type, trigger_value, action='alert',
                 action_price=None, action_shares=None, name='', note='', expires_at=None):
    """创建条件单"""
    if condition_type not in VALID_CONDITIONS:
        raise ValueError(f'Invalid condition_type: {condition_type}')
    if action not in VALID_ACTIONS:
        raise ValueError(f'Invalid action: {action}')

    db = get_db()
    try:
        cur = db.execute(
            '''INSERT INTO conditional_orders
               (code6, name, condition_type, trigger_value, action,
                action_price, action_shares, note, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (code6, name, condition_type, trigger_value, action,
             action_price, action_shares, note, expires_at)
        )
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def check_orders(current_quotes):
    """
    检查哪些条件单被触发。
    current_quotes: dict[code6] -> {price, change_pct, ...}
    返回被触发的条件单列表。
    """
    db = get_db()
    try:
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rows = db.execute(
            "SELECT * FROM conditional_orders WHERE status = 'active' "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (now_str,)
        ).fetchall()

        triggered = []
        for row in rows:
            order = dict(row)
            q = current_quotes.get(order['code6'])
            if q is None:
                continue

            price = q.get('price', 0)
            change_pct = q.get('change_pct', 0)
            ct = order['condition_type']
            tv = order['trigger_value']
            hit = False

            if ct == 'price_above' and price >= tv:
                hit = True
            elif ct == 'price_below' and price <= tv:
                hit = True
            elif ct == 'change_pct_above' and change_pct >= tv:
                hit = True
            elif ct == 'change_pct_below' and change_pct <= tv:
                hit = True

            if hit:
                triggered.append(order)

        return triggered
    finally:
        db.close()


def update_status(order_id, status):
    """更新条件单状态"""
    db = get_db()
    try:
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') if status == 'triggered' else None
        db.execute(
            'UPDATE conditional_orders SET status = ?, triggered_at = ? WHERE id = ?',
            (status, ts, order_id)
        )
        db.commit()
    finally:
        db.close()


def get_active_orders(code6=None):
    """获取活跃条件单"""
    db = get_db()
    try:
        if code6:
            rows = db.execute(
                "SELECT * FROM conditional_orders WHERE status = 'active' AND code6 = ? "
                "ORDER BY created_at DESC",
                (code6,)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM conditional_orders WHERE status = 'active' "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def cancel_order(order_id):
    """取消条件单"""
    update_status(order_id, 'cancelled')
