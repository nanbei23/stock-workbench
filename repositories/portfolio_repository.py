"""Portfolio database access helpers."""


async def fetch_accounts(db):
    rows = await db.execute_fetchall("SELECT * FROM accounts ORDER BY created_at")
    return [dict(row) for row in rows]


async def create_account(db, account_id: str, name: str, broker: str):
    await db.execute(
        "INSERT INTO accounts (id, name, broker) VALUES (?, ?, ?)",
        (account_id, name, broker),
    )
    await db.commit()


async def fetch_watchlist_and_positions(db):
    rows = await db.execute_fetchall(
        "SELECT * FROM watchlist ORDER BY sort_order ASC, added_at ASC"
    )
    stocks = [dict(row) for row in rows]
    portfolio_rows = await db.execute_fetchall(
        "SELECT code, avg_cost, total_shares FROM portfolio"
    )
    portfolio_map = {row["code"]: dict(row) for row in portfolio_rows}
    return stocks, portfolio_map


async def next_watchlist_sort_order(db):
    row = await (await db.execute("SELECT COALESCE(MAX(sort_order), 0) FROM watchlist")).fetchone()
    return (row[0] if row else 0) + 1


async def insert_watchlist_stock(db, req, sort_order: int):
    await db.execute(
        """
        INSERT OR IGNORE INTO watchlist (
            code, name, group_name, sort_order, strategy_state,
            target_buy_price, target_sell_price, stop_loss_price, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    await db.commit()
    row = await (await db.execute("SELECT * FROM watchlist WHERE code = ?", (req.code,))).fetchone()
    return dict(row) if row else {"code": req.code, "name": req.name}


async def delete_watchlist_stock(db, code: str):
    cursor = await db.execute("DELETE FROM watchlist WHERE code = ?", (code,))
    await db.commit()
    return cursor.rowcount


async def update_watchlist_stock(db, code: str, updates: dict):
    if not updates:
        return None
    columns = [f"{key} = ?" for key in updates]
    params = list(updates.values()) + [code]
    cursor = await db.execute(
        f"UPDATE watchlist SET {', '.join(columns)} WHERE code = ?",
        params,
    )
    await db.commit()
    return cursor.rowcount


async def reorder_watchlist(db, items):
    for item in items:
        await db.execute(
            "UPDATE watchlist SET sort_order = ? WHERE code = ?",
            (item.sort_order, item.code),
        )
    await db.commit()


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
    amount = round(req.price * req.shares, 2)
    total_cost = round(amount + req.commission + req.stamp_tax + req.transfer_fee, 2)
    trade_time = req.trade_time if req.trade_time else None
    await db.execute(
        """
        INSERT INTO trades (
            code, name, direction, price, shares, amount,
            commission, stamp_tax, transfer_fee, total_cost, trade_time, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), ?)
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
        ),
    )
    await db.commit()


async def fetch_trade(db, trade_id: int):
    row = await (await db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))).fetchone()
    return dict(row) if row else None


async def fetch_trade_stats(db, code: str):
    lowest_row = await (
        await db.execute(
            "SELECT MIN(price) as lowest_buy_price FROM trades WHERE code = ? AND direction = 'buy'",
            (code,),
        )
    ).fetchone()
    latest_row = await (
        await db.execute(
            "SELECT price FROM trades WHERE code = ? AND direction = 'buy' ORDER BY trade_time DESC LIMIT 1",
            (code,),
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


async def count_stock_trades(db, code: str):
    row = await (await db.execute("SELECT COUNT(*) as cnt FROM trades WHERE code = ?", (code,))).fetchone()
    return dict(row)["cnt"] if row else 0


async def delete_stock_trades(db, code: str):
    await db.execute("DELETE FROM trades WHERE code = ?", (code,))
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


async def recalc_portfolio(db, code: str):
    rows = await db.execute_fetchall(
        """
        SELECT direction, price, shares, amount, commission, stamp_tax, transfer_fee
        FROM trades
        WHERE code = ?
        ORDER BY trade_time ASC
        """,
        (code,),
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

    avg_cost = round(total_cost / total_shares, 4) if total_shares > 0 else 0
    name_row = await (
        await db.execute("SELECT name FROM trades WHERE code = ? LIMIT 1", (code,))
    ).fetchone()
    name = dict(name_row)["name"] if name_row else ""
    if total_shares > 0:
        await db.execute(
            """
            INSERT INTO portfolio (code, name, total_shares, available_shares, avg_cost, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(code) DO UPDATE SET
                total_shares=excluded.total_shares,
                available_shares=excluded.available_shares,
                avg_cost=excluded.avg_cost,
                updated_at=excluded.updated_at
            """,
            (code, name, total_shares, total_shares, avg_cost),
        )
    else:
        await db.execute("DELETE FROM portfolio WHERE code = ?", (code,))
    await db.commit()
    return {"code": code, "total_shares": total_shares, "avg_cost": avg_cost}


async def fetch_positions(db, account_id=None):
    if account_id:
        rows = await db.execute_fetchall(
            "SELECT * FROM portfolio WHERE total_shares > 0 AND account_id = ?",
            (account_id,),
        )
    else:
        rows = await db.execute_fetchall("SELECT * FROM portfolio WHERE total_shares > 0")
    return [dict(row) for row in rows]


async def fetch_cash_and_fees(db, account_id=None):
    cash_key = f"cash_balance_{account_id}" if account_id else "cash_balance"
    cash_row = await (
        await db.execute("SELECT value FROM settings WHERE key = ?", (cash_key,))
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
    amount = round(float(balance) - float(current["cash"]), 2)
    direction = "deposit" if amount > 0 else "withdraw" if amount < 0 else "adjust"
    await db.execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, str(round(float(balance), 2))),
    )
    await db.execute(
        """
        INSERT INTO cash_ledger (account_id, direction, amount, balance_after, source, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (aid, direction, amount, round(float(balance), 2), source, notes),
    )
    await db.commit()
    return {
        "account_id": aid,
        "cash": round(float(balance), 2),
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


async def fetch_daily_pnl(db, year: int, month: int, code: str | None = None):
    params = [str(year), f"{month:02d}"]
    query = (
        "SELECT date, code6, pnl, close_price, shares, total_pnl "
        "FROM daily_pnl WHERE strftime('%Y', date) = ? AND strftime('%m', date) = ?"
    )
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


async def fetch_conditional_orders(db, status=None, account_id=None):
    where, params = _where_clause({"status": status, "account_id": account_id})
    rows = await db.execute_fetchall(
        f"SELECT * FROM conditional_orders{where} ORDER BY created_at DESC",
        params,
    )
    return [dict(row) for row in rows]


async def insert_conditional_order(db, req):
    await db.execute(
        """
        INSERT INTO conditional_orders
            (code, name, condition_type, target_price, action, shares, notes, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            req.code,
            req.name,
            req.condition_type,
            req.target_price,
            req.action,
            req.shares,
            req.notes,
            req.expires_at,
        ),
    )
    await db.commit()
    row = await (await db.execute("SELECT last_insert_rowid()")).fetchone()
    return row[0]


async def cancel_conditional_order(db, order_id: int):
    cursor = await db.execute(
        "UPDATE conditional_orders SET status = 'cancelled' WHERE id = ?",
        (order_id,),
    )
    await db.commit()
    return cursor.rowcount


async def fetch_pending_positions(db, account_id=None):
    where, params = _where_clause({"account_id": account_id})
    rows = await db.execute_fetchall(
        f"SELECT * FROM pending_positions{where} ORDER BY created_at DESC",
        params,
    )
    return [dict(row) for row in rows]


async def insert_pending_position(db, req, plan_total_cost):
    await db.execute(
        """
        INSERT INTO pending_positions
            (code, name, target_buy_price, plan_shares, plan_total_cost, reason, strategy_state)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            req.code,
            req.name,
            req.target_buy_price,
            req.plan_shares,
            plan_total_cost,
            req.reason,
            req.strategy_state,
        ),
    )
    await db.commit()
    row = await (await db.execute("SELECT last_insert_rowid()")).fetchone()
    return row[0]


async def update_pending_position(db, position_id: int, req, plan_total_cost):
    cursor = await db.execute(
        """
        UPDATE pending_positions
        SET code=?, name=?, target_buy_price=?, plan_shares=?,
            plan_total_cost=?, reason=?, strategy_state=?
        WHERE id=?
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
        ),
    )
    await db.commit()
    return cursor.rowcount


async def delete_pending_position(db, position_id: int):
    cursor = await db.execute("DELETE FROM pending_positions WHERE id=?", (position_id,))
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


async def fetch_day_trades(db, day: str):
    rows = await db.execute_fetchall(
        """
        SELECT code, name, direction, price, shares, amount
        FROM trades
        WHERE date(trade_time) = ?
        ORDER BY trade_time ASC
        """,
        (day,),
    )
    return [dict(row) for row in rows]


async def fetch_daily_pnl_day(db, day: str):
    row = await (await db.execute("SELECT * FROM daily_pnl WHERE date = ?", (day,))).fetchone()
    return dict(row) if row else None


async def fetch_trading_plans(db, status=None, account_id=None):
    where, params = _where_clause({"status": status, "account_id": account_id})
    rows = await db.execute_fetchall(
        f"SELECT * FROM trading_plans{where} ORDER BY created_at DESC",
        params,
    )
    return [dict(row) for row in rows]


async def insert_trading_plan(db, req, plan_total_cost):
    await db.execute(
        """
        INSERT INTO trading_plans (
            code, name, direction, plan_type, target_price, condition_type,
            plan_shares, plan_total_cost, reason, status, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ),
    )
    await db.commit()
    row = await (await db.execute("SELECT last_insert_rowid()")).fetchone()
    return row[0]


async def update_trading_plan(db, plan_id: int, req, plan_total_cost):
    cursor = await db.execute(
        """
        UPDATE trading_plans
        SET code=?, name=?, direction=?, plan_type=?, target_price=?,
            condition_type=?, plan_shares=?, plan_total_cost=?, reason=?,
            status=?, expires_at=?
        WHERE id=?
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
        ),
    )
    await db.commit()
    return cursor.rowcount


async def delete_trading_plan(db, plan_id: int):
    cursor = await db.execute("DELETE FROM trading_plans WHERE id=?", (plan_id,))
    await db.commit()
    return cursor.rowcount
