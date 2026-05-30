"""全局设置"""
import sqlite3
from config import DB_PATH


def _get_sync_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def get_setting(key):
    """获取单个设置值"""
    db = _get_sync_db()
    try:
        row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else None
    finally:
        db.close()


def set_setting(key, value):
    """设置/更新配置项"""
    db = _get_sync_db()
    try:
        db.execute(
            'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
            (key, str(value))
        )
        db.commit()
    finally:
        db.close()


def get_all_settings():
    """返回所有设置 dict"""
    db = _get_sync_db()
    try:
        rows = db.execute('SELECT key, value FROM settings').fetchall()
        return {r['key']: r['value'] for r in rows}
    finally:
        db.close()
