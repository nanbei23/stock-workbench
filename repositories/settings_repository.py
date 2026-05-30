"""Settings and backup database access helpers."""

import sqlite3

from config import DB_PATH


def open_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_settings_table():
    conn = open_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def fetch_settings() -> dict:
    conn = open_connection()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        conn.close()


def fetch_setting(key: str):
    conn = open_connection()
    try:
        row = conn.execute("SELECT key, value FROM settings WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fetch_settings_like(pattern: str) -> dict:
    conn = open_connection()
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (pattern,),
        ).fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        conn.close()


def upsert_settings(settings: dict):
    conn = open_connection()
    try:
        for key, value in settings.items():
            conn.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, str(value)),
            )
        conn.commit()
    finally:
        conn.close()


def reset_settings(defaults: dict):
    conn = open_connection()
    try:
        conn.execute("DELETE FROM settings")
        for key, value in defaults.items():
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def export_data() -> dict:
    conn = open_connection()
    try:
        result = {}
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        result["settings"] = {row["key"]: row["value"] for row in rows}
        for key, query in {
            "watchlist": "SELECT * FROM watchlist ORDER BY sort_order ASC",
            "portfolio": "SELECT * FROM portfolio ORDER BY code",
            "orders": "SELECT * FROM conditional_orders ORDER BY id",
            "reports": "SELECT * FROM analysis_reports ORDER BY created_at DESC LIMIT 100",
            "daily_pnl": "SELECT * FROM daily_pnl ORDER BY date DESC",
        }.items():
            rows = conn.execute(query).fetchall()
            result[key] = [dict(row) for row in rows]
        return result
    finally:
        conn.close()


def import_data(data) -> dict:
    conn = open_connection()
    imported = {"watchlist": 0, "portfolio": 0, "orders": 0, "settings": 0}
    try:
        if data.settings:
            for key, value in data.settings.items():
                conn.execute(
                    """
                    INSERT INTO settings (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (key, str(value)),
                )
            imported["settings"] = len(data.settings)

        if data.watchlist:
            for item in data.watchlist:
                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO watchlist
                            (code, name, group_name, strategy_state)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            item.get("code", ""),
                            item.get("name", ""),
                            item.get("group_name", "默认"),
                            item.get("strategy_state", "watch"),
                        ),
                    )
                    imported["watchlist"] += 1
                except Exception:
                    pass

        if data.portfolio:
            for item in data.portfolio:
                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO portfolio
                            (code, name, total_shares, available_shares, avg_cost)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            item.get("code", ""),
                            item.get("name", ""),
                            item.get("total_shares", 0),
                            item.get("available_shares", 0),
                            item.get("avg_cost", 0),
                        ),
                    )
                    imported["portfolio"] += 1
                except Exception:
                    pass

        if data.orders:
            for item in data.orders:
                try:
                    conn.execute(
                        """
                        INSERT INTO conditional_orders
                            (code, name, condition_type, target_price, action, shares, status, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item.get("code", ""),
                            item.get("name", ""),
                            item.get("condition_type", "price_lte"),
                            item.get("target_price", 0),
                            item.get("action", "buy"),
                            item.get("shares", 0),
                            item.get("status", "active"),
                            item.get("notes", ""),
                        ),
                    )
                    imported["orders"] += 1
                except Exception:
                    pass

        conn.commit()
        return imported
    finally:
        conn.close()


def clear_data(tables: list[str]) -> dict:
    conn = open_connection()
    counts = {}
    try:
        for table in tables:
            try:
                row = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()
                counts[table] = row["c"]
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                counts[table] = "N/A"
        conn.commit()
        return counts
    finally:
        conn.close()


def fetch_recent_notifications() -> list[dict]:
    conn = open_connection()
    notifications = []
    try:
        rows = conn.execute(
            """
            SELECT code, name, condition_type, target_price, action, shares, triggered_at
            FROM conditional_orders
            WHERE status = 'triggered'
              AND triggered_at > datetime('now', '-5 minutes')
            ORDER BY triggered_at DESC
            """
        ).fetchall()
        for row in rows:
            data = dict(row)
            action_str = "买入" if data.get("action") == "buy" else "卖出"
            notifications.append({
                "type": "order_trigger",
                "title": f"条件单触发: {data.get('name', '') or data['code']}",
                "body": f"{action_str} {data.get('shares', 0)}股 @ {data['target_price']}",
                "time": data.get("triggered_at", ""),
                "data": data,
            })

        rows = conn.execute(
            """
            SELECT id, code, signal, confidence, created_at
            FROM analysis_reports
            WHERE created_at > datetime('now', '-2 minutes')
            ORDER BY created_at DESC
            LIMIT 3
            """
        ).fetchall()
        for row in rows:
            data = dict(row)
            notifications.append({
                "type": "analysis_done",
                "title": f"AI分析完成: {data['code']}",
                "body": f"信号: {data['signal']} | 置信度: {data.get('confidence', '—')}%",
                "time": data.get("created_at", ""),
                "data": data,
            })

        rows = conn.execute(
            """
            SELECT code, name, strategy_state, strategy_state_updated_at
            FROM watchlist
            WHERE strategy_state_updated_at IS NOT NULL
              AND strategy_state_updated_at > datetime('now', '-5 minutes')
            ORDER BY strategy_state_updated_at DESC
            """
        ).fetchall()
        state_labels = {
            "watch": "观察",
            "buy_zone": "买入区",
            "sell_zone": "卖出区",
            "hold": "持有",
            "stop_loss": "止损",
            "take_profit": "止盈",
        }
        for row in rows:
            data = dict(row)
            state_label = state_labels.get(data.get("strategy_state", ""), data.get("strategy_state", ""))
            notifications.append({
                "type": "strategy_change",
                "title": f"策略状态变化: {data.get('name', '') or data['code']}",
                "body": f"状态变更为: {state_label}",
                "time": data.get("strategy_state_updated_at", ""),
                "data": data,
            })

        rows = conn.execute(
            """
            SELECT id, code, name, anomaly_type, description, severity, created_at
            FROM anomaly_logs
            WHERE created_at > datetime('now', '-5 minutes')
            ORDER BY created_at DESC
            LIMIT 5
            """
        ).fetchall()
        for row in rows:
            data = dict(row)
            severity_label = (
                "严重" if data.get("severity") == "critical"
                else "预警" if data.get("severity") == "warning"
                else "关注"
            )
            notifications.append({
                "type": "anomaly",
                "title": f"{severity_label}异动告警: {data.get('name', '') or data['code']}",
                "body": f"{data.get('anomaly_type', '')} — {data.get('description', '')[:80]}",
                "time": data.get("created_at", ""),
                "data": data,
            })

        notifications.sort(key=lambda item: item.get("time", ""), reverse=True)
        return notifications
    finally:
        conn.close()
