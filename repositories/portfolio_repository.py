"""Portfolio database access helpers."""


async def fetch_accounts(db, login_user_id: str | None = None):
    if login_user_id:
        rows = await db.execute_fetchall(
            """
            SELECT id, name, broker, account_no_mask, is_default, status, notes,
                   display_order, created_at, updated_at, login_user_id
            FROM securities_accounts
            WHERE login_user_id = ? AND status = 'active'
            ORDER BY is_default DESC, display_order ASC, created_at ASC
            """,
            (login_user_id,),
        )
    else:
        rows = await db.execute_fetchall(
            """
            SELECT id, name, broker, account_no_mask, is_default, status, notes,
                   display_order, created_at, updated_at, login_user_id
            FROM securities_accounts
            WHERE status = 'active'
            ORDER BY login_user_id ASC, is_default DESC, display_order ASC, created_at ASC
            """
        )
    return [dict(row) for row in rows]


async def create_account(db, account_id: str, name: str, broker: str, login_user_id: str = "admin"):
    await db.execute(
        """
        INSERT INTO securities_accounts (id, login_user_id, name, broker)
        VALUES (?, ?, ?, ?)
        """,
        (account_id, login_user_id or "admin", name, broker),
    )
    await db.execute(
        "INSERT OR IGNORE INTO accounts (id, name, broker) VALUES (?, ?, ?)",
        (account_id, name, broker),
    )
    await db.commit()


async def fetch_watchlist_and_positions(db, login_user_id: str = "admin"):
    rows = await db.execute_fetchall(
        """
        SELECT *
        FROM watchlist
        WHERE COALESCE(login_user_id, 'admin') = ?
        ORDER BY sort_order ASC, added_at ASC
        """,
        (login_user_id or "admin",),
    )
    stocks = [dict(row) for row in rows]
    portfolio_rows = await db.execute_fetchall(
        """
        SELECT
            code,
            SUM(total_shares) AS total_shares,
            CASE
                WHEN SUM(total_shares) > 0
                THEN SUM(avg_cost * total_shares) / SUM(total_shares)
                ELSE 0
            END AS avg_cost
        FROM portfolio
        WHERE account_id IN (
            SELECT id FROM securities_accounts
            WHERE login_user_id = ? AND status = 'active'
        )
        GROUP BY code
        """,
        (login_user_id or "admin",),
    )
    portfolio_map = {row["code"]: dict(row) for row in portfolio_rows}
    return stocks, portfolio_map


async def fetch_latest_report_map(db, codes: list[str], login_user_id: str = "admin"):
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    rows = await db.execute_fetchall(
        f"""
        SELECT id, code, signal, confidence, risk_score, created_at
        FROM analysis_reports
        WHERE COALESCE(login_user_id, 'admin') = ?
          AND code IN ({placeholders})
        ORDER BY code ASC, datetime(created_at) DESC, id DESC
        """,
        [login_user_id or "admin", *codes],
    )
    latest = {}
    for row in rows:
        code = row["code"]
        if code in latest:
            continue
        latest[code] = dict(row)
    return latest


async def next_watchlist_sort_order(db, login_user_id: str = "admin"):
    row = await (
        await db.execute(
            """
            SELECT COALESCE(MAX(sort_order), 0)
            FROM watchlist
            WHERE COALESCE(login_user_id, 'admin') = ?
            """,
            (login_user_id or "admin",),
        )
    ).fetchone()
    return (row[0] if row else 0) + 1


async def fetch_watchlist_codes(db, codes: list[str], login_user_id: str = "admin"):
    if not codes:
        return set()
    placeholders = ",".join("?" for _ in codes)
    rows = await db.execute_fetchall(
        f"""
        SELECT code
        FROM watchlist
        WHERE COALESCE(login_user_id, 'admin') = ?
          AND code IN ({placeholders})
        """,
        [login_user_id or "admin", *codes],
    )
    return {row["code"] for row in rows}


async def insert_watchlist_stock(db, req, sort_order: int, login_user_id: str = "admin"):
    await db.execute(
        """
        INSERT OR IGNORE INTO watchlist (
            code, name, group_name, sort_order, strategy_state,
            target_buy_price, target_sell_price, stop_loss_price, notes, login_user_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            req.code,
            req.name,
            req.group_name,
            sort_order,
            req.strategy_state,
            req.target_buy_price,
            req.target_sell_price,
            req.stop_loss_price,
            req.notes,
            login_user_id or "admin",
        ),
    )
    await db.commit()
    row = await (
        await db.execute(
            """
            SELECT *
            FROM watchlist
            WHERE code = ? AND COALESCE(login_user_id, 'admin') = ?
            """,
            (req.code, login_user_id or "admin"),
        )
    ).fetchone()
    return dict(row) if row else {"code": req.code, "name": req.name}


async def delete_watchlist_stock(db, code: str, login_user_id: str = "admin"):
    cursor = await db.execute(
        "DELETE FROM watchlist WHERE code = ? AND COALESCE(login_user_id, 'admin') = ?",
        (code, login_user_id or "admin"),
    )
    await db.commit()
    return cursor.rowcount


async def delete_watchlist_stocks(db, codes: list[str], login_user_id: str = "admin"):
    if not codes:
        return 0
    placeholders = ",".join("?" for _ in codes)
    cursor = await db.execute(
        f"""
        DELETE FROM watchlist
        WHERE COALESCE(login_user_id, 'admin') = ?
          AND code IN ({placeholders})
        """,
        [login_user_id or "admin", *codes],
    )
    await db.commit()
    return cursor.rowcount


async def update_watchlist_stock(db, code: str, updates: dict, login_user_id: str = "admin"):
    if not updates:
        return None
    columns = [f"{key} = ?" for key in updates]
    params = list(updates.values()) + [code, login_user_id or "admin"]
    cursor = await db.execute(
        f"""
        UPDATE watchlist
        SET {', '.join(columns)}
        WHERE code = ? AND COALESCE(login_user_id, 'admin') = ?
        """,
        params,
    )
    await db.commit()
    return cursor.rowcount


async def reorder_watchlist(db, items, login_user_id: str = "admin"):
    for item in items:
        await db.execute(
            """
            UPDATE watchlist
            SET sort_order = ?
            WHERE code = ? AND COALESCE(login_user_id, 'admin') = ?
            """,
            (item.sort_order, item.code, login_user_id or "admin"),
        )
    await db.commit()


async def update_account(db, account_id: str, login_user_id: str, values: dict):
    allowed = {
        key: values[key]
        for key in ("name", "broker", "account_no_mask", "notes", "display_order")
        if key in values
    }
    if not allowed:
        return 0
    assignments = [f"{key} = ?" for key in allowed]
    params = list(allowed.values()) + [account_id, login_user_id or "admin"]
    cursor = await db.execute(
        f"""
        UPDATE securities_accounts
        SET {', '.join(assignments)}, updated_at = datetime('now')
        WHERE id = ? AND login_user_id = ?
        """,
        params,
    )
    if "name" in allowed or "broker" in allowed:
        await db.execute(
            """
            UPDATE accounts
            SET name = COALESCE(?, name), broker = COALESCE(?, broker)
            WHERE id = ?
            """,
            (allowed.get("name"), allowed.get("broker"), account_id),
        )
    await db.commit()
    return cursor.rowcount


async def archive_account(db, account_id: str, login_user_id: str):
    cursor = await db.execute(
        """
        UPDATE securities_accounts
        SET status = 'archived', updated_at = datetime('now')
        WHERE id = ? AND login_user_id = ? AND is_default = 0
        """,
        (account_id, login_user_id or "admin"),
    )
    await db.commit()
    return cursor.rowcount


async def fetch_trades(db, code=None, account_id=None):
    conditions = []
    params = []
    if code:
        conditions.append("code = ?")
        params.append(code)
    if account_id:
        conditions.append("account_id = ?")
        params.append(account_id)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = await db.execute_fetchall(f"SELECT * FROM trades{where} ORDER BY trade_time DESC", params)
    return [dict(row) for row in rows]


async def insert_trade(db, req):
    account_id = getattr(req, "account_id", None) or "default"
    amount = round(req.price * req.shares, 3)
    total_cost = round(amount + req.commission + req.stamp_tax + req.transfer_fee, 3)
    trade_time = req.trade_time if req.trade_time else None
    cursor = await db.execute(
        """
        INSERT INTO trades (
            code, name, direction, price, shares, amount,
            commission, stamp_tax, transfer_fee, total_cost, trade_time, notes, account_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), ?, ?)
        """,
        (
            req.code,
            req.name,
            req.direction,
            req.price,
            req.shares,
            amount,
            req.commission,
            req.stamp_tax,
            req.transfer_fee,
            total_cost,
            trade_time,
            req.notes,
            account_id,
        ),
    )
    await db.commit()
    return await fetch_trade(db, cursor.lastrowid)


async def fetch_trade(db, trade_id: int):
    row = await (await db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))).fetchone()
    return dict(row) if row else None


def trade_cash_delta(trade: dict) -> float:
    direction = str(trade.get("direction") or "").lower()
    amount = float(trade.get("amount") or 0)
    fees = (
        float(trade.get("commission") or 0)
        + float(trade.get("stamp_tax") or 0)
        + float(trade.get("transfer_fee") or 0)
    )
    if direction == "buy":
        return round(-(amount + fees), 3)
    if direction == "sell":
        return round(amount - fees, 3)
    return 0.0


async def apply_trade_cash_effect(db, trade: dict, *, reverse: bool = False):
    aid = trade.get("account_id") or "default"
    delta = trade_cash_delta(trade)
    if reverse:
        delta = round(-delta, 3)
    current = await fetch_cash_and_fees(db, aid)
    new_cash = round(float(current["cash"]) + delta, 3)
    key = f"cash_balance_{aid}"
    direction = "trade_reversal" if reverse else f"trade_{str(trade.get('direction') or 'adjust').lower()}"
    notes = (
        f"{'冲销' if reverse else '交易'}现金变动："
        f"{trade.get('name') or trade.get('code') or ''} {trade.get('code') or ''} "
        f"#{trade.get('id') or ''}"
    ).strip()
    await db.execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(new_cash)),
    )
    await db.execute(
        """
        INSERT INTO cash_ledger (account_id, direction, amount, balance_after, source, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (aid, direction, delta, new_cash, "trade", notes),
    )
    await db.commit()
    return {"account_id": aid, "cash": new_cash, "amount": delta, "direction": direction}


async def fetch_trade_stats(db, code: str, account_id: str | None = "default"):
    lowest_row = await (
        await db.execute(
            """
            SELECT MIN(price) as lowest_buy_price
            FROM trades
            WHERE code = ? AND direction = 'buy' AND (? IS NULL OR account_id = ?)
            """,
            (code, account_id, account_id),
        )
    ).fetchone()
    latest_row = await (
        await db.execute(
            """
            SELECT price
            FROM trades
            WHERE code = ? AND direction = 'buy' AND (? IS NULL OR account_id = ?)
            ORDER BY trade_time DESC LIMIT 1
            """,
            (code, account_id, account_id),
        )
    ).fetchone()
    return {
        "lowest_buy_price": dict(lowest_row)["lowest_buy_price"] if lowest_row else None,
        "latest_buy_price": dict(latest_row)["price"] if latest_row else None,
    }


async def delete_trade(db, trade_id: int):
    cursor = await db.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    await db.commit()
    return cursor.rowcount


async def count_stock_trades(db, code: str, account_id: str | None = None):
    row = await (
        await db.execute(
            "SELECT COUNT(*) as cnt FROM trades WHERE code = ? AND (? IS NULL OR account_id = ?)",
            (code, account_id, account_id),
        )
    ).fetchone()
    return dict(row)["cnt"] if row else 0


async def delete_stock_trades(db, code: str, account_id: str | None = None):
    await db.execute(
        "DELETE FROM trades WHERE code = ? AND (? IS NULL OR account_id = ?)",
        (code, account_id, account_id),
    )
    await db.commit()


async def update_trade(db, trade_id: int, values: dict):
    await db.execute(
        """
        UPDATE trades
        SET direction=?, price=?, shares=?, amount=?, commission=?,
            stamp_tax=?, transfer_fee=?, total_cost=?, notes=?
        WHERE id=?
        """,
        (
            values["direction"],
            values["price"],
            values["shares"],
            values["amount"],
            values["commission"],
            values["stamp_tax"],
            values["transfer_fee"],
            values["total_cost"],
            values["notes"],
            trade_id,
        ),
    )
    await db.commit()


async def recalc_portfolio(db, code: str, account_id: str | None = None):
    aid = account_id or "default"
    rows = await db.execute_fetchall(
        """
        SELECT direction, price, shares, amount, commission, stamp_tax, transfer_fee
        FROM trades
        WHERE code = ? AND account_id = ?
        ORDER BY trade_time ASC
        """,
        (code, aid),
    )
    total_shares = 0
    total_cost = 0.0
    for row in rows:
        trade = dict(row)
        if trade["direction"] == "buy":
            total_shares += trade["shares"]
            total_cost += (
                trade["amount"]
                + trade["commission"]
                + trade["stamp_tax"]
                + trade["transfer_fee"]
            )
        elif trade["direction"] == "sell" and total_shares > 0:
            avg_before = total_cost / total_shares
            total_shares = max(0, total_shares - trade["shares"])
            total_cost = avg_before * total_shares

    total_shares = round(total_shares, 3)
    avg_cost = round(total_cost / total_shares, 3) if total_shares > 0 else 0
    name_row = await (
        await db.execute(
            "SELECT name FROM trades WHERE code = ? AND account_id = ? LIMIT 1",
            (code, aid),
        )
    ).fetchone()
    name = dict(name_row)["name"] if name_row else ""
    if total_shares > 0:
        await db.execute(
            """
            INSERT INTO portfolio (code, name, total_shares, available_shares, avg_cost, updated_at, account_id)
            VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
            ON CONFLICT(account_id, code) DO UPDATE SET
                total_shares=excluded.total_shares,
                available_shares=excluded.available_shares,
                avg_cost=excluded.avg_cost,
                updated_at=excluded.updated_at,
                account_id=excluded.account_id
            """,
            (code, name, total_shares, total_shares, avg_cost, aid),
        )
    else:
        await db.execute("DELETE FROM portfolio WHERE code = ? AND account_id = ?", (code, aid))
    await db.commit()
    return {"code": code, "account_id": aid, "total_shares": total_shares, "avg_cost": avg_cost}


async def fetch_positions(db, account_id=None):
    if account_id:
        rows = await db.execute_fetchall(
            "SELECT * FROM portfolio WHERE total_shares > 0 AND account_id = ? ORDER BY market_value DESC, code ASC",
            (account_id,),
        )
    else:
        rows = await db.execute_fetchall("SELECT * FROM portfolio WHERE total_shares > 0 ORDER BY account_id ASC, market_value DESC, code ASC")
    return [dict(row) for row in rows]


async def fetch_cash_and_fees(db, account_id=None):
    if account_id:
        cash_key = f"cash_balance_{account_id}"
        cash_row = await (
            await db.execute("SELECT value FROM settings WHERE key = ?", (cash_key,))
        ).fetchone()
        if not cash_row and account_id == "default":
            cash_row = await (
                await db.execute("SELECT value FROM settings WHERE key = 'cash_balance'")
            ).fetchone()
        cash = float(cash_row[0]) if cash_row else 0.0
        cash_source = "manual" if cash_row else "unset"
    else:
        cash_rows = await db.execute_fetchall(
            "SELECT key, value FROM settings WHERE key LIKE 'cash_balance_%'"
        )
        if cash_rows:
            cash = sum(float(row["value"] or 0) for row in cash_rows)
            cash_source = "manual"
        else:
            cash_row = await (
                await db.execute("SELECT value FROM settings WHERE key = 'cash_balance'")
            ).fetchone()
            cash = float(cash_row[0]) if cash_row else 0.0
            cash_source = "manual" if cash_row else "unset"
    if account_id:
        fee_row = await (
            await db.execute(
                """
                SELECT COALESCE(SUM(commission), 0) as total_commission,
                       COALESCE(SUM(stamp_tax), 0) as total_stamp_tax
                FROM trades
                WHERE account_id = ?
                """,
                (account_id,),
            )
        ).fetchone()
    else:
        fee_row = await (
            await db.execute(
                """
                SELECT COALESCE(SUM(commission), 0) as total_commission,
                       COALESCE(SUM(stamp_tax), 0) as total_stamp_tax
                FROM trades
                """
            )
        ).fetchone()
    return {
        "cash": cash,
        "cash_source": cash_source,
        "total_commission": float(fee_row["total_commission"]) if fee_row else 0,
        "total_stamp_tax": float(fee_row["total_stamp_tax"]) if fee_row else 0,
    }


async def set_cash_balance(db, account_id: str | None, balance: float, notes: str = "", source: str = "manual"):
    aid = account_id or "default"
    key = f"cash_balance_{aid}"
    current = await fetch_cash_and_fees(db, aid)
    amount = round(float(balance) - float(current["cash"]), 3)
    direction = "deposit" if amount > 0 else "withdraw" if amount < 0 else "adjust"
    await db.execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(round(float(balance), 3))),
    )
    await db.execute(
        """
        INSERT INTO cash_ledger (account_id, direction, amount, balance_after, source, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (aid, direction, amount, round(float(balance), 3), source, notes),
    )
    await db.commit()
    return {
        "account_id": aid,
        "cash": round(float(balance), 3),
        "amount": amount,
        "direction": direction,
        "source": source,
    }


async def fetch_cash_ledger(db, account_id: str | None = None, limit: int = 20):
    aid = account_id or "default"
    rows = await db.execute_fetchall(
        """
        SELECT id, account_id, direction, amount, balance_after, source, notes, created_at
        FROM cash_ledger
        WHERE account_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (aid, limit),
    )
    return [dict(row) for row in rows]


async def fetch_daily_pnl(db, year: int, month: int, code: str | None = None, account_id: str | None = "default"):
    params = [str(year), f"{month:02d}"]
    query = (
        "SELECT date, account_id, code6, pnl, close_price, shares, total_pnl "
        "FROM daily_pnl WHERE strftime('%Y', date) = ? AND strftime('%m', date) = ?"
    )
    if account_id:
        query += " AND account_id = ?"
        params.append(account_id)
    if code:
        query += " AND code6 = ?"
        params.append(code[:6])
    query += " ORDER BY date"
    rows = await db.execute_fetchall(query, params)
    return [dict(row) for row in rows]


def _where_clause(filters: dict):
    conditions = []
    params = []
    for column, value in filters.items():
        if value is not None:
            conditions.append(f"{column} = ?")
            params.append(value)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return where, params


async def fetch_pending_positions(db, account_id=None):
    where, params = _where_clause({"account_id": account_id})
    rows = await db.execute_fetchall(
        f"SELECT * FROM pending_positions{where} ORDER BY created_at DESC",
        params,
    )
    return [dict(row) for row in rows]


async def insert_pending_position(db, req, plan_total_cost):
    account_id = getattr(req, "account_id", None) or "default"
    await db.execute(
        """
        INSERT INTO pending_positions
            (code, name, target_buy_price, plan_shares, plan_total_cost, reason, strategy_state, account_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            req.code,
            req.name,
            req.target_buy_price,
            req.plan_shares,
            plan_total_cost,
            req.reason,
            req.strategy_state,
            account_id,
        ),
    )
    await db.commit()
    row = await (await db.execute("SELECT last_insert_rowid()")).fetchone()
    return row[0]


async def update_pending_position(db, position_id: int, req, plan_total_cost):
    account_id = getattr(req, "account_id", None)
    cursor = await db.execute(
        """
        UPDATE pending_positions
        SET code=?, name=?, target_buy_price=?, plan_shares=?,
            plan_total_cost=?, reason=?, strategy_state=?
        WHERE id=? AND (? IS NULL OR account_id = ?)
        """,
        (
            req.code,
            req.name,
            req.target_buy_price,
            req.plan_shares,
            plan_total_cost,
            req.reason,
            req.strategy_state,
            position_id,
            account_id,
            account_id,
        ),
    )
    await db.commit()
    return cursor.rowcount


async def delete_pending_position(db, position_id: int, account_id: str | None = None):
    cursor = await db.execute(
        "DELETE FROM pending_positions WHERE id=? AND (? IS NULL OR account_id = ?)",
        (position_id, account_id, account_id),
    )
    await db.commit()
    return cursor.rowcount


async def fetch_buy_points(db, code: str):
    rows = await db.execute_fetchall(
        "SELECT * FROM buy_points WHERE code = ? ORDER BY created_at DESC",
        (code,),
    )
    return [dict(row) for row in rows]


async def insert_buy_point(db, code: str, req):
    await db.execute(
        "INSERT INTO buy_points (code, price, shares, reason, status) VALUES (?, ?, ?, ?, ?)",
        (code, req.price, req.shares, req.reason, req.status),
    )
    await db.commit()
    row = await (await db.execute("SELECT last_insert_rowid()")).fetchone()
    return row[0]


async def delete_buy_point(db, point_id: int):
    cursor = await db.execute("DELETE FROM buy_points WHERE id = ?", (point_id,))
    await db.commit()
    return cursor.rowcount


async def fetch_day_trades(db, day: str, account_id: str | None = "default"):
    params = [day]
    where = "WHERE date(trade_time) = ?"
    if account_id:
        where += " AND account_id = ?"
        params.append(account_id)
    rows = await db.execute_fetchall(
        f"""
        SELECT code, name, direction, price, shares, amount
        FROM trades
        {where}
        ORDER BY trade_time ASC
        """,
        params,
    )
    return [dict(row) for row in rows]


async def fetch_daily_pnl_day(db, day: str, account_id: str | None = "default"):
    params = [day]
    where = "WHERE date = ?"
    if account_id:
        where += " AND account_id = ?"
        params.append(account_id)
    rows = await db.execute_fetchall(
        f"SELECT * FROM daily_pnl {where} ORDER BY code6",
        params,
    )
    if not rows:
        return None
    items = [dict(row) for row in rows]
    summary = next((row for row in items if not row.get("code6")), None)
    if summary and summary.get("total_pnl") is not None:
        total_pnl = summary.get("total_pnl") or 0
    else:
        total_pnl = sum((row.get("pnl") or 0) for row in items if row.get("code6"))
    result = dict(summary) if summary else {"date": day, "code6": ""}
    result["total_pnl"] = round(total_pnl, 3)
    return result


async def fetch_daily_pnl_stock_rows_day(db, day: str, account_id: str | None = "default"):
    params = [day]
    account_filter = ""
    if account_id:
        account_filter = "AND account_id = ?"
        params.append(account_id)
    rows = await db.execute_fetchall(
        f"""
        SELECT date, account_id, code6, pnl, close_price, shares
        FROM daily_pnl
        WHERE date = ? {account_filter} AND COALESCE(code6, '') <> ''
        ORDER BY code6
        """,
        params,
    )
    return [dict(row) for row in rows]


async def fetch_trading_plans(db, status=None, account_id=None):
    where, params = _where_clause({"status": status, "account_id": account_id})
    rows = await db.execute_fetchall(
        f"SELECT * FROM trading_plans{where} ORDER BY created_at DESC",
        params,
    )
    return [dict(row) for row in rows]


async def insert_trading_plan(db, req, plan_total_cost):
    account_id = getattr(req, "account_id", None) or "default"
    await db.execute(
        """
        INSERT INTO trading_plans (
            code, name, direction, plan_type, target_price, condition_type,
            plan_shares, plan_total_cost, reason, status, expires_at, account_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            req.code,
            req.name,
            req.direction,
            req.plan_type,
            req.target_price,
            req.condition_type,
            req.plan_shares,
            plan_total_cost,
            req.reason,
            req.status,
            req.expires_at,
            account_id,
        ),
    )
    await db.commit()
    row = await (await db.execute("SELECT last_insert_rowid()")).fetchone()
    return row[0]


async def update_trading_plan(db, plan_id: int, req, plan_total_cost):
    account_id = getattr(req, "account_id", None)
    cursor = await db.execute(
        """
        UPDATE trading_plans
        SET code=?, name=?, direction=?, plan_type=?, target_price=?,
            condition_type=?, plan_shares=?, plan_total_cost=?, reason=?,
            status=?, expires_at=?
        WHERE id=? AND (? IS NULL OR account_id = ?)
        """,
        (
            req.code,
            req.name,
            req.direction,
            req.plan_type,
            req.target_price,
            req.condition_type,
            req.plan_shares,
            plan_total_cost,
            req.reason,
            req.status,
            req.expires_at,
            plan_id,
            account_id,
            account_id,
        ),
    )
    await db.commit()
    return cursor.rowcount


async def delete_trading_plan(db, plan_id: int, account_id: str | None = None):
    cursor = await db.execute(
        "DELETE FROM trading_plans WHERE id=? AND (? IS NULL OR account_id = ?)",
        (plan_id, account_id, account_id),
    )
    await db.commit()
    return cursor.rowcount
