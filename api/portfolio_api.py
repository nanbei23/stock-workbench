"""持仓API - Phase 3 完整实现"""
import uuid
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from models.database import get_db
from data.quote import get_batch_quotes, get_realtime_quote

logger = logging.getLogger(__name__)
router = APIRouter(tags=["持仓"])


# ── Account Management ──────────────────────────────────────
@router.get("/accounts")
async def list_accounts():
    """获取账户列表"""
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall("SELECT * FROM accounts ORDER BY created_at")
            return {'accounts': [dict(r) for r in rows]}
        finally:
            await db.close()
    except Exception as e:
        logger.error("list_accounts error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts")
async def create_account(request: Request):
    """创建账户"""
    body = await request.json()
    aid = body.get('id', str(uuid.uuid4())[:8])
    name = body.get('name', '')
    broker = body.get('broker', '')
    if not name:
        return JSONResponse({'error': 'name required'}, status_code=400)
    try:
        db = await get_db()
        try:
            await db.execute("INSERT INTO accounts (id, name, broker) VALUES (?, ?, ?)", (aid, name, broker))
            await db.commit()
        finally:
            await db.close()
        return {'success': True, 'id': aid}
    except Exception as e:
        logger.error("create_account error: %s", e)
        return JSONResponse({'error': str(e)}, status_code=500)


# ── Request Models ────────────────────────────────────────
class WatchlistAddRequest(BaseModel):
    code: str
    name: str = ""
    group_name: str = "默认"
    strategy_state: str = "watch"
    target_buy_price: Optional[float] = None
    target_sell_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    notes: str = ""


class TradeAddRequest(BaseModel):
    code: str
    name: str = ""
    direction: str = "buy"
    price: float
    shares: int
    commission: float = 0
    stamp_tax: float = 0
    transfer_fee: float = 0
    notes: str = ""
    trade_time: Optional[str] = None


class TradeEditRequest(BaseModel):
    price: Optional[float] = None
    shares: Optional[int] = None
    commission: Optional[float] = None
    stamp_tax: Optional[float] = None
    transfer_fee: Optional[float] = None
    notes: Optional[str] = None
    direction: Optional[str] = None


# ── 均价计算 ──────────────────────────────────────────────
async def _recalc_portfolio(db, code: str):
    """从trades表重新计算某只股票的持仓均价和股数"""
    cursor = await db.execute(
        "SELECT direction, price, shares, amount, commission, stamp_tax, transfer_fee FROM trades WHERE code = ? ORDER BY trade_time ASC",
        (code,)
    )
    rows = await cursor.fetchall()
    total_shares = 0
    total_cost = 0.0
    for r in rows:
        d = dict(r)
        if d["direction"] == "buy":
            total_shares += d["shares"]
            total_cost += d["amount"] + d["commission"] + d["stamp_tax"] + d["transfer_fee"]
        elif d["direction"] == "sell":
            if total_shares > 0:
                avg_before = total_cost / total_shares
                total_shares -= d["shares"]
                if total_shares < 0:
                    total_shares = 0
                total_cost = avg_before * total_shares
    avg_cost = round(total_cost / total_shares, 4) if total_shares > 0 else 0
    cursor2 = await db.execute("SELECT name FROM trades WHERE code = ? LIMIT 1", (code,))
    name_row = await cursor2.fetchone()
    name = dict(name_row)["name"] if name_row else ""
    if total_shares > 0:
        await db.execute(
            "INSERT INTO portfolio (code, name, total_shares, available_shares, avg_cost, updated_at) VALUES (?, ?, ?, ?, ?, datetime('now')) ON CONFLICT(code) DO UPDATE SET total_shares=excluded.total_shares, available_shares=excluded.available_shares, avg_cost=excluded.avg_cost, updated_at=excluded.updated_at",
            (code, name, total_shares, total_shares, avg_cost)
        )
    else:
        await db.execute("DELETE FROM portfolio WHERE code = ?", (code,))
    await db.commit()
    return {"code": code, "total_shares": total_shares, "avg_cost": avg_cost}


# ── Watchlist ─────────────────────────────────────────────
@router.get("/watchlist")
async def get_watchlist():
    """自选股列表（含实时行情+盈亏）"""
    try:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM watchlist ORDER BY sort_order ASC, added_at ASC"
            )
            rows = await cursor.fetchall()
            stocks = [dict(row) for row in rows]
            # 获取持仓数据
            cursor2 = await db.execute("SELECT code, avg_cost, total_shares FROM portfolio")
            portfolio_rows = await cursor2.fetchall()
            portfolio_map = {r["code"]: dict(r) for r in portfolio_rows}
        finally:
            await db.close()
        # 批量获取实时行情
        if stocks:
            codes = [s["code"] for s in stocks]
            quotes = await get_batch_quotes(codes)
            for s in stocks:
                q = quotes.get(s["code"], {})
                s["price"] = q.get("price", 0)
                s["change_pct"] = q.get("change_pct", 0)
                s["change"] = q.get("change", 0)
                s["prev_close"] = q.get("prev_close", 0)
                s["volume"] = q.get("volume", 0)
                s["amount"] = q.get("amount", 0)
                s["turnover"] = q.get("turnover", 0)
                s["pe"] = q.get("pe", 0)
                s["total_market_cap"] = q.get("total_market_cap", 0)
                # 持仓数据
                p = portfolio_map.get(s["code"], {})
                s["avg_cost"] = p.get("avg_cost", 0)
                s["total_shares"] = p.get("total_shares", 0)
                # 计算盈亏
                if s["avg_cost"] and s["total_shares"] and s["price"]:
                    s["unrealized_pnl"] = round((s["price"] - s["avg_cost"]) * s["total_shares"], 2)
                    s["unrealized_pnl_pct"] = round((s["price"] - s["avg_cost"]) / s["avg_cost"] * 100, 2)
                else:
                    s["unrealized_pnl"] = 0
                    s["unrealized_pnl_pct"] = 0
                # 当日盈亏
                if s["prev_close"] and s["total_shares"] and s["price"]:
                    s["daily_pnl"] = round((s["price"] - s["prev_close"]) * s["total_shares"], 2)
                else:
                    s["daily_pnl"] = 0
        return {"count": len(stocks), "stocks": stocks}
    except Exception as e:
        logger.error("get_watchlist error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/watchlist")
async def add_to_watchlist(req: WatchlistAddRequest):
    """添加自选股"""
    try:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT COALESCE(MAX(sort_order), 0) FROM watchlist"
            )
            row = await cursor.fetchone()
            max_order = row[0] if row else 0
            await db.execute(
                "INSERT OR IGNORE INTO watchlist (code, name, group_name, sort_order, strategy_state, target_buy_price, target_sell_price, stop_loss_price, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (req.code, req.name, req.group_name, max_order + 1, req.strategy_state, req.target_buy_price, req.target_sell_price, req.stop_loss_price, req.notes)
            )
            await db.commit()
            cursor = await db.execute("SELECT * FROM watchlist WHERE code = ?", (req.code,))
            row = await cursor.fetchone()
            if row:
                return {"status": "ok", "stock": dict(row)}
            return {"status": "ok", "stock": {"code": req.code, "name": req.name}}
        finally:
            await db.close()
    except Exception as e:
        logger.error("add_to_watchlist(%s) error: %s", req.code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/watchlist/{code}")
async def remove_from_watchlist(code: str):
    """删除自选股"""
    try:
        db = await get_db()
        try:
            cursor = await db.execute(
                "DELETE FROM watchlist WHERE code = ?", (code,)
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"未找到自选股 {code}")
            return {"status": "ok", "code": code}
        finally:
            await db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("remove_from_watchlist(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


class WatchlistUpdateRequest(BaseModel):
    target_buy_price: Optional[float] = None
    target_sell_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    strategy_state: Optional[str] = None
    notes: Optional[str] = None


@router.put("/watchlist/{code}")
async def update_watchlist(code: str, req: WatchlistUpdateRequest):
    """更新自选股（目标价等）"""
    try:
        db = await get_db()
        try:
            # Build dynamic SET clause
            updates = []
            params = []
            if req.target_buy_price is not None:
                updates.append("target_buy_price = ?")
                params.append(req.target_buy_price)
            if req.target_sell_price is not None:
                updates.append("target_sell_price = ?")
                params.append(req.target_sell_price)
            if req.stop_loss_price is not None:
                updates.append("stop_loss_price = ?")
                params.append(req.stop_loss_price)
            if req.strategy_state is not None:
                updates.append("strategy_state = ?")
                params.append(req.strategy_state)
            if req.notes is not None:
                updates.append("notes = ?")
                params.append(req.notes)

            if not updates:
                raise HTTPException(status_code=400, detail="没有要更新的字段")

            params.append(code)
            sql = f"UPDATE watchlist SET {', '.join(updates)} WHERE code = ?"
            cursor = await db.execute(sql, params)
            await db.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"未找到自选股 {code}")
            return {"status": "ok", "code": code}
        finally:
            await db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_watchlist(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 拖拽排序 ─────────────────────────────────────────────
class ReorderItem(BaseModel):
    code: str
    sort_order: int

class ReorderRequest(BaseModel):
    items: list[ReorderItem]


@router.put("/watchlist/reorder")
async def reorder_watchlist(req: ReorderRequest):
    """批量更新自选股排序"""
    try:
        db = await get_db()
        try:
            for item in req.items:
                await db.execute(
                    "UPDATE watchlist SET sort_order = ? WHERE code = ?",
                    (item.sort_order, item.code)
                )
            await db.commit()
            return {"status": "ok", "updated": len(req.items)}
        finally:
            await db.close()
    except Exception as e:
        logger.error("reorder_watchlist error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 交易记录 API ──────────────────────────────────────────
@router.get("/trades")
async def get_trades(code: Optional[str] = None, account_id: Optional[str] = Query(None)):
    """获取交易记录"""
    try:
        db = await get_db()
        try:
            conditions = []
            params = []
            if code:
                conditions.append("code = ?")
                params.append(code)
            if account_id:
                conditions.append("account_id = ?")
                params.append(account_id)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            cursor = await db.execute(f"SELECT * FROM trades{where} ORDER BY trade_time DESC", params)
            rows = await cursor.fetchall()
            return {"count": len(rows), "trades": [dict(r) for r in rows]}
        finally:
            await db.close()
    except Exception as e:
        logger.error("get_trades error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trades")
async def add_trade(req: TradeAddRequest):
    """录入交易（自动重算均价）"""
    try:
        db = await get_db()
        try:
            amount = round(req.price * req.shares, 2)
            total_cost = round(amount + req.commission + req.stamp_tax + req.transfer_fee, 2)
            trade_time = req.trade_time if req.trade_time else None
            await db.execute(
                "INSERT INTO trades (code, name, direction, price, shares, amount, commission, stamp_tax, transfer_fee, total_cost, trade_time, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), ?)",
                (req.code, req.name, req.direction, req.price, req.shares, amount, req.commission, req.stamp_tax, req.transfer_fee, total_cost, trade_time, req.notes)
            )
            await db.commit()
            result = await _recalc_portfolio(db, req.code)
            return {"status": "ok", "trade": result}
        finally:
            await db.close()
    except Exception as e:
        logger.error("add_trade error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades/stats/{code}")
async def get_trade_stats(code: str):
    """获取某只股票的交易统计（最低买入价、最近买入价）"""
    try:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT MIN(price) as lowest_buy_price FROM trades WHERE code = ? AND direction = 'buy'",
                (code,)
            )
            row = await cursor.fetchone()
            lowest = dict(row)["lowest_buy_price"] if row else None

            cursor2 = await db.execute(
                "SELECT price FROM trades WHERE code = ? AND direction = 'buy' ORDER BY trade_time DESC LIMIT 1",
                (code,)
            )
            row2 = await cursor2.fetchone()
            latest = dict(row2)["price"] if row2 else None

            return {
                "code": code,
                "lowest_buy_price": lowest,
                "latest_buy_price": latest,
            }
        finally:
            await db.close()
    except Exception as e:
        logger.error("get_trade_stats error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trades/{trade_id}")
async def delete_trade(trade_id: int):
    """删除单笔交易记录（撤销）"""
    try:
        db = await get_db()
        try:
            # 先获取交易信息以便重算持仓
            cursor = await db.execute("SELECT code FROM trades WHERE id = ?", (trade_id,))
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="未找到交易记录")
            code = dict(row)["code"]

            await db.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
            await db.commit()
            result = await _recalc_portfolio(db, code)
            return {"status": "ok", "deleted_id": trade_id, "portfolio": result}
        finally:
            await db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_trade error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trades/stock/{code}")
async def clear_stock_trades(code: str):
    """清空某只股票的所有交易记录"""
    try:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM trades WHERE code = ?", (code,))
            row = await cursor.fetchone()
            cnt = dict(row)["cnt"] if row else 0
            if cnt == 0:
                raise HTTPException(status_code=404, detail=f"未找到 {code} 的交易记录")

            await db.execute("DELETE FROM trades WHERE code = ?", (code,))
            await db.commit()
            result = await _recalc_portfolio(db, code)
            return {"status": "ok", "deleted_count": cnt, "code": code, "portfolio": result}
        finally:
            await db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("clear_stock_trades error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/trades/{trade_id}")
async def edit_trade(trade_id: int, req: TradeEditRequest):
    """编辑交易记录（手动修正）"""
    try:
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="未找到交易记录")
            trade = dict(row)

            # 用新值覆盖旧值
            price = req.price if req.price is not None else trade["price"]
            shares = req.shares if req.shares is not None else trade["shares"]
            commission = req.commission if req.commission is not None else trade["commission"]
            stamp_tax = req.stamp_tax if req.stamp_tax is not None else trade["stamp_tax"]
            transfer_fee = req.transfer_fee if req.transfer_fee is not None else trade["transfer_fee"]
            notes = req.notes if req.notes is not None else trade.get("notes", "")
            direction = req.direction if req.direction is not None else trade["direction"]

            amount = round(price * shares, 2)
            total_cost = round(amount + commission + stamp_tax + transfer_fee, 2)

            await db.execute(
                "UPDATE trades SET direction=?, price=?, shares=?, amount=?, commission=?, stamp_tax=?, transfer_fee=?, total_cost=?, notes=? WHERE id=?",
                (direction, price, shares, amount, commission, stamp_tax, transfer_fee, total_cost, notes, trade_id)
            )
            await db.commit()
            result = await _recalc_portfolio(db, trade["code"])
            return {"status": "ok", "trade_id": trade_id, "portfolio": result}
        finally:
            await db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("edit_trade error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 持仓 API ─────────────────────────────────────────────
@router.get("/portfolio")
async def get_portfolio(account_id: Optional[str] = Query(None)):
    """获取持仓列表（含实时行情+盈亏）"""
    try:
        db = await get_db()
        try:
            if account_id:
                cursor = await db.execute("SELECT * FROM portfolio WHERE total_shares > 0 AND account_id = ?", (account_id,))
            else:
                cursor = await db.execute("SELECT * FROM portfolio WHERE total_shares > 0")
            rows = await cursor.fetchall()
            positions = [dict(r) for r in rows]
        finally:
            await db.close()
        if positions:
            codes = [p["code"] for p in positions]
            quotes = await get_batch_quotes(codes)
            for p in positions:
                q = quotes.get(p["code"], {})
                p["price"] = q.get("price", 0)
                p["prev_close"] = q.get("prev_close", 0)
                p["change_pct"] = q.get("change_pct", 0)
                p["name"] = q.get("name", p.get("name", ""))
                if p["avg_cost"] and p["price"]:
                    p["unrealized_pnl"] = round((p["price"] - p["avg_cost"]) * p["total_shares"], 2)
                    p["unrealized_pnl_pct"] = round((p["price"] - p["avg_cost"]) / p["avg_cost"] * 100, 2)
                if p["prev_close"] and p["price"]:
                    p["daily_pnl"] = round((p["price"] - p["prev_close"]) * p["total_shares"], 2)
        return {"count": len(positions), "positions": positions}
    except Exception as e:
        logger.error("get_portfolio error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 资产概览 ─────────────────────────────────────────────
@router.get("/portfolio/overview")
async def get_portfolio_overview(account_id: Optional[str] = Query(None)):
    """资产概览（总资产/持仓市值/今日盈亏/浮动盈亏 + 费用统计）"""
    try:
        db = await get_db()
        try:
            if account_id:
                cursor = await db.execute("SELECT * FROM portfolio WHERE total_shares > 0 AND account_id = ?", (account_id,))
            else:
                cursor = await db.execute("SELECT * FROM portfolio WHERE total_shares > 0")
            rows = await cursor.fetchall()
            positions = [dict(r) for r in rows]
            # 可用资金（从settings读取，默认132516）
            cursor2 = await db.execute("SELECT value FROM settings WHERE key = 'cash_balance'")
            row = await cursor2.fetchone()
            cash = float(row[0]) if row else 132516.0
            # 交易费用统计
            cursor3 = await db.execute(
                "SELECT COALESCE(SUM(commission), 0) as total_commission, COALESCE(SUM(stamp_tax), 0) as total_stamp_tax FROM trades"
            )
            fee_row = await cursor3.fetchone()
            total_commission = float(fee_row["total_commission"]) if fee_row else 0
            total_stamp_tax = float(fee_row["total_stamp_tax"]) if fee_row else 0
        finally:
            await db.close()
        
        total_market_value = 0
        total_cost = 0
        total_daily_pnl = 0
        total_unrealized_pnl = 0
        
        if positions:
            codes = [p["code"] for p in positions]
            quotes = await get_batch_quotes(codes)
            for p in positions:
                q = quotes.get(p["code"], {})
                price = q.get("price", 0)
                prev_close = q.get("prev_close", 0)
                shares = p["total_shares"]
                avg_cost = p["avg_cost"]
                
                market_value = price * shares
                cost_value = avg_cost * shares
                daily_pnl = (price - prev_close) * shares if price and prev_close else 0
                unrealized_pnl = (price - avg_cost) * shares if price and avg_cost else 0
                
                total_market_value += market_value
                total_cost += cost_value
                total_daily_pnl += daily_pnl
                total_unrealized_pnl += unrealized_pnl
        
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
            "total_commission": round(total_commission, 2),
            "total_stamp_tax": round(total_stamp_tax, 2),
        }
    except Exception as e:
        logger.error("get_portfolio_overview error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 盈亏日历 ─────────────────────────────────────────────
@router.get("/pnl/calendar")
async def get_pnl_calendar(year: int = Query(None), month: int = Query(None), code: Optional[str] = Query(None)):
    """盈亏日历数据，支持 ?code=XXXXXX 按个股筛选"""
    try:
        now = datetime.now()
        y = year or now.year
        m = month or now.month
        
        db = await get_db()
        try:
            if code:
                # Per-stock breakdown for a specific stock
                code6 = code[:6]
                cursor = await db.execute(
                    "SELECT date, code6, pnl, close_price, shares, total_pnl FROM daily_pnl "
                    "WHERE strftime('%Y', date) = ? AND strftime('%m', date) = ? AND code6 = ? ORDER BY date",
                    (str(y), f"{m:02d}", code6)
                )
                rows = await cursor.fetchall()
                days = []
                for r in rows:
                    d = dict(r)
                    d["stock_pnl"] = d.pop("pnl", None)
                    days.append(d)
            else:
                # Aggregate view: per-date totals + per-stock breakdown
                cursor = await db.execute(
                    "SELECT date, code6, pnl, close_price, shares, total_pnl FROM daily_pnl "
                    "WHERE strftime('%Y', date) = ? AND strftime('%m', date) = ? ORDER BY date",
                    (str(y), f"{m:02d}")
                )
                rows = await cursor.fetchall()
                # Group by date
                date_map: dict = {}
                for r in rows:
                    r = dict(r)
                    d = r["date"]
                    if d not in date_map:
                        date_map[d] = {
                            "date": d,
                            "total_pnl": r.get("total_pnl") or 0,
                            "stocks": [],
                        }
                    if r.get("code6"):
                        date_map[d]["stocks"].append({
                            "code6": r["code6"],
                            "pnl": r.get("pnl"),
                            "close_price": r.get("close_price"),
                            "shares": r.get("shares"),
                        })
                        # Sum per-stock pnl into total if total_pnl is None
                        if r.get("pnl") and not r.get("total_pnl"):
                            date_map[d]["total_pnl"] += r["pnl"]
                days = list(date_map.values())
        finally:
            await db.close()
        
        # 计算月度统计
        total_pnl = sum(d.get("total_pnl") or 0 for d in days)
        win_days = sum(1 for d in days if (d.get("total_pnl") or 0) > 0)
        loss_days = sum(1 for d in days if (d.get("total_pnl") or 0) < 0)
        trade_days = win_days + loss_days
        win_rate = round(win_days / trade_days * 100, 1) if trade_days > 0 else 0
        
        return {
            "year": y,
            "month": m,
            "code": code,
            "days": days,
            "total_pnl": round(total_pnl, 2),
            "win_days": win_days,
            "loss_days": loss_days,
            "trade_days": trade_days,
            "win_rate": win_rate
        }
    except Exception as e:
        logger.error("get_pnl_calendar error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# -- Conditional Orders --
class ConditionalOrderRequest(BaseModel):
    code: str
    name: str = ""
    condition_type: str  # price_lte, price_gte, change_pct_gte, change_pct_lte
    target_price: float
    action: str  # buy, sell
    shares: int = 0
    notes: str = ""
    expires_at: Optional[str] = None


@router.get("/orders")
async def get_conditional_orders(status: Optional[str] = None, account_id: Optional[str] = Query(None)):
    """get conditional orders"""
    try:
        db = await get_db()
        try:
            conditions = []
            params = []
            if status:
                conditions.append("status = ?")
                params.append(status)
            if account_id:
                conditions.append("account_id = ?")
                params.append(account_id)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            cursor = await db.execute(f"SELECT * FROM conditional_orders{where} ORDER BY created_at DESC", params)
            rows = await cursor.fetchall()
            orders = [dict(r) for r in rows]
        finally:
            await db.close()
        
        # enrich with realtime price
        if orders:
            codes = list(set(o["code"] for o in orders))
            quotes = await get_batch_quotes(codes)
            for o in orders:
                q = quotes.get(o["code"], {})
                o["current_price"] = q.get("price", 0)
                o["change_pct"] = q.get("change_pct", 0)
                # calc distance to trigger
                if o["current_price"] and o["target_price"]:
                    if o["condition_type"] in ("price_lte", "change_pct_lte"):
                        o["distance_pct"] = round((o["target_price"] - o["current_price"]) / o["current_price"] * 100, 2)
                    else:
                        o["distance_pct"] = round((o["target_price"] - o["current_price"]) / o["current_price"] * 100, 2)
        
        return {"count": len(orders), "orders": orders}
    except Exception as e:
        logger.error("get_conditional_orders error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/orders")
async def create_conditional_order(req: ConditionalOrderRequest):
    """create conditional order"""
    try:
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO conditional_orders (code, name, condition_type, target_price, action, shares, notes, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (req.code, req.name, req.condition_type, req.target_price, req.action, req.shares, req.notes, req.expires_at)
            )
            await db.commit()
            cursor = await db.execute("SELECT last_insert_rowid()")
            row = await cursor.fetchone()
            return {"status": "ok", "id": row[0]}
        finally:
            await db.close()
    except Exception as e:
        logger.error("create_conditional_order error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/orders/{order_id}")
async def cancel_conditional_order(order_id: int):
    """cancel conditional order"""
    try:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE conditional_orders SET status = 'cancelled' WHERE id = ?", (order_id,)
            )
            await db.commit()
            return {"status": "ok", "id": order_id}
        finally:
            await db.close()
    except Exception as e:
        logger.error("cancel_conditional_order error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 待持仓 (Pending Positions) CRUD ──────────────────────
class PendingPositionRequest(BaseModel):
    code: str
    name: str = ""
    target_buy_price: Optional[float] = None
    plan_shares: int = 100
    plan_total_cost: Optional[float] = None
    reason: str = ""
    strategy_state: str = "watch"


@router.get("/pending-positions")
async def get_pending_positions(account_id: Optional[str] = Query(None)):
    """获取待持仓列表"""
    try:
        db = await get_db()
        try:
            if account_id:
                cursor = await db.execute("SELECT * FROM pending_positions WHERE account_id = ? ORDER BY created_at DESC", (account_id,))
            else:
                cursor = await db.execute("SELECT * FROM pending_positions ORDER BY created_at DESC")
            rows = await cursor.fetchall()
            positions = [dict(r) for r in rows]
        finally:
            await db.close()
        # 获取实时行情
        if positions:
            codes = list(set(p["code"] for p in positions))
            quotes = await get_batch_quotes(codes)
            for p in positions:
                q = quotes.get(p["code"], {})
                p["current_price"] = q.get("price", 0)
                p["change_pct"] = q.get("change_pct", 0)
                if p.get("target_buy_price") and p["current_price"]:
                    p["distance_pct"] = round(
                        (p["current_price"] - p["target_buy_price"]) / p["target_buy_price"] * 100, 2
                    )
                else:
                    p["distance_pct"] = None
        return {"count": len(positions), "positions": positions}
    except Exception as e:
        logger.error("get_pending_positions error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pending-positions")
async def add_pending_position(req: PendingPositionRequest):
    """添加待持仓"""
    try:
        db = await get_db()
        try:
            plan_total_cost = req.plan_total_cost
            if plan_total_cost is None and req.target_buy_price and req.plan_shares:
                plan_total_cost = round(req.target_buy_price * req.plan_shares, 2)
            await db.execute(
                "INSERT INTO pending_positions (code, name, target_buy_price, plan_shares, plan_total_cost, reason, strategy_state) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (req.code, req.name, req.target_buy_price, req.plan_shares, plan_total_cost, req.reason, req.strategy_state)
            )
            await db.commit()
            cursor = await db.execute("SELECT last_insert_rowid()")
            row = await cursor.fetchone()
            return {"status": "ok", "id": row[0]}
        finally:
            await db.close()
    except Exception as e:
        logger.error("add_pending_position error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/pending-positions/{pid}")
async def update_pending_position(pid: int, req: PendingPositionRequest):
    """更新待持仓"""
    try:
        db = await get_db()
        try:
            plan_total_cost = req.plan_total_cost
            if plan_total_cost is None and req.target_buy_price and req.plan_shares:
                plan_total_cost = round(req.target_buy_price * req.plan_shares, 2)
            cursor = await db.execute(
                "UPDATE pending_positions SET code=?, name=?, target_buy_price=?, plan_shares=?, plan_total_cost=?, reason=?, strategy_state=? WHERE id=?",
                (req.code, req.name, req.target_buy_price, req.plan_shares, plan_total_cost, req.reason, req.strategy_state, pid)
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="未找到待持仓记录")
            return {"status": "ok", "id": pid}
        finally:
            await db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_pending_position error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/pending-positions/{pid}")
async def delete_pending_position(pid: int):
    """删除待持仓"""
    try:
        db = await get_db()
        try:
            cursor = await db.execute("DELETE FROM pending_positions WHERE id=?", (pid,))
            await db.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="未找到待持仓记录")
            return {"status": "ok", "id": pid}
        finally:
            await db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_pending_position error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Buy Points CRUD ─────────────────────────────────────────
class BuyPointRequest(BaseModel):
    code: str
    price: float
    shares: int = 0
    reason: str = ""
    status: str = "pending"


@router.get("/buy-points/{code}")
async def get_buy_points(code: str):
    """获取某只股票的买点列表"""
    try:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM buy_points WHERE code = ? ORDER BY created_at DESC", (code,)
            )
            rows = await cursor.fetchall()
            return {"code": code, "buy_points": [dict(r) for r in rows]}
        finally:
            await db.close()
    except Exception as e:
        logger.error("get_buy_points error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/buy-points/{code}")
async def add_buy_point(code: str, req: BuyPointRequest):
    """添加买点"""
    try:
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO buy_points (code, price, shares, reason, status) VALUES (?, ?, ?, ?, ?)",
                (code, req.price, req.shares, req.reason, req.status),
            )
            await db.commit()
            cursor = await db.execute("SELECT last_insert_rowid()")
            row = await cursor.fetchone()
            return {"status": "ok", "id": row[0]}
        finally:
            await db.close()
    except Exception as e:
        logger.error("add_buy_point error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/buy-points/{point_id}")
async def delete_buy_point(point_id: int):
    """删除买点"""
    try:
        db = await get_db()
        try:
            await db.execute("DELETE FROM buy_points WHERE id = ?", (point_id,))
            await db.commit()
            return {"status": "ok", "id": point_id}
        finally:
            await db.close()
    except Exception as e:
        logger.error("delete_buy_point error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Calendar Day Detail ─────────────────────────────────────
@router.get("/pnl/calendar/day/{date}")
async def get_pnl_day_detail(date: str):
    """获取某日各股票盈亏明细"""
    try:
        db = await get_db()
        try:
            # 查当日交易记录
            cursor = await db.execute(
                "SELECT code, name, direction, price, shares, amount FROM trades "
                "WHERE date(trade_time) = ? ORDER BY trade_time ASC",
                (date,),
            )
            trades = [dict(r) for r in await cursor.fetchall()]

            # 查 daily_pnl 表
            cursor2 = await db.execute(
                "SELECT * FROM daily_pnl WHERE date = ?", (date,)
            )
            pnl_row = await cursor2.fetchone()
            daily_pnl = dict(pnl_row) if pnl_row else None
        finally:
            await db.close()

        # 计算各股票当日盈亏
        stock_pnl = {}
        for t in trades:
            code = t["code"]
            if code not in stock_pnl:
                stock_pnl[code] = {"code": code, "name": t["name"], "trades": [], "amount": 0}
            stock_pnl[code]["trades"].append(t)
            if t["direction"] == "buy":
                stock_pnl[code]["amount"] -= t["amount"]
            else:
                stock_pnl[code]["amount"] += t["amount"]

        return {
            "date": date,
            "daily_pnl": daily_pnl,
            "stock_pnl": list(stock_pnl.values()),
            "trades": trades,
        }
    except Exception as e:
        logger.error("get_pnl_day_detail error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# Trading Plans (合并 条件单 + 待持仓)
# ══════════════════════════════════════════════════════════════

class TradingPlanRequest(BaseModel):
    code: str
    name: str = ""
    direction: str = "buy"           # buy / sell
    plan_type: str = "watch"         # watch / near_target / conditional
    target_price: Optional[float] = None
    condition_type: str = "price_lte"  # price_lte / price_gte / change_pct_gte / change_pct_lte
    plan_shares: int = 100
    plan_total_cost: Optional[float] = None
    reason: str = ""
    status: str = "pending"
    expires_at: Optional[str] = None


@router.get("/trading-plans")
async def get_trading_plans(status: Optional[str] = None, account_id: Optional[str] = Query(None)):
    """获取交易计划列表"""
    try:
        db = await get_db()
        try:
            conditions = []
            params = []
            if status:
                conditions.append("status = ?")
                params.append(status)
            if account_id:
                conditions.append("account_id = ?")
                params.append(account_id)
            where = " WHERE " + " AND ".join(conditions) if conditions else ""
            cursor = await db.execute(f"SELECT * FROM trading_plans{where} ORDER BY created_at DESC", params)
            rows = await cursor.fetchall()
            plans = [dict(r) for r in rows]
        finally:
            await db.close()

        # enrich with realtime price
        if plans:
            codes = list(set(p["code"] for p in plans))
            quotes = await get_batch_quotes(codes)
            for p in plans:
                q = quotes.get(p["code"], {})
                p["current_price"] = q.get("price", 0)
                p["change_pct"] = q.get("change_pct", 0)
                if p["current_price"] and p.get("target_price"):
                    p["distance_pct"] = round(
                        (p["current_price"] - p["target_price"]) / p["target_price"] * 100, 2
                    )
                else:
                    p["distance_pct"] = None

        return {"count": len(plans), "plans": plans}
    except Exception as e:
        logger.error("get_trading_plans error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trading-plans")
async def create_trading_plan(req: TradingPlanRequest):
    """创建交易计划"""
    try:
        db = await get_db()
        try:
            plan_total_cost = req.plan_total_cost
            if plan_total_cost is None and req.target_price and req.plan_shares:
                plan_total_cost = round(req.target_price * req.plan_shares, 2)
            await db.execute(
                "INSERT INTO trading_plans (code, name, direction, plan_type, target_price, condition_type, plan_shares, plan_total_cost, reason, status, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (req.code, req.name, req.direction, req.plan_type, req.target_price, req.condition_type, req.plan_shares, plan_total_cost, req.reason, req.status, req.expires_at)
            )
            await db.commit()
            cursor = await db.execute("SELECT last_insert_rowid()")
            row = await cursor.fetchone()
            return {"status": "ok", "id": row[0]}
        finally:
            await db.close()
    except Exception as e:
        logger.error("create_trading_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/trading-plans/{pid}")
async def update_trading_plan(pid: int, req: TradingPlanRequest):
    """更新交易计划"""
    try:
        db = await get_db()
        try:
            plan_total_cost = req.plan_total_cost
            if plan_total_cost is None and req.target_price and req.plan_shares:
                plan_total_cost = round(req.target_price * req.plan_shares, 2)
            cursor = await db.execute(
                "UPDATE trading_plans SET code=?, name=?, direction=?, plan_type=?, target_price=?, condition_type=?, plan_shares=?, plan_total_cost=?, reason=?, status=?, expires_at=? WHERE id=?",
                (req.code, req.name, req.direction, req.plan_type, req.target_price, req.condition_type, req.plan_shares, plan_total_cost, req.reason, req.status, req.expires_at, pid)
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="未找到交易计划")
            return {"status": "ok", "id": pid}
        finally:
            await db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_trading_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trading-plans/{pid}")
async def delete_trading_plan(pid: int):
    """删除交易计划"""
    try:
        db = await get_db()
        try:
            cursor = await db.execute("DELETE FROM trading_plans WHERE id=?", (pid,))
            await db.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="未找到交易计划")
            return {"status": "ok", "id": pid}
        finally:
            await db.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_trading_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
