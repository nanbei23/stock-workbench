"""Portfolio business operations."""

import asyncio
import re
import uuid
import unicodedata
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from fastapi import HTTPException

from data.kline import get_tencent_history_kline
from data.quote import get_batch_quotes
from models.database import get_db
from repositories import portfolio_repository as repo


async def _with_db(fn):
    db = await get_db()
    try:
        return await fn(db)
    finally:
        await db.close()


def _daily_pnl_from_quote(quote: dict, shares) -> float:
    price = float(quote.get("price") or 0)
    total_shares = float(shares or 0)
    if not price or not total_shares:
        return 0.0

    change_pct = quote.get("change_pct")
    if change_pct is not None:
        pct = float(change_pct or 0)
        if pct != -100:
            return round(price * total_shares * pct / (100 + pct), 3)

    change = quote.get("change")
    if change is not None:
        return round(float(change or 0) * total_shares, 3)

    prev_close = float(quote.get("prev_close") or 0)
    return round((price - prev_close) * total_shares, 3) if prev_close else 0.0


def _enrich_position(position: dict, quote: dict):
    quote_price = float(quote.get("price") or 0)
    stored_price = float(position.get("current_price") or 0)
    avg_cost = float(position.get("avg_cost") or 0)
    if quote_price:
        price = quote_price
        valuation_source = "realtime_quote"
    elif stored_price:
        price = stored_price
        valuation_source = "stored_price"
    elif avg_cost:
        price = avg_cost
        valuation_source = "cost_fallback"
    else:
        price = 0.0
        valuation_source = "missing"
    position["price"] = price
    position["valuation_source"] = valuation_source
    position["prev_close"] = float(quote.get("prev_close") or 0)
    position["change_pct"] = float(quote.get("change_pct") or 0)
    position["name"] = quote.get("name") or position.get("name", "")
    position["market_value"] = round(position["price"] * float(position.get("total_shares") or 0), 3)
    position["unrealized_pnl"] = 0.0
    position["unrealized_pnl_pct"] = 0.0
    if position["avg_cost"] and position["price"]:
        position["unrealized_pnl"] = round(
            (position["price"] - position["avg_cost"]) * position["total_shares"], 3
        )
        position["unrealized_pnl_pct"] = round(
            (position["price"] - position["avg_cost"]) / position["avg_cost"] * 100, 3
        )
    position["daily_pnl"] = _daily_pnl_from_quote(quote, position["total_shares"])


def _summary_from_positions(positions: list[dict], cash_and_fees: dict) -> dict:
    total_market_value = sum(float(position.get("market_value") or 0) for position in positions)
    total_cost = sum(float(position.get("avg_cost") or 0) * float(position.get("total_shares") or 0) for position in positions)
    total_daily_pnl = sum(float(position.get("daily_pnl") or 0) for position in positions)
    total_unrealized_pnl = sum(float(position.get("unrealized_pnl") or 0) for position in positions)
    total_realized_pnl = float(cash_and_fees.get("realized_pnl") or 0)
    cash = float(cash_and_fees["cash"])
    total_assets = total_market_value + cash
    previous_market_value = total_market_value - total_daily_pnl
    total_lifetime_pnl = total_realized_pnl + total_unrealized_pnl
    return {
        "total_assets": round(total_assets, 3),
        "market_value": round(total_market_value, 3),
        "cash": round(cash, 3),
        "cash_source": cash_and_fees.get("cash_source") or "unset",
        "total_cost": round(total_cost, 3),
        "daily_pnl": round(total_daily_pnl, 3),
        "unrealized_pnl": round(total_unrealized_pnl, 3),
        "daily_pnl_pct": round(total_daily_pnl / previous_market_value * 100, 3) if previous_market_value else 0,
        "unrealized_pnl_pct": round(total_unrealized_pnl / total_cost * 100, 3) if total_cost else 0,
        "realized_pnl": round(total_realized_pnl, 3),
        "historical_pnl": round(total_realized_pnl, 3),
        "total_pnl": round(total_lifetime_pnl, 3),
        "total_commission": round(cash_and_fees["total_commission"], 3),
        "total_stamp_tax": round(cash_and_fees["total_stamp_tax"], 3),
    }


async def _trade_fee_settings(db) -> dict:
    rows = await db.execute_fetchall(
        "SELECT key, value FROM settings WHERE key IN (?, ?, ?, ?)",
        ("commission_rate", "commission_min", "stamp_tax_rate", "transfer_fee_rate"),
    )
    settings = {row["key"]: row["value"] for row in rows}

    def _num(key: str, default: float) -> float:
        try:
            return float(settings.get(key, default))
        except (TypeError, ValueError):
            return default

    return {
        "commission_rate": _num("commission_rate", 0.0003),
        "commission_min": _num("commission_min", 5),
        "stamp_tax_rate": _num("stamp_tax_rate", 0.0005),
        "transfer_fee_rate": _num("transfer_fee_rate", 0.00001),
    }


async def _apply_default_trade_fees(db, req):
    amount = round(float(req.price or 0) * float(req.shares or 0), 3)
    if amount <= 0:
        return req
    fees = await _trade_fee_settings(db)
    if getattr(req, "commission", None) is None:
        req.commission = round(max(amount * fees["commission_rate"], fees["commission_min"]), 3)
    if getattr(req, "stamp_tax", None) is None:
        req.stamp_tax = round(amount * fees["stamp_tax_rate"], 3) if str(req.direction).lower() == "sell" else 0
    if getattr(req, "transfer_fee", None) is None:
        req.transfer_fee = round(amount * fees["transfer_fee_rate"], 3)
    return req


def _realized_pnl_from_trades(trades: list[dict]) -> float:
    states = {}
    realized = 0.0
    sorted_trades = sorted(
        trades or [],
        key=lambda item: (
            str(item.get("account_id") or "default"),
            str(item.get("code") or ""),
            str(item.get("trade_time") or ""),
            int(item.get("id") or 0),
        ),
    )
    for trade in sorted_trades:
        account_id = str(trade.get("account_id") or "default")
        code = str(trade.get("code") or "")
        direction = str(trade.get("direction") or "").lower()
        shares = float(trade.get("shares") or 0)
        amount = float(trade.get("amount") or 0)
        fees = (
            float(trade.get("commission") or 0)
            + float(trade.get("stamp_tax") or 0)
            + float(trade.get("transfer_fee") or 0)
        )
        if not code or shares <= 0:
            continue
        state = states.setdefault((account_id, code), {"shares": 0.0, "cost": 0.0})
        if direction == "buy":
            state["shares"] += shares
            state["cost"] += amount + fees
        elif direction == "sell" and state["shares"] > 0:
            matched_shares = min(shares, state["shares"])
            avg_cost = state["cost"] / state["shares"] if state["shares"] else 0.0
            sell_fee_ratio = matched_shares / shares if shares else 0.0
            net_proceeds = amount * sell_fee_ratio - fees * sell_fee_ratio
            realized += net_proceeds - avg_cost * matched_shares
            state["shares"] = max(0.0, state["shares"] - matched_shares)
            state["cost"] = avg_cost * state["shares"]
    return round(realized, 3)


async def _portfolio_snapshot(account_id=None):
    async def _load(db):
        positions = await repo.fetch_positions(db, account_id)
        cash_and_fees = await repo.fetch_cash_and_fees(db, account_id)
        trades = await repo.fetch_trades(db, account_id=account_id)
        return positions, cash_and_fees, trades

    positions, cash_and_fees, trades = await _with_db(_load)
    cash_and_fees["realized_pnl"] = _realized_pnl_from_trades(trades)
    if positions:
        quotes = await get_batch_quotes([position["code"] for position in positions])
        for position in positions:
            _enrich_position(position, quotes.get(position["code"], {}))
    summary = _summary_from_positions(positions, cash_and_fees)
    total_assets = summary["total_assets"]
    for position in positions:
        position["weight_pct"] = round(float(position.get("market_value") or 0) / total_assets * 100, 3) if total_assets else 0
    positions.sort(key=lambda item: (-(float(item.get("market_value") or 0)), item.get("code") or ""))
    return positions, summary


async def list_accounts(login_user_id: str | None = None):
    async def _load(db):
        return await repo.fetch_accounts(db, login_user_id)

    return {"accounts": await _with_db(_load)}


async def create_account(name: str, broker: str = "", account_id: str | None = None, login_user_id: str | None = None):
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    aid = account_id or str(uuid.uuid4())[:8]

    async def _create(db):
        await repo.create_account(db, aid, name, broker, login_user_id or "admin")

    await _with_db(_create)
    return {"success": True, "id": aid}


async def update_account(account_id: str, login_user_id: str, values: dict):
    async def _update(db):
        rowcount = await repo.update_account(db, account_id, login_user_id or "admin", values)
        if rowcount == 0:
            raise HTTPException(status_code=404, detail="证券账户不存在")
        return {"success": True, "id": account_id}

    return await _with_db(_update)


async def archive_account(account_id: str, login_user_id: str):
    if account_id == "default":
        raise HTTPException(status_code=400, detail="默认证券账户不能删除")

    async def _archive(db):
        rowcount = await repo.archive_account(db, account_id, login_user_id or "admin")
        if rowcount == 0:
            raise HTTPException(status_code=404, detail="证券账户不存在")
        return {"success": True, "id": account_id}

    return await _with_db(_archive)


async def get_watchlist(login_user_id: str = "admin"):
    async def _load(db):
        stocks, portfolio_map = await repo.fetch_watchlist_and_positions(db, login_user_id)
        latest_reports = await repo.fetch_latest_report_map(db, [stock["code"] for stock in stocks], login_user_id)
        return stocks, portfolio_map, latest_reports

    stocks, portfolio_map, latest_reports = await _with_db(_load)
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
            stock["daily_pnl"] = _daily_pnl_from_quote(quote, stock["total_shares"])
            latest_report = latest_reports.get(stock["code"], {})
            stock["last_report_id"] = latest_report.get("id")
            stock["last_report_signal"] = latest_report.get("signal")
            stock["last_report_confidence"] = latest_report.get("confidence")
            stock["last_report_risk_score"] = latest_report.get("risk_score")
            stock["last_report_created_at"] = latest_report.get("created_at")
    return {"count": len(stocks), "stocks": stocks}


async def add_to_watchlist(req, login_user_id: str = "admin"):
    clean_code = _clean_watchlist_code(req.code)
    if not clean_code:
        raise HTTPException(status_code=400, detail="请输入 6 位股票代码")
    quote_map = await get_batch_quotes([clean_code])
    quote = quote_map.get(clean_code) or {}
    if not quote:
        raise HTTPException(status_code=400, detail=f"未找到股票 {clean_code} 的实时行情，请检查股票代码是否正确")
    input_name = _clean_watchlist_name(getattr(req, "name", "") or "")
    quote_name = _clean_watchlist_name(quote.get("name") or "")
    if input_name and quote_name and _normalize_stock_name(input_name) != _normalize_stock_name(quote_name):
        raise HTTPException(status_code=400, detail=f"股票名称与代码不匹配：{clean_code} 对应 {quote_name}，不是 {input_name}")

    req = SimpleNamespace(
        code=clean_code,
        name=quote_name or input_name or clean_code,
        group_name=getattr(req, "group_name", None) or "默认",
        strategy_state=getattr(req, "strategy_state", None) or "watch",
        target_buy_price=getattr(req, "target_buy_price", None),
        target_sell_price=getattr(req, "target_sell_price", None),
        stop_loss_price=getattr(req, "stop_loss_price", None),
        notes=getattr(req, "notes", "") or "",
    )

    async def _add(db):
        sort_order = await repo.next_watchlist_sort_order(db, login_user_id)
        return await repo.insert_watchlist_stock(db, req, sort_order, login_user_id)

    stock = await _with_db(_add)
    return {"status": "ok", "stock": stock}


def _clean_watchlist_code(value) -> str:
    code = re.sub(r"\D", "", str(value or "").strip())
    return code if re.fullmatch(r"\d{6}", code) else ""


def _normalize_stock_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = re.sub(r"\s+", "", text)
    return text.upper()


def _is_markdown_table_separator(cells):
    if not cells:
        return False
    return all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in cells if cell)


def _is_markdown_table_header(cells):
    labels = {cell.strip().lower() for cell in cells}
    return "代码" in labels and ("股票名称" in labels or "名称" in labels or "股票" in labels)


def _clean_watchlist_name(value: str):
    name = re.sub(r"^[\s#>*+\-•·\d.、\[\]xX]+", "", value or "")
    name = re.sub(r"[()（）【】\[\]{}]", " ", name)
    name = re.sub(r"[+|,，:：;；/\\]+", " ", name)
    name = re.sub(r"[*_`~]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def _parse_watchlist_table_row(line: str):
    if "|" not in line:
        return None
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    cells = [cell for cell in cells if cell]
    if _is_markdown_table_header(cells) or _is_markdown_table_separator(cells):
        return {"skip": True}

    code_idx = next((idx for idx, cell in enumerate(cells) if re.fullmatch(r"\d{6}", cell)), None)
    if code_idx is None:
        return None

    name_candidates = []
    if code_idx > 0:
        name_candidates.extend(cells[:code_idx])
    if code_idx + 1 < len(cells):
        name_candidates.extend(cells[code_idx + 1:])
    name = ""
    for candidate in reversed(name_candidates):
        cleaned = _clean_watchlist_name(candidate)
        if cleaned and not re.fullmatch(r"\d+", cleaned) and cleaned not in {"#", "序号", "代码"}:
            name = cleaned
            break
    return {"code": cells[code_idx], "name": name or cells[code_idx]}


def parse_watchlist_markdown(content: str):
    items = []
    invalid_lines = []
    seen = set()
    duplicates = 0
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        table_item = _parse_watchlist_table_row(line)
        if table_item and table_item.get("skip"):
            continue
        if table_item and table_item.get("code"):
            code = table_item["code"]
            if code in seen:
                duplicates += 1
                continue
            seen.add(code)
            items.append({"code": code, "name": table_item["name"]})
            continue
        match = re.search(r"(?<!\d)(\d{6})(?!\d)", line)
        if not match:
            invalid_lines.append(line)
            continue
        code = match.group(1)
        if code in seen:
            duplicates += 1
            continue
        seen.add(code)
        name = f"{line[:match.start()]} {line[match.end():]}"
        name = _clean_watchlist_name(name)
        items.append({"code": code, "name": name or code})
    return {"items": items, "duplicates": duplicates, "invalid_lines": invalid_lines}


async def import_watchlist_markdown(content: str, group_name: str = "默认", login_user_id: str = "admin"):
    parsed = parse_watchlist_markdown(content)
    items = [
        {"code": _clean_watchlist_code(item["code"]), "name": _clean_watchlist_name(item.get("name") or "")}
        for item in parsed["items"]
    ]
    items = [item for item in items if item["code"]]
    if not items:
        return {
            "status": "ok",
            "imported": 0,
            "duplicates": parsed["duplicates"],
            "invalid": len(parsed["invalid_lines"]),
            "items": [],
            "invalid_lines": parsed["invalid_lines"],
        }

    quotes = await get_batch_quotes([item["code"] for item in items])

    async def _import(db):
        existing = await repo.fetch_watchlist_codes(db, [item["code"] for item in items], login_user_id)
        sort_order = await repo.next_watchlist_sort_order(db, login_user_id)
        imported = []
        duplicate_count = parsed["duplicates"]
        invalid_lines = list(parsed["invalid_lines"])
        for item in items:
            if item["code"] in existing:
                duplicate_count += 1
                continue
            quote = quotes.get(item["code"]) or {}
            quote_name = _clean_watchlist_name(quote.get("name") or "")
            input_name = _clean_watchlist_name(item.get("name") or "")
            if not quote:
                invalid_lines.append(f"{input_name or item['code']} {item['code']}：未找到实时行情")
                continue
            if input_name and quote_name and _normalize_stock_name(input_name) != _normalize_stock_name(quote_name):
                invalid_lines.append(f"{input_name} {item['code']}：名称不匹配，行情名称为 {quote_name}")
                continue
            req = SimpleNamespace(
                code=item["code"],
                name=quote_name or input_name or item["code"],
                group_name=group_name or "默认",
                strategy_state="watch",
                target_buy_price=None,
                target_sell_price=None,
                stop_loss_price=None,
                notes="初始化向导 Markdown 导入",
            )
            stock = await repo.insert_watchlist_stock(db, req, sort_order, login_user_id)
            imported.append(stock)
            existing.add(item["code"])
            sort_order += 1
        return imported, duplicate_count, invalid_lines

    imported, duplicate_count, invalid_lines = await _with_db(_import)
    return {
        "status": "ok",
        "imported": len(imported),
        "duplicates": duplicate_count,
        "invalid": len(invalid_lines),
        "items": imported,
        "invalid_lines": invalid_lines,
    }


async def remove_from_watchlist(code: str, login_user_id: str = "admin"):
    async def _remove(db):
        return await repo.delete_watchlist_stock(db, code, login_user_id)

    rowcount = await _with_db(_remove)
    if rowcount == 0:
        raise HTTPException(status_code=404, detail=f"未找到自选股 {code}")
    return {"status": "ok", "code": code}


def _clean_watchlist_codes(codes):
    cleaned = []
    seen = set()
    for raw in codes or []:
        code = str(raw or "").strip()
        if not re.fullmatch(r"\d{6}", code) or code in seen:
            continue
        seen.add(code)
        cleaned.append(code)
    return cleaned


async def remove_watchlist_batch(codes: list[str], login_user_id: str = "admin"):
    clean_codes = _clean_watchlist_codes(codes)
    if not clean_codes:
        raise HTTPException(status_code=400, detail="请选择要删除的自选股")

    async def _remove(db):
        return await repo.delete_watchlist_stocks(db, clean_codes, login_user_id)

    deleted = await _with_db(_remove)
    return {"status": "ok", "deleted": deleted, "codes": clean_codes}


async def update_watchlist(code: str, req, login_user_id: str = "admin"):
    updates = {
        key: value
        for key, value in {
            "group_name": req.group_name,
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
        return await repo.update_watchlist_stock(db, code, updates, login_user_id)

    rowcount = await _with_db(_update)
    if rowcount == 0:
        raise HTTPException(status_code=404, detail=f"未找到自选股 {code}")
    return {"status": "ok", "code": code}


async def reorder_watchlist(req, login_user_id: str = "admin"):
    async def _reorder(db):
        await repo.reorder_watchlist(db, req.items, login_user_id)

    await _with_db(_reorder)
    return {"status": "ok", "updated": len(req.items)}


async def get_trades(code=None, account_id=None):
    async def _load(db):
        return await repo.fetch_trades(db, code, account_id)

    trades = await _with_db(_load)
    return {"count": len(trades), "trades": trades}


async def add_trade(req):
    async def _add(db):
        await _apply_default_trade_fees(db, req)
        trade = await repo.insert_trade(db, req)
        await repo.apply_trade_cash_effect(db, trade)
        return await repo.recalc_portfolio(db, req.code, getattr(req, "account_id", None) or "default")

    return {"status": "ok", "trade": await _with_db(_add)}


async def get_trade_stats(code: str, account_id: str | None = "default"):
    async def _load(db):
        return await repo.fetch_trade_stats(db, code, account_id)

    stats = await _with_db(_load)
    return {"code": code, "account_id": account_id or "default", **stats}


async def delete_trade(trade_id: int, account_id: str | None = None):
    async def _delete(db):
        trade = await repo.fetch_trade(db, trade_id)
        if not trade:
            raise HTTPException(status_code=404, detail="未找到交易记录")
        trade_account_id = trade.get("account_id") or "default"
        requested_account_id = account_id or trade_account_id
        if trade_account_id != requested_account_id:
            raise HTTPException(status_code=403, detail="交易记录不属于当前证券账户")
        await repo.delete_trade(db, trade_id)
        await repo.apply_trade_cash_effect(db, trade, reverse=True)
        portfolio = await repo.recalc_portfolio(db, trade["code"], requested_account_id)
        return portfolio

    portfolio = await _with_db(_delete)
    return {"status": "ok", "deleted_id": trade_id, "portfolio": portfolio}


async def clear_stock_trades(code: str, account_id: str | None = None):
    async def _clear(db):
        aid = account_id or "default"
        trades = await repo.fetch_trades(db, code, aid)
        count = len(trades)
        if count == 0:
            raise HTTPException(status_code=404, detail=f"未找到 {code} 的交易记录")
        await repo.delete_stock_trades(db, code, aid)
        affected_accounts = {trade.get("account_id") or "default" for trade in trades}
        for trade in trades:
            await repo.apply_trade_cash_effect(db, trade, reverse=True)
        portfolio = await repo.recalc_portfolio(db, code, aid)
        for affected_account in affected_accounts - {aid}:
            await repo.recalc_portfolio(db, code, affected_account)
        return count, portfolio

    count, portfolio = await _with_db(_clear)
    return {"status": "ok", "deleted_count": count, "code": code, "portfolio": portfolio}


async def edit_trade(trade_id: int, req, account_id: str | None = None):
    async def _edit(db):
        trade = await repo.fetch_trade(db, trade_id)
        if not trade:
            raise HTTPException(status_code=404, detail="未找到交易记录")
        trade_account_id = trade.get("account_id") or "default"
        requested_account_id = account_id or trade_account_id
        if trade_account_id != requested_account_id:
            raise HTTPException(status_code=403, detail="交易记录不属于当前证券账户")
        values = {
            "price": req.price if req.price is not None else trade["price"],
            "shares": req.shares if req.shares is not None else trade["shares"],
            "commission": req.commission if req.commission is not None else trade["commission"],
            "stamp_tax": req.stamp_tax if req.stamp_tax is not None else trade["stamp_tax"],
            "transfer_fee": req.transfer_fee if req.transfer_fee is not None else trade["transfer_fee"],
            "notes": req.notes if req.notes is not None else trade.get("notes", ""),
            "direction": req.direction if req.direction is not None else trade["direction"],
        }
        values["amount"] = round(values["price"] * values["shares"], 3)
        values["total_cost"] = round(
            values["amount"]
            + values["commission"]
            + values["stamp_tax"]
            + values["transfer_fee"],
            3,
        )
        await repo.update_trade(db, trade_id, values)
        updated = await repo.fetch_trade(db, trade_id)
        await repo.apply_trade_cash_effect(db, trade, reverse=True)
        await repo.apply_trade_cash_effect(db, updated)
        return await repo.recalc_portfolio(db, trade["code"], requested_account_id)

    portfolio = await _with_db(_edit)
    return {"status": "ok", "trade_id": trade_id, "portfolio": portfolio}


async def get_portfolio(account_id=None):
    positions, summary = await _portfolio_snapshot(account_id)
    return {"count": len(positions), "positions": positions, "summary": summary}


async def get_portfolio_overview(account_id=None):
    _, summary = await _portfolio_snapshot(account_id)
    return summary


async def get_account_dashboard(login_user_id: str | None = None):
    accounts = (await list_accounts(login_user_id)).get("accounts", [])
    if not any(account.get("id") == "default" for account in accounts):
        if not login_user_id:
            accounts = [{"id": "default", "name": "默认账户", "broker": ""}, *accounts]

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

    combined = {
        "total_assets": round(sum(float(item.get("total_assets") or 0) for item in items), 3),
        "market_value": round(sum(float(item.get("market_value") or 0) for item in items), 3),
        "cash": round(sum(float(item.get("cash") or 0) for item in items), 3),
        "total_cost": round(sum(float(item.get("total_cost") or 0) for item in items), 3),
        "daily_pnl": round(sum(float(item.get("daily_pnl") or 0) for item in items), 3),
        "unrealized_pnl": round(sum(float(item.get("unrealized_pnl") or 0) for item in items), 3),
        "realized_pnl": round(sum(float(item.get("realized_pnl") or 0) for item in items), 3),
        "historical_pnl": round(sum(float(item.get("historical_pnl") or 0) for item in items), 3),
        "total_commission": round(sum(float(item.get("total_commission") or 0) for item in items), 3),
        "total_stamp_tax": round(sum(float(item.get("total_stamp_tax") or 0) for item in items), 3),
    }
    previous_market_value = combined["market_value"] - combined["daily_pnl"]
    combined["daily_pnl_pct"] = round(combined["daily_pnl"] / previous_market_value * 100, 3) if previous_market_value else 0
    combined["unrealized_pnl_pct"] = round(combined["unrealized_pnl"] / combined["total_cost"] * 100, 3) if combined["total_cost"] else 0
    combined["total_pnl"] = round(combined["realized_pnl"] + combined["unrealized_pnl"], 3)
    combined["cash_source"] = "manual" if any((item.get("cash_source") == "manual") for item in items) else "unset"

    return {
        "combined": combined,
        "accounts": items,
        "dominant_account": max(items, key=lambda item: item.get("market_value", 0), default=None),
    }


async def set_cash_balance(account_id=None, balance=0, notes=""):
    async def _set(db):
        return await repo.set_cash_balance(db, account_id or "default", float(balance), notes=notes, source="manual")

    return await _with_db(_set)


async def get_cash_ledger(account_id=None, limit=20):
    async def _load(db):
        return await repo.fetch_cash_ledger(db, account_id or "default", limit)

    rows = await _with_db(_load)
    return {"account_id": account_id or "default", "count": len(rows), "entries": rows}


def _planned_total_cost(price, shares, explicit_total=None):
    if explicit_total is not None:
        return explicit_total
    if price and shares:
        return round(price * shares, 3)
    return None


def _parse_day(value=None) -> date:
    if not value:
        return datetime.now().date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc


def _historical_close_for_day(code: str, day: date, rows: list[dict]) -> float:
    target = day.isoformat()
    eligible = []
    for row in rows or []:
        row_day = str(row.get("date") or "")[:10]
        if not row_day:
            continue
        if row_day == target:
            return float(row.get("close") or 0)
        if row_day < target:
            eligible.append(row)
    if eligible:
        return float(eligible[-1].get("close") or 0)
    return 0.0


async def ensure_daily_pnl_snapshot(day=None, account_id=None) -> dict:
    """Persist holding PnL rows for a day so the PnL calendar is not empty."""
    target_day = _parse_day(day)

    async def _load(db):
        positions = await repo.fetch_positions(db, account_id)
        cash_and_fees = await repo.fetch_cash_and_fees(db, account_id)
        return positions, cash_and_fees

    positions, cash_and_fees = await _with_db(_load)
    if not positions:
        return {"status": "empty", "date": target_day.isoformat(), "written": 0, "items": []}

    today = datetime.now().date()
    codes = [position["code"] for position in positions]
    quote_map = await get_batch_quotes(codes) if target_day >= today else {}
    items = []

    for position in positions:
        code = str(position.get("code") or "")[:6]
        shares = float(position.get("total_shares") or 0)
        avg_cost = float(position.get("avg_cost") or 0)
        if shares <= 0 or avg_cost <= 0:
            continue

        close_price = 0.0
        source = "missing"
        if target_day >= today:
            quote = quote_map.get(code) or {}
            close_price = float(quote.get("price") or 0)
            source = "realtime_quote" if close_price else source
        if not close_price:
            rows = await asyncio.to_thread(get_tencent_history_kline, code, "day", 30)
            close_price = _historical_close_for_day(code, target_day, rows)
            source = "historical_kline" if close_price else source
        if not close_price:
            close_price = float(position.get("current_price") or 0) or avg_cost
            source = "stored_or_cost_fallback"

        pnl = round((close_price - avg_cost) * shares, 3)
        items.append(
            {
                "code6": code,
                "name": position.get("name") or code,
                "pnl": pnl,
                "close_price": round(close_price, 3),
                "shares": round(shares, 3),
                "source": source,
            }
        )

    total_pnl = round(sum(item["pnl"] for item in items), 3)
    market_value = round(sum(item["close_price"] * item["shares"] for item in items), 3)
    cash = float(cash_and_fees.get("cash") or 0)
    total_cost = round(sum((float(p.get("avg_cost") or 0) * float(p.get("total_shares") or 0)) for p in positions), 3)
    total_assets = round(cash + market_value, 3)
    total_pnl_pct = round(total_pnl / total_cost * 100, 3) if total_cost else 0.0

    async def _write(db):
        aid = account_id or "default"
        for item in items:
            await db.execute(
                """
                INSERT OR REPLACE INTO daily_pnl (date, account_id, code6, pnl, close_price, shares)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (target_day.isoformat(), aid, item["code6"], item["pnl"], item["close_price"], item["shares"]),
            )
        await db.execute(
            """
            INSERT OR REPLACE INTO daily_pnl (
                date, account_id, code6, total_assets, cash, market_value, realized_pnl,
                unrealized_pnl, total_pnl, total_pnl_pct
            ) VALUES (?, ?, '', ?, ?, ?, 0, ?, ?, ?)
            """,
            (target_day.isoformat(), aid, total_assets, cash, market_value, total_pnl, total_pnl, total_pnl_pct),
        )
        await db.commit()

    await _with_db(_write)
    return {
        "status": "ok",
        "date": target_day.isoformat(),
        "written": len(items),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "items": items,
    }


async def _ensure_recent_pnl_snapshots_for_calendar(year: int, month: int, account_id: str | None = "default"):
    today = datetime.now().date()
    recent_days = [today - timedelta(days=1)]
    if datetime.now().hour >= 15:
        recent_days.append(today)
    for day in recent_days:
        if day.year == year and day.month == month:
            async def _has_rows(db, target=day.isoformat()):
                rows = await repo.fetch_daily_pnl_day(db, target, account_id)
                return bool(rows)

            try:
                exists = await _with_db(_has_rows)
                if not exists:
                    await ensure_daily_pnl_snapshot(day, account_id)
            except Exception:
                # Calendar reads must remain available even if a backfill data source is temporarily down.
                continue


async def get_pnl_calendar(year=None, month=None, code=None, account_id: str | None = "default"):
    now = datetime.now()
    y = year or now.year
    m = month or now.month

    if y == now.year and m == now.month:
        await _ensure_recent_pnl_snapshots_for_calendar(y, m, account_id)

    async def _load(db):
        return await repo.fetch_daily_pnl(db, y, m, code, account_id)

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
        "total_pnl": round(total_pnl, 3),
        "win_days": win_days,
        "loss_days": loss_days,
        "trade_days": trade_days,
        "win_rate": win_rate,
    }


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


async def delete_pending_position(position_id: int, account_id: str | None = None):
    async def _delete(db):
        return await repo.delete_pending_position(db, position_id, account_id)

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


async def get_pnl_day_detail(day: str, account_id: str | None = "default"):
    async def _load(db):
        trades = await repo.fetch_day_trades(db, day, account_id)
        daily_pnl = await repo.fetch_daily_pnl_day(db, day, account_id)
        daily_rows = await repo.fetch_daily_pnl_stock_rows_day(db, day, account_id)
        return trades, daily_pnl, daily_rows

    trades, daily_pnl, daily_rows = await _with_db(_load)
    trade_names = {}
    for trade in trades:
        trade_names.setdefault((trade.get("code") or "")[:6], trade.get("name"))
    stock_pnl = [
        {
            "code": row.get("code6"),
            "name": trade_names.get(row.get("code6")) or row.get("code6"),
            "amount": round(row.get("pnl") or 0, 3),
            "close_price": row.get("close_price"),
            "shares": row.get("shares"),
        }
        for row in daily_rows
    ]
    return {
        "date": day,
        "account_id": account_id or "default",
        "daily_pnl": daily_pnl,
        "stock_pnl": stock_pnl,
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


async def delete_trading_plan(plan_id: int, account_id: str | None = None):
    async def _delete(db):
        return await repo.delete_trading_plan(db, plan_id, account_id)

    rowcount = await _with_db(_delete)
    if rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到交易计划")
    return {"status": "ok", "id": plan_id}
