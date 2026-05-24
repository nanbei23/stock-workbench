"""自选股 CRUD"""
from models.database import get_db


def add_stock(code6, name=''):
    """添加自选股"""
    db = get_db()
    try:
        max_order = db.execute('SELECT COALESCE(MAX(sort_order), 0) FROM watchlist').fetchone()[0]
        db.execute(
            'INSERT OR IGNORE INTO watchlist (code6, name, sort_order) VALUES (?, ?, ?)',
            (code6, name, max_order + 1)
        )
        db.commit()
    finally:
        db.close()


def remove_stock(code6):
    """移除自选股"""
    db = get_db()
    try:
        db.execute('DELETE FROM watchlist WHERE code6 = ?', (code6,))
        db.commit()
    finally:
        db.close()


def get_all():
    """返回排序后的自选股列表"""
    db = get_db()
    try:
        rows = db.execute(
            'SELECT * FROM watchlist ORDER BY sort_order ASC, created_at ASC'
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def reorder(code6_list):
    """按传入顺序重新排列"""
    db = get_db()
    try:
        for i, code6 in enumerate(code6_list):
            db.execute('UPDATE watchlist SET sort_order = ? WHERE code6 = ?', (i, code6))
        db.commit()
    finally:
        db.close()


def set_holding(code6, is_holding):
    """设置是否持仓"""
    db = get_db()
    try:
        db.execute(
            'UPDATE watchlist SET is_holding = ? WHERE code6 = ?',
            (1 if is_holding else 0, code6)
        )
        db.commit()
    finally:
        db.close()


def set_pending(code6, is_pending):
    """设置是否待买入"""
    db = get_db()
    try:
        db.execute(
            'UPDATE watchlist SET is_pending = ? WHERE code6 = ?',
            (1 if is_pending else 0, code6)
        )
        db.commit()
    finally:
        db.close()


def get_stock(code6):
    """获取单只股票信息"""
    db = get_db()
    try:
        row = db.execute('SELECT * FROM watchlist WHERE code6 = ?', (code6,)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()
