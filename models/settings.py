"""全局设置"""
from models.database import get_db


def get_setting(key):
    """获取单个设置值"""
    db = get_db()
    try:
        row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else None
    finally:
        db.close()


def set_setting(key, value):
    """设置/更新配置项"""
    db = get_db()
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
    db = get_db()
    try:
        rows = db.execute('SELECT key, value FROM settings').fetchall()
        return {r['key']: r['value'] for r in rows}
    finally:
        db.close()
