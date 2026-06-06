"""AI report and anomaly read queries."""


async def list_reports(db, code=None, signal=None, limit=20, depth=None, model_mode=None, login_user_id: str = "admin"):
    query = """
        SELECT id, task_id, code, signal, confidence, risk_score,
               duration_seconds, created_at, depth, model_mode, raw_state,
               fact_check, bystander_verify, market_snapshot
        FROM analysis_reports
        WHERE COALESCE(login_user_id, 'admin') = ?
    """
    params = [login_user_id or "admin"]
    if code:
        query += " AND code = ?"
        params.append(code)
    if signal:
        query += " AND signal = ?"
        params.append(signal)
    if depth:
        query += " AND depth = ?"
        params.append(depth)
    if model_mode:
        query += " AND model_mode = ?"
        params.append(model_mode)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = await db.execute_fetchall(query, params)
    return [dict(row) for row in rows]


async def list_quality_reports(db, limit=50):
    rows = await db.execute_fetchall(
        """
        SELECT id, task_id, code, signal, confidence, risk_score, raw_state,
               fact_check, bystander_verify, created_at, depth, model_mode
        FROM analysis_reports
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (max(1, min(limit, 200)),),
    )
    return [dict(row) for row in rows]


async def signal_tracking_summary(db):
    rows = await db.execute_fetchall(
        """
        SELECT signal, status, pnl_pct, excess_return
        FROM signal_tracking
        WHERE pnl_pct IS NOT NULL OR status = 'open'
        """
    )
    return [dict(row) for row in rows]


async def watchlist_name_map(db, login_user_id: str = "admin"):
    try:
        rows = await db.execute_fetchall(
            """
            SELECT code, name
            FROM watchlist
            WHERE COALESCE(login_user_id, 'admin') = ?
            """,
            (login_user_id or "admin",),
        )
    except Exception:
        return {}
    return {row["code"]: row["name"] for row in rows}


async def get_report(db, report_id: int, login_user_id: str | None = None):
    if login_user_id:
        row = await (
            await db.execute(
                """
                SELECT *
                FROM analysis_reports
                WHERE id = ? AND COALESCE(login_user_id, 'admin') = ?
                """,
                (report_id, login_user_id),
            )
        ).fetchone()
        return dict(row) if row else None
    row = await (
        await db.execute("SELECT * FROM analysis_reports WHERE id = ?", (report_id,))
    ).fetchone()
    return dict(row) if row else None


async def get_watchlist_name(db, code: str):
    try:
        row = await (
            await db.execute("SELECT name FROM watchlist WHERE code = ?", (code,))
        ).fetchone()
    except Exception:
        return ""
    return row["name"] if row else ""


async def list_anomalies(db, today: str, limit: int, code: str | None = None):
    if code:
        rows = await db.execute_fetchall(
            """
            WITH latest AS (
                SELECT MAX(id) AS id
                FROM anomaly_logs
                WHERE date(created_at, 'localtime') = ? AND code LIKE ?
                GROUP BY code, anomaly_type
            )
            SELECT a.code, a.name, a.anomaly_type, a.description, a.severity, a.created_at
            FROM anomaly_logs a
            JOIN latest l ON l.id = a.id
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            (today, f"%{code[:6]}%", limit),
        )
    else:
        rows = await db.execute_fetchall(
            """
            WITH latest AS (
                SELECT MAX(id) AS id
                FROM anomaly_logs
                WHERE date(created_at, 'localtime') = ?
                GROUP BY code, anomaly_type
            )
            SELECT a.code, a.name, a.anomaly_type, a.description, a.severity, a.created_at
            FROM anomaly_logs a
            JOIN latest l ON l.id = a.id
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            (today, limit),
        )
    return [dict(row) for row in rows]


async def delete_anomalies_for_date(db, day: str) -> int:
    cursor = await db.execute("DELETE FROM anomaly_logs WHERE date(created_at) = ?", (day,))
    await db.commit()
    return cursor.rowcount


async def delete_anomalies_before_date(db, day: str) -> int:
    cursor = await db.execute("DELETE FROM anomaly_logs WHERE date(created_at, 'localtime') < ?", (day,))
    await db.commit()
    return cursor.rowcount


async def dedupe_anomalies_for_date(db, day: str) -> int:
    cursor = await db.execute(
        """
        DELETE FROM anomaly_logs
        WHERE date(created_at, 'localtime') = ?
          AND id NOT IN (
              SELECT MAX(id)
              FROM anomaly_logs
              WHERE date(created_at, 'localtime') = ?
              GROUP BY code, anomaly_type
          )
        """,
        (day, day),
    )
    await db.commit()
    return cursor.rowcount
