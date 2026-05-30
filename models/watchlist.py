"""自选股 CRUD"""
import sqlite3
from config import DB_PATH


def _get_sync_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def add_stock(code6, name=''):
    """添加自选股"""
    db = _get_sync_db()
    try:
        max_order = db.execute('SELECT COALESCE(MAX(sort_order), 0) FROM watchlist').fetchone()[0]
        db.execute(
            'INSERT OR IGNORE INTO watchlist (code, name, sort_order) VALUES (?, ?, ?)',
            (code6, name, max_order + 1)
        )
        db.commit()
    finally:
        db.close()


def remove_stock(code6):
    """移除自选股"""
    db = _get_sync_db()
    try:
        db.execute('DELETE FROM watchlist WHERE code = ?', (code6,))
        db.commit()
    finally:
        db.close()


def get_all():
    """返回排序后的自选股列表"""
    db = _get_sync_db()
    try:
        rows = db.execute(
            'SELECT * FROM watchlist ORDER BY sort_order ASC, added_at ASC'
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def reorder(code6_list):
    """按传入顺序重新排列"""
    db = _get_sync_db()
    try:
        for i, code6 in enumerate(code6_list):
            db.execute('UPDATE watchlist SET sort_order = ? WHERE code = ?', (i, code6))
        db.commit()
    finally:
        db.close()


def set_holding(code6, is_holding):
    """设置是否持仓"""
    db = _get_sync_db()
    try:
        db.execute(
            'UPDATE watchlist SET strategy_state = ? WHERE code = ?',
            ('buy' if is_holding else 'watch', code6)
        )
        db.commit()
    finally:
        db.close()


def set_pending(code6, is_pending):
    """设置是否待买入"""
    db = _get_sync_db()
    try:
        db.execute(
            'UPDATE watchlist SET strategy_state = ? WHERE code = ?',
            ('near_buy' if is_pending else 'watch', code6)
        )
        db.commit()
    finally:
        db.close()


def get_stock(code6):
    """获取单只股票信息"""
    db = _get_sync_db()
    try:
        row = db.execute('SELECT * FROM watchlist WHERE code = ?', (code6,)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()
