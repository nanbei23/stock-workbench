"""持仓API - Phase 3 完整实现"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from services import auth_service, portfolio_service, trade_memory_service, model_provider_resolver

logger = logging.getLogger(__name__)
router = APIRouter(tags=["持仓"])


class AccountUpdateRequest(BaseModel):
    name: Optional[str] = None
    broker: Optional[str] = None
    account_no_mask: Optional[str] = None
    notes: Optional[str] = None
    display_order: Optional[int] = None


# ── Account Management ──────────────────────────────────────
@router.get("/accounts")
async def list_accounts(user: dict = Depends(auth_service.require_login_user)):
    """获取账户列表"""
    try:
        return await portfolio_service.list_accounts(user.get("id"))
    except Exception as e:
        logger.error("list_accounts error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts")
async def create_account(request: Request, user: dict = Depends(auth_service.require_login_user)):
    """创建账户"""
    body = await request.json()
    try:
        return await portfolio_service.create_account(
            name=body.get('name', ''),
            broker=body.get('broker', ''),
            account_id=body.get('id'),
            login_user_id=user.get("id"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("create_account error: %s", e)
        return JSONResponse({'error': str(e)}, status_code=500)


@router.put("/accounts/{account_id}")
async def update_account(
    account_id: str,
    req: AccountUpdateRequest,
    user: dict = Depends(auth_service.require_login_user),
):
    await _owned_account_id(user, account_id)
    return await portfolio_service.update_account(account_id, user.get("id"), req.model_dump(exclude_none=True))


@router.delete("/accounts/{account_id}")
async def archive_account(account_id: str, user: dict = Depends(auth_service.require_login_user)):
    await _owned_account_id(user, account_id)
    return await portfolio_service.archive_account(account_id, user.get("id"))


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
    commission: Optional[float] = None
    stamp_tax: Optional[float] = None
    transfer_fee: Optional[float] = None
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


class PnlSnapshotRequest(BaseModel):
    date: Optional[str] = None
    account_id: Optional[str] = None


async def _owned_account_id(user: dict, account_id: str | None = None) -> str:
    return await auth_service.resolve_securities_account_id(user, account_id)


def _login_user_scope(user: dict) -> str | None:
    login_user_id = user.get("id") or "admin"
    if login_user_id == "admin":
        return None
    return login_user_id


class TradeMemoryDraftRequest(BaseModel):
    code: str
    account_id: str = "default"
    memory_key: Optional[str] = None


class TradeMemorySaveRequest(BaseModel):
    memory_key: str
    account_id: str = "default"
    code: str
    name: str = ""
    status: str = "draft"
    outcome: str = "neutral"
    trade_ids: list[int] = []
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None
    holding_days: float = 0
    buy_amount: float = 0
    sell_amount: float = 0
    fees: float = 0
    realized_pnl: float = 0
    realized_pnl_pct: float = 0
    summary: str = ""
    facts: dict = {}
    lesson_tags: list[str] = []
    rules: list[str] = []
    veto_lessons: list[str] = []
    report_context: dict = {}


class TradeMemoryRelatedRequest(BaseModel):
    code: Optional[str] = None
    scenario_tags: list[str] = []
    report_text: str = ""
    account_id: str = "default"
    limit: int = 6


class TradeMemoryEmbeddingBackfillRequest(BaseModel):
    account_id: str = "default"
    limit: int = 200


class TradeMemoryEmbeddingTestRequest(BaseModel):
    provider_id: str = ""
    api_key: str = ""
    endpoint: str = "https://api.openai.com/v1/embeddings"
    model: str = "text-embedding-3-small"
    dimensions: int = 1536


# ── Watchlist ─────────────────────────────────────────────
@router.get("/watchlist")
async def get_watchlist(user: dict = Depends(auth_service.require_login_user)):
    """自选股列表（含实时行情+盈亏）"""
    try:
        scope = _login_user_scope(user)
        return await portfolio_service.get_watchlist(scope) if scope else await portfolio_service.get_watchlist()
    except Exception as e:
        logger.error("get_watchlist error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/watchlist")
async def add_to_watchlist(req: WatchlistAddRequest, user: dict = Depends(auth_service.require_login_user)):
    """添加自选股"""
    try:
        scope = _login_user_scope(user)
        return await portfolio_service.add_to_watchlist(req, scope) if scope else await portfolio_service.add_to_watchlist(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("add_to_watchlist(%s) error: %s", req.code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/watchlist/import-md")
async def import_watchlist_markdown(req: WatchlistImportMdRequest, user: dict = Depends(auth_service.require_login_user)):
    """从 Markdown 文本批量导入自选股。"""
    try:
        scope = _login_user_scope(user)
        if scope:
            return await portfolio_service.import_watchlist_markdown(req.content, req.group_name, scope)
        return await portfolio_service.import_watchlist_markdown(req.content, req.group_name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("import_watchlist_markdown error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/watchlist/{code}")
async def remove_from_watchlist(code: str, user: dict = Depends(auth_service.require_login_user)):
    """删除自选股"""
    try:
        scope = _login_user_scope(user)
        return await portfolio_service.remove_from_watchlist(code, scope) if scope else await portfolio_service.remove_from_watchlist(code)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("remove_from_watchlist(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/watchlist")
async def remove_watchlist_batch(req: WatchlistBatchDeleteRequest, user: dict = Depends(auth_service.require_login_user)):
    """批量删除自选股。"""
    try:
        scope = _login_user_scope(user)
        if scope:
            return await portfolio_service.remove_watchlist_batch(req.codes, scope)
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
async def update_watchlist(code: str, req: WatchlistUpdateRequest, user: dict = Depends(auth_service.require_login_user)):
    """更新自选股（目标价等）"""
    try:
        scope = _login_user_scope(user)
        return await portfolio_service.update_watchlist(code, req, scope) if scope else await portfolio_service.update_watchlist(code, req)
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
async def reorder_watchlist(req: ReorderRequest, user: dict = Depends(auth_service.require_login_user)):
    """批量更新自选股排序"""
    try:
        scope = _login_user_scope(user)
        return await portfolio_service.reorder_watchlist(req, scope) if scope else await portfolio_service.reorder_watchlist(req)
    except Exception as e:
        logger.error("reorder_watchlist error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 交易记录 API ──────────────────────────────────────────
@router.get("/trades")
async def get_trades(
    code: Optional[str] = None,
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """获取交易记录"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.get_trades(code, aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_trades error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trades")
async def add_trade(req: TradeAddRequest, user: dict = Depends(auth_service.require_login_user)):
    """录入交易（自动重算均价）"""
    try:
        req.account_id = await _owned_account_id(user, req.account_id)
        return await portfolio_service.add_trade(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("add_trade error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades/stats/{code}")
async def get_trade_stats(
    code: str,
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """获取某只股票的交易统计（最低买入价、最近买入价）"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.get_trade_stats(code, aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_trade_stats error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trades/{trade_id}")
async def delete_trade(
    trade_id: int,
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """删除单笔交易记录（撤销）"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.delete_trade(trade_id, aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_trade error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trades/stock/{code}")
async def clear_stock_trades(
    code: str,
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """清空某只股票的所有交易记录"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.clear_stock_trades(code, aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("clear_stock_trades error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/trades/{trade_id}")
async def edit_trade(
    trade_id: int,
    req: TradeEditRequest,
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """编辑交易记录（手动修正）"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.edit_trade(trade_id, req, aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("edit_trade error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 交易复盘记忆 API ──────────────────────────────────────
@router.get("/trade-memories/candidates")
async def list_trade_memory_candidates(
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """列出已清仓、可生成复盘记忆的交易闭环。"""
    try:
        aid = await _owned_account_id(user, account_id)
        return trade_memory_service.list_closed_trade_candidates(account_id=aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("list_trade_memory_candidates error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trade-memories")
async def list_trade_memories(
    status: Optional[str] = None,
    code: Optional[str] = None,
    account_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(auth_service.require_login_user),
):
    """列出交易复盘记忆卡。"""
    try:
        aid = await _owned_account_id(user, account_id)
        return trade_memory_service.list_trade_memories(status=status, code=code, account_id=aid, limit=limit)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("list_trade_memories error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trade-memories/draft")
async def generate_trade_memory_draft(req: TradeMemoryDraftRequest, user: dict = Depends(auth_service.require_login_user)):
    """基于已清仓交易闭环生成复盘记忆草稿。"""
    try:
        req.account_id = await _owned_account_id(user, req.account_id)
        return trade_memory_service.generate_memory_draft(req.code, account_id=req.account_id, memory_key=req.memory_key)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("generate_trade_memory_draft error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trade-memories")
async def save_trade_memory(req: TradeMemorySaveRequest, user: dict = Depends(auth_service.require_login_user)):
    """保存交易复盘记忆；status=active 后进入后续报告上下文。"""
    try:
        req.account_id = await _owned_account_id(user, req.account_id)
        return trade_memory_service.save_trade_memory(req.model_dump())
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("save_trade_memory error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trade-memories/context")
async def get_trade_memory_context(
    code: Optional[str] = None,
    report_text: Optional[str] = None,
    account_id: Optional[str] = Query(None),
    limit: int = Query(6, ge=1, le=20),
    user: dict = Depends(auth_service.require_login_user),
):
    """预览会注入 AI 报告的交易复盘记忆上下文。"""
    try:
        aid = await _owned_account_id(user, account_id)
        context = trade_memory_service.trade_memory_context(code=code, report_text=report_text, account_id=aid, limit=limit)
        return {"context": context, "enabled": bool(context)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_trade_memory_context error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trade-memories/related")
async def related_trade_memories(req: TradeMemoryRelatedRequest, user: dict = Depends(auth_service.require_login_user)):
    """按标的和报告文本检索最相关的交易复盘记忆。"""
    try:
        req.account_id = await _owned_account_id(user, req.account_id)
        return trade_memory_service.related_trade_memories(
            code=req.code,
            scenario_tags=req.scenario_tags,
            report_text=req.report_text,
            account_id=req.account_id,
            limit=req.limit,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("related_trade_memories error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trade-memories/embeddings/backfill")
async def backfill_trade_memory_embeddings(req: TradeMemoryEmbeddingBackfillRequest, user: dict = Depends(auth_service.require_login_user)):
    """用 OpenAI embedding 补齐 active 交易记忆的 sqlite-vec 向量索引。"""
    try:
        req.account_id = await _owned_account_id(user, req.account_id)
        return trade_memory_service.backfill_trade_memory_embeddings(
            account_id=req.account_id or "default",
            limit=max(1, min(int(req.limit or 200), 200)),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("backfill_trade_memory_embeddings error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trade-memories/embeddings/status")
async def get_trade_memory_embedding_status(
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """查看 active 交易复盘记忆的 sqlite-vec 索引覆盖率。"""
    try:
        aid = await _owned_account_id(user, account_id)
        return trade_memory_service.trade_memory_embedding_status(account_id=aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_trade_memory_embedding_status error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trade-memories/embeddings/test-connection")
async def test_trade_memory_embedding_connection(req: TradeMemoryEmbeddingTestRequest):
    """测试 OpenAI embedding 连接，返回结构化诊断错误。"""
    try:
        if req.provider_id:
            provider = model_provider_resolver.provider_by_id(req.provider_id)
            if not provider:
                return {"status": "error", "message": "模型配置不存在", "error_type": "missing_provider"}
            req.api_key = provider.get("api_key") or ""
            req.endpoint = provider.get("base_url") or req.endpoint
            req.model = provider.get("embedding_model") or provider.get("default_model") or provider.get("quick_model") or req.model
            req.dimensions = int(provider.get("embedding_dimensions") or req.dimensions)
        return trade_memory_service.test_embedding_connection(
            api_key=req.api_key,
            endpoint=req.endpoint,
            model=req.model,
            dimensions=req.dimensions,
        )
    except Exception as e:
        logger.error("test_trade_memory_embedding_connection error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trade-memories/injection-map")
async def get_trade_memory_injection_map():
    """列出交易复盘记忆进入 AI 上下文的覆盖点和约束。"""
    return trade_memory_service.context_injection_status()


# ── 持仓 API ─────────────────────────────────────────────
@router.get("/portfolio")
async def get_portfolio(
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """获取持仓列表（含实时行情+盈亏）"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.get_portfolio(aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_portfolio error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 资产概览 ─────────────────────────────────────────────
@router.get("/portfolio/overview")
async def get_portfolio_overview(
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """资产概览（总资产/持仓市值/今日盈亏/浮动盈亏 + 费用统计）"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.get_portfolio_overview(aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_portfolio_overview error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/accounts/overview")
async def get_account_dashboard(user: dict = Depends(auth_service.require_login_user)):
    """多账户资产概览：合并视图 + 账户对比"""
    try:
        return await portfolio_service.get_account_dashboard(user.get("id"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_account_dashboard error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/cash-ledger")
async def get_cash_ledger(
    account_id: Optional[str] = Query(None),
    limit: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(auth_service.require_login_user),
):
    """现金流水：解释资产现金数据从哪里来"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.get_cash_ledger(aid, limit)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_cash_ledger error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/portfolio/cash-balance")
async def set_cash_balance(req: CashBalanceRequest, user: dict = Depends(auth_service.require_login_user)):
    """设置账户现金余额，并记录现金流水"""
    try:
        req.account_id = await _owned_account_id(user, req.account_id)
        return await portfolio_service.set_cash_balance(req.account_id, req.balance, req.notes)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("set_cash_balance error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── 持仓盈亏日历 ─────────────────────────────────────────
@router.get("/pnl/calendar")
async def get_pnl_calendar(
    year: int = Query(None),
    month: int = Query(None),
    code: Optional[str] = Query(None),
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """持仓盈亏日历数据，支持 ?code=XXXXXX 按个股筛选"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.get_pnl_calendar(year, month, code, aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_pnl_calendar error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pnl/calendar/snapshot")
async def create_pnl_calendar_snapshot(req: PnlSnapshotRequest, user: dict = Depends(auth_service.require_login_user)):
    """手动补写某日持仓盈亏日历快照。"""
    try:
        req.account_id = await _owned_account_id(user, req.account_id)
        return await portfolio_service.ensure_daily_pnl_snapshot(req.date, req.account_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("create_pnl_calendar_snapshot error: %s", e)
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
    account_id: str = "default"


@router.get("/pending-positions")
async def get_pending_positions(
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """获取待持仓列表"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.get_pending_positions(aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_pending_positions error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/pending-positions")
async def add_pending_position(req: PendingPositionRequest, user: dict = Depends(auth_service.require_login_user)):
    """添加待持仓"""
    try:
        req.account_id = await _owned_account_id(user, req.account_id)
        return await portfolio_service.add_pending_position(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("add_pending_position error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/pending-positions/{pid}")
async def update_pending_position(pid: int, req: PendingPositionRequest, user: dict = Depends(auth_service.require_login_user)):
    """更新待持仓"""
    try:
        req.account_id = await _owned_account_id(user, req.account_id)
        return await portfolio_service.update_pending_position(pid, req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_pending_position error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/pending-positions/{pid}")
async def delete_pending_position(
    pid: int,
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """删除待持仓"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.delete_pending_position(pid, aid)
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
async def get_pnl_day_detail(
    date: str,
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """获取某日各股票持仓盈亏明细"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.get_pnl_day_detail(date, aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_pnl_day_detail error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════
# Trading Plans
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
    account_id: str = "default"


@router.get("/trading-plans")
async def get_trading_plans(
    status: Optional[str] = None,
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """获取交易计划列表"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.get_trading_plans(status, aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_trading_plans error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trading-plans")
async def create_trading_plan(req: TradingPlanRequest, user: dict = Depends(auth_service.require_login_user)):
    """创建交易计划"""
    try:
        req.account_id = await _owned_account_id(user, req.account_id)
        return await portfolio_service.create_trading_plan(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("create_trading_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/trading-plans/{pid}")
async def update_trading_plan(pid: int, req: TradingPlanRequest, user: dict = Depends(auth_service.require_login_user)):
    """更新交易计划"""
    try:
        req.account_id = await _owned_account_id(user, req.account_id)
        return await portfolio_service.update_trading_plan(pid, req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_trading_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trading-plans/{pid}")
async def delete_trading_plan(
    pid: int,
    account_id: Optional[str] = Query(None),
    user: dict = Depends(auth_service.require_login_user),
):
    """删除交易计划"""
    try:
        aid = await _owned_account_id(user, account_id)
        return await portfolio_service.delete_trading_plan(pid, aid)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_trading_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
