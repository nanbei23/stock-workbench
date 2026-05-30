"""Portfolio business operations."""

import uuid
from datetime import datetime

from fastapi import HTTPException

from data.quote import get_batch_quotes
from models.database import get_db
from repositories import portfolio_repository as repo


async def _with_db(fn):
    db = await get_db()
    try:
        return await fn(db)
    finally:
        await db.close()


def _enrich_position(position: dict, quote: dict):
    position["price"] = quote.get("price", 0)
    position["prev_close"] = quote.get("prev_close", 0)
    position["change_pct"] = quote.get("change_pct", 0)
    position["name"] = quote.get("name", position.get("name", ""))
    if position["avg_cost"] and position["price"]:
        position["unrealized_pnl"] = round(
            (position["price"] - position["avg_cost"]) * position["total_shares"], 2
        )
        position["unrealized_pnl_pct"] = round(
            (position["price"] - position["avg_cost"]) / position["avg_cost"] * 100, 2
        )
    if position["prev_close"] and position["price"]:
        position["daily_pnl"] = round(
            (position["price"] - position["prev_close"]) * position["total_shares"], 2
        )


async def list_accounts():
    return {"accounts": await _with_db(repo.fetch_accounts)}


async def create_account(name: str, broker: str = "", account_id: str | None = None):
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    aid = account_id or str(uuid.uuid4())[:8]

    async def _create(db):
        await repo.create_account(db, aid, name, broker)

    await _with_db(_create)
    return {"success": True, "id": aid}


async def get_watchlist():
    async def _load(db):
        return await repo.fetch_watchlist_and_positions(db)

    stocks, portfolio_map = await _with_db(_load)
    if stocks:
        quotes = await get_batch_quotes([stock["code"] for stock in stocks])
        for stock in stocks:
            quote = quotes.get(stock["code"], {})
            stock["price"] = quote.get("price", 0)
            stock["change_pct"] = quote.get("change_pct", 0)
            stock["change"] = quote.get("change", 0)
            stock["prev_close"] = quote.get("prev_close", 0)
            stock["volume"] = quote.get("volume", 0)
            stock["amount"] = quote.get("amount", 0)
            stock["turnover"] = quote.get("turnover", 0)
            stock["pe"] = quote.get("pe", 0)
            stock["total_market_cap"] = quote.get("total_market_cap", 0)
            position = portfolio_map.get(stock["code"], {})
            stock["avg_cost"] = position.get("avg_cost", 0)
            stock["total_shares"] = position.get("total_shares", 0)
            if stock["avg_cost"] and stock["total_shares"] and stock["price"]:
                stock["unrealized_pnl"] = round(
                    (stock["price"] - stock["avg_cost"]) * stock["total_shares"], 2
                )
                stock["unrealized_pnl_pct"] = round(
                    (stock["price"] - stock["avg_cost"]) / stock["avg_cost"] * 100, 2
                )
            else:
                stock["unrealized_pnl"] = 0
                stock["unrealized_pnl_pct"] = 0
            stock["daily_pnl"] = (
                round((stock["price"] - stock["prev_close"]) * stock["total_shares"], 2)
                if stock["prev_close"] and stock["total_shares"] and stock["price"]
                else 0
            )
    return {"count": len(stocks), "stocks": stocks}


async def add_to_watchlist(req):
    async def _add(db):
        sort_order = await repo.next_watchlist_sort_order(db)
        return await repo.insert_watchlist_stock(db, req, sort_order)

    stock = await _with_db(_add)
    return {"status": "ok", "stock": stock}


async def remove_from_watchlist(code: str):
    async def _remove(db):
        return await repo.delete_watchlist_stock(db, code)

    rowcount = await _with_db(_remove)
    if rowcount == 0:
        raise HTTPException(status_code=404, detail=f"未找到自选股 {code}")
    return {"status": "ok", "code": code}


async def update_watchlist(code: str, req):
    updates = {
        key: value
        for key, value in {
            "target_buy_price": req.target_buy_price,
            "target_sell_price": req.target_sell_price,
            "stop_loss_price": req.stop_loss_price,
            "strategy_state": req.strategy_state,
            "notes": req.notes,
        }.items()
        if value is not None
    }
    if not updates:
        raise HTTPException(status_code=400, detail="没有要更新的字段")

    async def _update(db):
        return await repo.update_watchlist_stock(db, code, updates)

    rowcount = await _with_db(_update)
    if rowcount == 0:
        raise HTTPException(status_code=404, detail=f"未找到自选股 {code}")
    return {"status": "ok", "code": code}


async def reorder_watchlist(req):
    async def _reorder(db):
        await repo.reorder_watchlist(db, req.items)

    await _with_db(_reorder)
    return {"status": "ok", "updated": len(req.items)}


async def get_trades(code=None, account_id=None):
    async def _load(db):
        return await repo.fetch_trades(db, code, account_id)

    trades = await _with_db(_load)
    return {"count": len(trades), "trades": trades}


async def add_trade(req):
    async def _add(db):
        await repo.insert_trade(db, req)
        return await repo.recalc_portfolio(db, req.code)

    return {"status": "ok", "trade": await _with_db(_add)}


async def get_trade_stats(code: str):
    async def _load(db):
        return await repo.fetch_trade_stats(db, code)

    stats = await _with_db(_load)
    return {"code": code, **stats}


async def delete_trade(trade_id: int):
    async def _delete(db):
        trade = await repo.fetch_trade(db, trade_id)
        if not trade:
            raise HTTPException(status_code=404, detail="未找到交易记录")
        await repo.delete_trade(db, trade_id)
        portfolio = await repo.recalc_portfolio(db, trade["code"])
        return trade, portfolio

    _, portfolio = await _with_db(_delete)
    return {"status": "ok", "deleted_id": trade_id, "portfolio": portfolio}


async def clear_stock_trades(code: str):
    async def _clear(db):
        count = await repo.count_stock_trades(db, code)
        if count == 0:
            raise HTTPException(status_code=404, detail=f"未找到 {code} 的交易记录")
        await repo.delete_stock_trades(db, code)
        portfolio = await repo.recalc_portfolio(db, code)
        return count, portfolio

    count, portfolio = await _with_db(_clear)
    return {"status": "ok", "deleted_count": count, "code": code, "portfolio": portfolio}


async def edit_trade(trade_id: int, req):
    async def _edit(db):
        trade = await repo.fetch_trade(db, trade_id)
        if not trade:
            raise HTTPException(status_code=404, detail="未找到交易记录")
        values = {
            "price": req.price if req.price is not None else trade["price"],
            "shares": req.shares if req.shares is not None else trade["shares"],
            "commission": req.commission if req.commission is not None else trade["commission"],
            "stamp_tax": req.stamp_tax if req.stamp_tax is not None else trade["stamp_tax"],
            "transfer_fee": req.transfer_fee if req.transfer_fee is not None else trade["transfer_fee"],
            "notes": req.notes if req.notes is not None else trade.get("notes", ""),
            "direction": req.direction if req.direction is not None else trade["direction"],
        }
        values["amount"] = round(values["price"] * values["shares"], 2)
        values["total_cost"] = round(
            values["amount"]
            + values["commission"]
            + values["stamp_tax"]
            + values["transfer_fee"],
            2,
        )
        await repo.update_trade(db, trade_id, values)
        return await repo.recalc_portfolio(db, trade["code"])

    portfolio = await _with_db(_edit)
    return {"status": "ok", "trade_id": trade_id, "portfolio": portfolio}


async def get_portfolio(account_id=None):
    async def _load(db):
        return await repo.fetch_positions(db, account_id)

    positions = await _with_db(_load)
    if positions:
        quotes = await get_batch_quotes([position["code"] for position in positions])
        for position in positions:
            _enrich_position(position, quotes.get(position["code"], {}))
    return {"count": len(positions), "positions": positions}


async def get_portfolio_overview(account_id=None):
    async def _load(db):
        positions = await repo.fetch_positions(db, account_id)
        cash_and_fees = await repo.fetch_cash_and_fees(db, account_id)
        return positions, cash_and_fees

    positions, cash_and_fees = await _with_db(_load)
    total_market_value = 0
    total_cost = 0
    total_daily_pnl = 0
    total_unrealized_pnl = 0

    if positions:
        quotes = await get_batch_quotes([position["code"] for position in positions])
        for position in positions:
            quote = quotes.get(position["code"], {})
            price = quote.get("price", 0)
            prev_close = quote.get("prev_close", 0)
            shares = position["total_shares"]
            avg_cost = position["avg_cost"]
            total_market_value += price * shares
            total_cost += avg_cost * shares
            total_daily_pnl += (price - prev_close) * shares if price and prev_close else 0
            total_unrealized_pnl += (price - avg_cost) * shares if price and avg_cost else 0

    cash = cash_and_fees["cash"]
    total_assets = total_market_value + cash
    return {
        "total_assets": round(total_assets, 2),
        "market_value": round(total_market_value, 2),
        "cash": round(cash, 2),
        "total_cost": round(total_cost, 2),
        "daily_pnl": round(total_daily_pnl, 2),
        "unrealized_pnl": round(total_unrealized_pnl, 2),
        "daily_pnl_pct": round(total_daily_pnl / total_assets * 100, 2) if total_assets else 0,
        "unrealized_pnl_pct": round(total_unrealized_pnl / total_cost * 100, 2) if total_cost else 0,
        "total_commission": round(cash_and_fees["total_commission"], 2),
        "total_stamp_tax": round(cash_and_fees["total_stamp_tax"], 2),
    }


async def get_account_dashboard():
    accounts = (await list_accounts()).get("accounts", [])
    if not any(account.get("id") == "default" for account in accounts):
        accounts = [{"id": "default", "name": "默认账户", "broker": ""}, *accounts]

    combined = await get_portfolio_overview()
    items = []
    for account in accounts:
        overview = await get_portfolio_overview(account.get("id"))
        positions = await get_portfolio(account.get("id"))
        items.append({
            "id": account.get("id"),
            "name": account.get("name") or account.get("id"),
            "broker": account.get("broker") or "",
            "position_count": positions.get("count", 0),
            **overview,
        })

    return {
        "combined": combined,
        "accounts": items,
        "dominant_account": max(items, key=lambda item: item.get("market_value", 0), default=None),
    }


def _planned_total_cost(price, shares, explicit_total=None):
    if explicit_total is not None:
        return explicit_total
    if price and shares:
        return round(price * shares, 2)
    return None


async def get_pnl_calendar(year=None, month=None, code=None):
    now = datetime.now()
    y = year or now.year
    m = month or now.month

    async def _load(db):
        return await repo.fetch_daily_pnl(db, y, m, code)

    rows = await _with_db(_load)
    if code:
        days = []
        for row in rows:
            row["stock_pnl"] = row.pop("pnl", None)
            days.append(row)
    else:
        date_map: dict = {}
        for row in rows:
            day = row["date"]
            if day not in date_map:
                date_map[day] = {
                    "date": day,
                    "total_pnl": row.get("total_pnl") or 0,
                    "stocks": [],
                }
            if row.get("code6"):
                date_map[day]["stocks"].append({
                    "code6": row["code6"],
                    "pnl": row.get("pnl"),
                    "close_price": row.get("close_price"),
                    "shares": row.get("shares"),
                })
                if row.get("pnl") and not row.get("total_pnl"):
                    date_map[day]["total_pnl"] += row["pnl"]
        days = list(date_map.values())

    total_pnl = sum(day.get("total_pnl") or 0 for day in days)
    win_days = sum(1 for day in days if (day.get("total_pnl") or 0) > 0)
    loss_days = sum(1 for day in days if (day.get("total_pnl") or 0) < 0)
    trade_days = win_days + loss_days
    win_rate = round(win_days / trade_days * 100, 1) if trade_days else 0
    return {
        "year": y,
        "month": m,
        "code": code,
        "days": days,
        "total_pnl": round(total_pnl, 2),
        "win_days": win_days,
        "loss_days": loss_days,
        "trade_days": trade_days,
        "win_rate": win_rate,
    }


async def get_conditional_orders(status=None, account_id=None):
    async def _load(db):
        return await repo.fetch_conditional_orders(db, status, account_id)

    orders = await _with_db(_load)
    if orders:
        quotes = await get_batch_quotes(list({order["code"] for order in orders}))
        for order in orders:
            quote = quotes.get(order["code"], {})
            order["current_price"] = quote.get("price", 0)
            order["change_pct"] = quote.get("change_pct", 0)
            if order["current_price"] and order["target_price"]:
                order["distance_pct"] = round(
                    (order["target_price"] - order["current_price"]) / order["current_price"] * 100,
                    2,
                )
    return {"count": len(orders), "orders": orders}


async def create_conditional_order(req):
    async def _create(db):
        return await repo.insert_conditional_order(db, req)

    order_id = await _with_db(_create)
    return {"status": "ok", "id": order_id}


async def cancel_conditional_order(order_id: int):
    async def _cancel(db):
        return await repo.cancel_conditional_order(db, order_id)

    await _with_db(_cancel)
    return {"status": "ok", "id": order_id}


async def get_pending_positions(account_id=None):
    async def _load(db):
        return await repo.fetch_pending_positions(db, account_id)

    positions = await _with_db(_load)
    if positions:
        quotes = await get_batch_quotes(list({position["code"] for position in positions}))
        for position in positions:
            quote = quotes.get(position["code"], {})
            position["current_price"] = quote.get("price", 0)
            position["change_pct"] = quote.get("change_pct", 0)
            if position.get("target_buy_price") and position["current_price"]:
                position["distance_pct"] = round(
                    (position["current_price"] - position["target_buy_price"])
                    / position["target_buy_price"]
                    * 100,
                    2,
                )
            else:
                position["distance_pct"] = None
    return {"count": len(positions), "positions": positions}


async def add_pending_position(req):
    plan_total_cost = _planned_total_cost(req.target_buy_price, req.plan_shares, req.plan_total_cost)

    async def _add(db):
        return await repo.insert_pending_position(db, req, plan_total_cost)

    position_id = await _with_db(_add)
    return {"status": "ok", "id": position_id}


async def update_pending_position(position_id: int, req):
    plan_total_cost = _planned_total_cost(req.target_buy_price, req.plan_shares, req.plan_total_cost)

    async def _update(db):
        return await repo.update_pending_position(db, position_id, req, plan_total_cost)

    rowcount = await _with_db(_update)
    if rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到待持仓记录")
    return {"status": "ok", "id": position_id}


async def delete_pending_position(position_id: int):
    async def _delete(db):
        return await repo.delete_pending_position(db, position_id)

    rowcount = await _with_db(_delete)
    if rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到待持仓记录")
    return {"status": "ok", "id": position_id}


async def get_buy_points(code: str):
    async def _load(db):
        return await repo.fetch_buy_points(db, code)

    return {"code": code, "buy_points": await _with_db(_load)}


async def add_buy_point(code: str, req):
    async def _add(db):
        return await repo.insert_buy_point(db, code, req)

    point_id = await _with_db(_add)
    return {"status": "ok", "id": point_id}


async def delete_buy_point(point_id: int):
    async def _delete(db):
        return await repo.delete_buy_point(db, point_id)

    await _with_db(_delete)
    return {"status": "ok", "id": point_id}


async def get_pnl_day_detail(day: str):
    async def _load(db):
        trades = await repo.fetch_day_trades(db, day)
        daily_pnl = await repo.fetch_daily_pnl_day(db, day)
        return trades, daily_pnl

    trades, daily_pnl = await _with_db(_load)
    stock_pnl = {}
    for trade in trades:
        code = trade["code"]
        if code not in stock_pnl:
            stock_pnl[code] = {"code": code, "name": trade["name"], "trades": [], "amount": 0}
        stock_pnl[code]["trades"].append(trade)
        if trade["direction"] == "buy":
            stock_pnl[code]["amount"] -= trade["amount"]
        else:
            stock_pnl[code]["amount"] += trade["amount"]
    return {
        "date": day,
        "daily_pnl": daily_pnl,
        "stock_pnl": list(stock_pnl.values()),
        "trades": trades,
    }


async def get_trading_plans(status=None, account_id=None):
    async def _load(db):
        return await repo.fetch_trading_plans(db, status, account_id)

    plans = await _with_db(_load)
    if plans:
        quotes = await get_batch_quotes(list({plan["code"] for plan in plans}))
        for plan in plans:
            quote = quotes.get(plan["code"], {})
            plan["current_price"] = quote.get("price", 0)
            plan["change_pct"] = quote.get("change_pct", 0)
            if plan["current_price"] and plan.get("target_price"):
                plan["distance_pct"] = round(
                    (plan["current_price"] - plan["target_price"]) / plan["target_price"] * 100,
                    2,
                )
            else:
                plan["distance_pct"] = None
    return {"count": len(plans), "plans": plans}


async def create_trading_plan(req):
    plan_total_cost = _planned_total_cost(req.target_price, req.plan_shares, req.plan_total_cost)

    async def _create(db):
        return await repo.insert_trading_plan(db, req, plan_total_cost)

    plan_id = await _with_db(_create)
    return {"status": "ok", "id": plan_id}


async def update_trading_plan(plan_id: int, req):
    plan_total_cost = _planned_total_cost(req.target_price, req.plan_shares, req.plan_total_cost)

    async def _update(db):
        return await repo.update_trading_plan(db, plan_id, req, plan_total_cost)

    rowcount = await _with_db(_update)
    if rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到交易计划")
    return {"status": "ok", "id": plan_id}


async def delete_trading_plan(plan_id: int):
    async def _delete(db):
        return await repo.delete_trading_plan(db, plan_id)

    rowcount = await _with_db(_delete)
    if rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到交易计划")
    return {"status": "ok", "id": plan_id}
