"""交易账本"""
import sqlite3
from config import DB_PATH


def _get_sync_db():
    """获取同步sqlite3连接（用于线程内调用）"""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def record_trade(code6, trade_type, price, shares, note=''):
    """记录买入/卖出"""
    amount = round(price * shares, 3)
    db = _get_sync_db()
    try:
        db.execute(
            '''
            INSERT INTO trades (code, direction, price, shares, amount, total_cost, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (code6, trade_type, price, shares, amount, amount, note)
        )
        db.commit()
    finally:
        db.close()


def undo_last(code6):
    """撤销最后一笔成交"""
    db = _get_sync_db()
    try:
        row = db.execute(
            'SELECT id FROM trades WHERE code = ? ORDER BY id DESC LIMIT 1', (code6,)
        ).fetchone()
        if row:
            db.execute('DELETE FROM trades WHERE id = ?', (row['id'],))
            db.commit()
            return True
        return False
    finally:
        db.close()


def clear_trades(code6):
    """清空某只股票的所有成交记录"""
    db = _get_sync_db()
    try:
        db.execute('DELETE FROM trades WHERE code = ?', (code6,))
        db.commit()
    finally:
        db.close()


def get_trades(code6):
    """获取成交记录（按时间升序）"""
    db = _get_sync_db()
    try:
        rows = db.execute(
            'SELECT * FROM trades WHERE code = ? ORDER BY trade_time ASC, id ASC',
            (code6,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_position_summary(code6):
    """
    计算持仓摘要：shares, avg_price, cost_basis, min_buy_price, last_buy_price
    用加权平均；卖出按比例扣减成本。
    """
    trades = get_trades(code6)
    total_shares = 0
    total_cost = 0.0
    min_buy_price = None
    last_buy_price = None

    for t in trades:
        direction = t.get('direction', t.get('type', ''))
        if direction == 'buy':
            total_cost += t['price'] * t['shares']
            total_shares += t['shares']
            last_buy_price = t['price']
            if min_buy_price is None or t['price'] < min_buy_price:
                min_buy_price = t['price']
        elif direction == 'sell':
            if total_shares > 0:
                sell_shares = min(t['shares'], total_shares)
                avg_cost = total_cost / total_shares
                total_cost -= avg_cost * sell_shares
                total_shares -= sell_shares
                total_cost = max(total_cost, 0)
                total_shares = max(total_shares, 0)

    avg_price = (total_cost / total_shares) if total_shares > 0 else 0
    return {
        'shares': total_shares,
        'avg_price': round(avg_price, 4),
        'cost_basis': round(total_cost, 3),
        'min_buy_price': min_buy_price,
        'last_buy_price': last_buy_price,
    }


def get_portfolio_overview():
    """
    组合总览：total_value, total_cost, today_pnl, historical_pnl
    需要外部传入实时行情才精确，这里基于 trades 计算静态数据。
    """
    db = _get_sync_db()
    try:
        codes = db.execute('SELECT DISTINCT code FROM trades').fetchall()

        total_value = 0.0
        total_cost = 0.0
        today_pnl = 0.0
        historical_pnl = 0.0

        for row in codes:
            code6 = row['code']
            summary = get_position_summary(code6)
            total_cost += summary['cost_basis']

            # 获取最新快照
            snap = db.execute(
                'SELECT pnl, close_price FROM daily_pnl WHERE code6 = ? ORDER BY date DESC LIMIT 1',
                (code6,)
            ).fetchone()
            if snap and summary['shares'] > 0:
                market_val = snap['close_price'] * summary['shares']
                total_value += market_val
                today_pnl += snap['pnl']

            # 历史已实现盈亏：所有卖出收入 - 对应成本
            sell_rows = db.execute(
                "SELECT price, shares FROM trades WHERE code = ? AND direction = 'sell'",
                (code6,)
            ).fetchall()
            buy_rows = db.execute(
                "SELECT price, shares FROM trades WHERE code = ? AND direction = 'buy'",
                (code6,)
            ).fetchall()
            total_bought_shares = sum(r['shares'] for r in buy_rows)
            total_bought_cost = sum(r['price'] * r['shares'] for r in buy_rows)
            for s in sell_rows:
                if total_bought_shares > 0:
                    avg_b = total_bought_cost / total_bought_shares
                    historical_pnl += (s['price'] - avg_b) * s['shares']

        return {
            'total_value': round(total_value, 3),
            'total_cost': round(total_cost, 3),
            'today_pnl': round(today_pnl, 3),
            'historical_pnl': round(historical_pnl, 3),
        }
    finally:
        db.close()
