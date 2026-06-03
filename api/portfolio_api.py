"""持仓API - Phase 3 完整实现"""
import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from services import portfolio_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["持仓"])


# ── Account Management ──────────────────────────────────────
@router.get("/accounts")
async def list_accounts():
    """获取账户列表"""
    try:
        return await portfolio_service.list_accounts()
    except Exception as e:
        logger.error("list_accounts error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts")
async def create_account(request: Request):
    """创建账户"""
    body = await request.json()
    try:
        return await portfolio_service.create_account(
            name=body.get('name', ''),
            broker=body.get('broker', ''),
            account_id=body.get('id'),
        )
    except HTTPException:
        raise
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


class WatchlistImportMdRequest(BaseModel):
    content: str
    group_name: str = "默认"


class WatchlistBatchDeleteRequest(BaseModel):
    codes: list[str]


class TradeAddRequest(BaseModel):
    code: str
    name: str = ""
    direction: str = "buy"
    price: float
    shares: float
    commission: float = 0
    stamp_tax: float = 0
    transfer_fee: float = 0
    notes: str = ""
    trade_time: Optional[str] = None
    account_id: str = "default"


class TradeEditRequest(BaseModel):
    price: Optional[float] = None
    shares: Optional[float] = None
    commission: Optional[float] = None
    stamp_tax: Optional[float] = None
    transfer_fee: Optional[float] = None
    notes: Optional[str] = None
    direction: Optional[str] = None


class CashBalanceRequest(BaseModel):
    account_id: str = "default"
    balance: float
    notes: str = ""


# ── Watchlist ─────────────────────────────────────────────
@router.get("/watchlist")
async def get_watchlist():
    """自选股列表（含实时行情+盈亏）"""
    try:
        return await portfolio_service.get_watchlist()
    except Exception as e:
        logger.error("get_watchlist error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/watchlist")
async def add_to_watchlist(req: WatchlistAddRequest):
    """添加自选股"""
    try:
        return await portfolio_service.add_to_watchlist(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("add_to_watchlist(%s) error: %s", req.code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/watchlist/import-md")
async def import_watchlist_markdown(req: WatchlistImportMdRequest):
    """从 Markdown 文本批量导入自选股。"""
    try:
        return await portfolio_service.import_watchlist_markdown(req.content, req.group_name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("import_watchlist_markdown error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/watchlist/{code}")
async def remove_from_watchlist(code: str):
    """删除自选股"""
    try:
        return await portfolio_service.remove_from_watchlist(code)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("remove_from_watchlist(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/watchlist")
async def remove_watchlist_batch(req: WatchlistBatchDeleteRequest):
    """批量删除自选股。"""
    try:
        return await portfolio_service.remove_watchlist_batch(req.codes)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("remove_watchlist_batch error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


class WatchlistUpdateRequest(BaseModel):
    group_name: Optional[str] = None
    target_buy_price: Optional[float] = None
    target_sell_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    strategy_state: Optional[str] = None
    notes: Optional[str] = None


@router.put("/watchlist/{code}")
async def update_watchlist(code: str, req: WatchlistUpdateRequest):
    """更新自选股（目标价等）"""
    try:
        return await portfolio_service.update_watchlist(code, req)
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
        return await portfolio_service.reorder_watchlist(req)
    except Exception as e:
        logger.error("reorder_watchlist error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 交易记录 API ──────────────────────────────────────────
@router.get("/trades")
async def get_trades(code: Optional[str] = None, account_id: Optional[str] = Query(None)):
    """获取交易记录"""
    try:
        return await portfolio_service.get_trades(code, account_id)
    except Exception as e:
        logger.error("get_trades error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trades")
async def add_trade(req: TradeAddRequest):
    """录入交易（自动重算均价）"""
    try:
        return await portfolio_service.add_trade(req)
    except Exception as e:
        logger.error("add_trade error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades/stats/{code}")
async def get_trade_stats(code: str):
    """获取某只股票的交易统计（最低买入价、最近买入价）"""
    try:
        return await portfolio_service.get_trade_stats(code)
    except Exception as e:
        logger.error("get_trade_stats error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trades/{trade_id}")
async def delete_trade(trade_id: int):
    """删除单笔交易记录（撤销）"""
    try:
        return await portfolio_service.delete_trade(trade_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_trade error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trades/stock/{code}")
async def clear_stock_trades(code: str):
    """清空某只股票的所有交易记录"""
    try:
        return await portfolio_service.clear_stock_trades(code)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("clear_stock_trades error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/trades/{trade_id}")
async def edit_trade(trade_id: int, req: TradeEditRequest):
    """编辑交易记录（手动修正）"""
    try:
        return await portfolio_service.edit_trade(trade_id, req)
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
        return await portfolio_service.get_portfolio(account_id)
    except Exception as e:
        logger.error("get_portfolio error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 资产概览 ─────────────────────────────────────────────
@router.get("/portfolio/overview")
async def get_portfolio_overview(account_id: Optional[str] = Query(None)):
    """资产概览（总资产/持仓市值/今日盈亏/浮动盈亏 + 费用统计）"""
    try:
        return await portfolio_service.get_portfolio_overview(account_id)
    except Exception as e:
        logger.error("get_portfolio_overview error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/accounts/overview")
async def get_account_dashboard():
    """多账户资产概览：合并视图 + 账户对比"""
    try:
        return await portfolio_service.get_account_dashboard()
    except Exception as e:
        logger.error("get_account_dashboard error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/cash-ledger")
async def get_cash_ledger(account_id: Optional[str] = Query("default"), limit: int = Query(default=20, ge=1, le=100)):
    """现金流水：解释资产现金数据从哪里来"""
    try:
        return await portfolio_service.get_cash_ledger(account_id, limit)
    except Exception as e:
        logger.error("get_cash_ledger error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/portfolio/cash-balance")
async def set_cash_balance(req: CashBalanceRequest):
    """设置账户现金余额，并记录现金流水"""
    try:
        return await portfolio_service.set_cash_balance(req.account_id, req.balance, req.notes)
    except Exception as e:
        logger.error("set_cash_balance error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 盈亏日历 ─────────────────────────────────────────────
@router.get("/pnl/calendar")
async def get_pnl_calendar(year: int = Query(None), month: int = Query(None), code: Optional[str] = Query(None)):
    """盈亏日历数据，支持 ?code=XXXXXX 按个股筛选"""
    try:
        return await portfolio_service.get_pnl_calendar(year, month, code)
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
    shares: float = 0
    notes: str = ""
    expires_at: Optional[str] = None


@router.get("/orders")
async def get_conditional_orders(status: Optional[str] = None, account_id: Optional[str] = Query(None)):
    """get conditional orders"""
    try:
        return await portfolio_service.get_conditional_orders(status, account_id)
    except Exception as e:
        logger.error("get_conditional_orders error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/orders")
async def create_conditional_order(req: ConditionalOrderRequest):
    """create conditional order"""
    try:
        return await portfolio_service.create_conditional_order(req)
    except Exception as e:
        logger.error("create_conditional_order error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/orders/{order_id}")
async def cancel_conditional_order(order_id: int):
    """cancel conditional order"""
    try:
        return await portfolio_service.cancel_conditional_order(order_id)
    except Exception as e:
        logger.error("cancel_conditional_order error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 待持仓 (Pending Positions) CRUD ──────────────────────
class PendingPositionRequest(BaseModel):
    code: str
    name: str = ""
    target_buy_price: Optional[float] = None
    plan_shares: float = 100
    plan_total_cost: Optional[float] = None
    reason: str = ""
    strategy_state: str = "watch"


@router.get("/pending-positions")
async def get_pending_positions(account_id: Optional[str] = Query(None)):
    """获取待持仓列表"""
    try:
        return await portfolio_service.get_pending_positions(account_id)
    except Exception as e:
        logger.error("get_pending_positions error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pending-positions")
async def add_pending_position(req: PendingPositionRequest):
    """添加待持仓"""
    try:
        return await portfolio_service.add_pending_position(req)
    except Exception as e:
        logger.error("add_pending_position error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/pending-positions/{pid}")
async def update_pending_position(pid: int, req: PendingPositionRequest):
    """更新待持仓"""
    try:
        return await portfolio_service.update_pending_position(pid, req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_pending_position error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/pending-positions/{pid}")
async def delete_pending_position(pid: int):
    """删除待持仓"""
    try:
        return await portfolio_service.delete_pending_position(pid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_pending_position error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Buy Points CRUD ─────────────────────────────────────────
class BuyPointRequest(BaseModel):
    code: str
    price: float
    shares: float = 0
    reason: str = ""
    status: str = "pending"


@router.get("/buy-points/{code}")
async def get_buy_points(code: str):
    """获取某只股票的买点列表"""
    try:
        return await portfolio_service.get_buy_points(code)
    except Exception as e:
        logger.error("get_buy_points error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/buy-points/{code}")
async def add_buy_point(code: str, req: BuyPointRequest):
    """添加买点"""
    try:
        return await portfolio_service.add_buy_point(code, req)
    except Exception as e:
        logger.error("add_buy_point error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/buy-points/{point_id}")
async def delete_buy_point(point_id: int):
    """删除买点"""
    try:
        return await portfolio_service.delete_buy_point(point_id)
    except Exception as e:
        logger.error("delete_buy_point error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Calendar Day Detail ─────────────────────────────────────
@router.get("/pnl/calendar/day/{date}")
async def get_pnl_day_detail(date: str):
    """获取某日各股票盈亏明细"""
    try:
        return await portfolio_service.get_pnl_day_detail(date)
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
    plan_shares: float = 100
    plan_total_cost: Optional[float] = None
    reason: str = ""
    status: str = "pending"
    expires_at: Optional[str] = None


@router.get("/trading-plans")
async def get_trading_plans(status: Optional[str] = None, account_id: Optional[str] = Query(None)):
    """获取交易计划列表"""
    try:
        return await portfolio_service.get_trading_plans(status, account_id)
    except Exception as e:
        logger.error("get_trading_plans error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trading-plans")
async def create_trading_plan(req: TradingPlanRequest):
    """创建交易计划"""
    try:
        return await portfolio_service.create_trading_plan(req)
    except Exception as e:
        logger.error("create_trading_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/trading-plans/{pid}")
async def update_trading_plan(pid: int, req: TradingPlanRequest):
    """更新交易计划"""
    try:
        return await portfolio_service.update_trading_plan(pid, req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_trading_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trading-plans/{pid}")
async def delete_trading_plan(pid: int):
    """删除交易计划"""
    try:
        return await portfolio_service.delete_trading_plan(pid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_trading_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
