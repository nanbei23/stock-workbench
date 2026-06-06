#!/usr/bin/env python3
"""Initialize local database from personal watchlist and trade-history files."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DB_PATH  # noqa: E402
from models.database import SCHEMA  # noqa: E402


OBSERVATION_GROUP = "观察池"
DEFAULT_GROUP = "默认"


@dataclass(frozen=True)
class FeeConfig:
    commission_rate: float = 0.0001
    min_commission: float = 5.0
    transfer_fee_rate: float = 0.00001
    sell_stamp_tax_rate: float = 0.0


@dataclass(frozen=True)
class FeeBreakdown:
    commission: float
    transfer_fee: float
    stamp_tax: float


@dataclass(frozen=True)
class WatchItem:
    code: str
    name: str
    group_name: str


@dataclass(frozen=True)
class ClosedTrade:
    name: str
    code: str
    security_type: str
    buy_price: float
    sell_price: float
    shares: float
    buy_date: str | None
    sell_date: str | None


@dataclass(frozen=True)
class TradeRecord:
    code: str
    name: str
    direction: str
    price: float
    shares: float
    commission: float
    stamp_tax: float
    transfer_fee: float
    trade_time: str
    notes: str


@dataclass(frozen=True)
class TradingPlan:
    code: str
    name: str
    target_price: float
    shares: float
    notes: str


@dataclass(frozen=True)
class ParsedTradeHistory:
    closed_trades: list[ClosedTrade]
    trading_plans: list[TradingPlan]
    cash_balance: float | None
    initial_capital_reported: float | None


def parse_money(value: str | int | float | None) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"—", "-", "--"}:
        return None
    sign = -1 if text.startswith("-") else 1
    text = re.sub(r"[,+元¥￥\s]", "", text)
    text = text.replace("~", "").replace("约", "")
    multiplier = 1.0
    if text.endswith("万"):
        multiplier = 10000.0
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100000000.0
        text = text[:-1]
    text = text.lstrip("+-")
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    return round(sign * float(match.group(0)) * multiplier, 3) if match else None


def parse_quantity(value: str | int | float | None) -> float:
    number = parse_money(str(value or "").replace("股", "").replace("份", ""))
    return round(float(number or 0), 3)


def parse_price(value: str | int | float | None) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"—", "-", "--"}:
        return None
    first = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return float(first.group(0)) if first else None


def markdown_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_table_separator(cells: Iterable[str]) -> bool:
    clean = [cell.replace(" ", "") for cell in cells if cell.strip()]
    return bool(clean) and all(re.fullmatch(r":?-{2,}:?", cell) for cell in clean)


def parse_watchlist(content: str) -> list[WatchItem]:
    items: list[WatchItem] = []
    seen: set[str] = set()
    current_group = DEFAULT_GROUP
    for raw in str(content or "").splitlines():
        line = raw.strip()
        if line.startswith("##"):
            current_group = OBSERVATION_GROUP if OBSERVATION_GROUP in line else DEFAULT_GROUP
            continue
        if "|" not in line:
            continue
        cells = markdown_cells(line)
        if is_table_separator(cells) or "代码" in cells:
            continue
        code_idx = next((i for i, cell in enumerate(cells) if re.fullmatch(r"\d{6}", cell)), None)
        if code_idx is None:
            continue
        code = cells[code_idx]
        if code in seen:
            continue
        name_idx = 1 if len(cells) > 2 else 0
        name = cells[name_idx] if name_idx != code_idx else code
        items.append(WatchItem(code=code, name=name.strip() or code, group_name=current_group))
        seen.add(code)
    return items


def parse_date(value: str | None, year: int) -> str | None:
    text = str(value or "").strip()
    if not text or text in {"—", "-", "--"}:
        return None
    text = text.replace("前", "").replace(".", "/").replace("-", "/")
    if re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", text):
        y, m, d = [int(part) for part in text.split("/")]
        return f"{y:04d}-{m:02d}-{d:02d}"
    if re.fullmatch(r"\d{1,2}/\d{1,2}", text):
        m, d = [int(part) for part in text.split("/")]
        return f"{year:04d}-{m:02d}-{d:02d}"
    return None


def infer_year(content: str) -> int:
    match = re.search(r"20\d{2}", content or "")
    return int(match.group(0)) if match else datetime.now().year


def parse_trade_history(content: str) -> ParsedTradeHistory:
    year = infer_year(content)
    section = ""
    closed: list[ClosedTrade] = []
    plans: list[TradingPlan] = []
    cash_balance: float | None = None
    initial_capital_reported: float | None = None

    for raw in str(content or "").splitlines():
        line = raw.strip()
        if line.startswith("##"):
            section = line
            continue
        if "|" not in line:
            continue
        cells = markdown_cells(line)
        if is_table_separator(cells):
            continue

        if "交易明细" in section and len(cells) >= 12 and cells[0] not in {"标的", "**合计**"}:
            buy_price = parse_price(cells[3])
            sell_price = parse_price(cells[4])
            shares = parse_quantity(cells[5])
            if not cells[1] or not re.fullmatch(r"\d{6}", cells[1]) or not buy_price or not sell_price or not shares:
                continue
            closed.append(
                ClosedTrade(
                    name=cells[0].strip("* "),
                    code=cells[1],
                    security_type=cells[2],
                    buy_price=buy_price,
                    sell_price=sell_price,
                    shares=shares,
                    buy_date=parse_date(cells[6], year),
                    sell_date=parse_date(cells[7], year),
                )
            )
        elif "账户总结" in section and len(cells) >= 2:
            if "当前现金" in cells[0]:
                cash_balance = parse_money(cells[1])
            elif "初始本金" in cells[0]:
                initial_capital_reported = parse_money(cells[1])
        elif "待建仓" in section and len(cells) >= 7 and cells[0] != "标的":
            code = cells[1]
            price = parse_price(cells[2])
            shares = parse_quantity(cells[3])
            if not re.fullmatch(r"\d{6}", code or "") or not price or not shares:
                continue
            notes = f"初始化导入；买入价 {cells[2]}；金额 {cells[4]}；止盈 {cells[5]}；止损 {cells[6]}"
            plans.append(TradingPlan(code=code, name=cells[0], target_price=price, shares=shares, notes=notes))

    return ParsedTradeHistory(
        closed_trades=closed,
        trading_plans=plans,
        cash_balance=cash_balance,
        initial_capital_reported=initial_capital_reported,
    )


def is_transfer_fee_applicable(code: str, security_type: str) -> bool:
    return str(code).startswith("6") and "ETF" not in str(security_type).upper()


def calculate_fees(amount: float, direction: str, code: str, security_type: str, config: FeeConfig) -> FeeBreakdown:
    commission = max(amount * config.commission_rate, config.min_commission)
    transfer_fee = amount * config.transfer_fee_rate if is_transfer_fee_applicable(code, security_type) else 0.0
    stamp_tax = amount * config.sell_stamp_tax_rate if direction == "sell" else 0.0
    return FeeBreakdown(
        commission=round(commission, 3),
        transfer_fee=round(transfer_fee, 3),
        stamp_tax=round(stamp_tax, 3),
    )


def _buy_date_for(trade: ClosedTrade) -> str:
    if trade.buy_date:
        return trade.buy_date
    if trade.sell_date:
        return (datetime.strptime(trade.sell_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return f"{datetime.now().year}-01-01"


def build_trade_records(closed_trades: list[ClosedTrade], config: FeeConfig) -> list[TradeRecord]:
    records: list[TradeRecord] = []
    for trade in closed_trades:
        buy_amount = round(trade.buy_price * trade.shares, 3)
        sell_amount = round(trade.sell_price * trade.shares, 3)
        buy_fees = calculate_fees(buy_amount, "buy", trade.code, trade.security_type, config)
        sell_fees = calculate_fees(sell_amount, "sell", trade.code, trade.security_type, config)
        buy_date = _buy_date_for(trade)
        sell_date = trade.sell_date or buy_date
        records.append(
            TradeRecord(
                code=trade.code,
                name=trade.name,
                direction="buy",
                price=trade.buy_price,
                shares=trade.shares,
                commission=buy_fees.commission,
                stamp_tax=buy_fees.stamp_tax,
                transfer_fee=buy_fees.transfer_fee,
                trade_time=f"{buy_date} 09:30:00",
                notes="初始化脚本导入：历史买入",
            )
        )
        records.append(
            TradeRecord(
                code=trade.code,
                name=trade.name,
                direction="sell",
                price=trade.sell_price,
                shares=trade.shares,
                commission=sell_fees.commission,
                stamp_tax=sell_fees.stamp_tax,
                transfer_fee=sell_fees.transfer_fee,
                trade_time=f"{sell_date} 15:00:00",
                notes="初始化脚本导入：历史卖出",
            )
        )
    return records


def infer_initial_capital(current_cash: float | None, trades: list[TradeRecord]) -> float | None:
    if current_cash is None:
        return None
    cash_delta = 0.0
    for trade in trades:
        amount = round(trade.price * trade.shares, 3)
        fees = trade.commission + trade.stamp_tax + trade.transfer_fee
        if trade.direction == "buy":
            cash_delta -= amount + fees
        elif trade.direction == "sell":
            cash_delta += amount - fees
    return round(float(current_cash) - cash_delta, 3)


def backup_database(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_suffix(f".init-backup-{stamp}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO accounts (id, name, broker) VALUES ('default', '默认账户', '')")
    conn.commit()


def reset_imported_data(conn: sqlite3.Connection) -> None:
    for table in ("trades", "portfolio", "watchlist", "trading_plans", "cash_ledger"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM settings WHERE key = 'cash_balance_default'")
    conn.commit()


def insert_watchlist(conn: sqlite3.Connection, items: list[WatchItem]) -> int:
    for idx, item in enumerate(items, start=1):
        conn.execute(
            """
            INSERT OR REPLACE INTO watchlist
                (code, name, group_name, sort_order, strategy_state, notes)
            VALUES (?, ?, ?, ?, 'watch', ?)
            """,
            (item.code, item.name, item.group_name, idx, "初始化脚本导入"),
        )
    conn.commit()
    return len(items)


def insert_trades(conn: sqlite3.Connection, records: list[TradeRecord], account_id: str) -> int:
    for item in records:
        amount = round(item.price * item.shares, 3)
        total_cost = round(amount + item.commission + item.stamp_tax + item.transfer_fee, 3)
        conn.execute(
            """
            INSERT INTO trades (
                code, name, direction, price, shares, amount,
                commission, stamp_tax, transfer_fee, total_cost, trade_time, notes, account_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.code,
                item.name,
                item.direction,
                item.price,
                item.shares,
                amount,
                item.commission,
                item.stamp_tax,
                item.transfer_fee,
                total_cost,
                item.trade_time,
                item.notes,
                account_id,
            ),
        )
    conn.commit()
    rebuild_portfolio(conn, account_id)
    return len(records)


def rebuild_portfolio(conn: sqlite3.Connection, account_id: str) -> None:
    codes = [row[0] for row in conn.execute("SELECT DISTINCT code FROM trades WHERE account_id = ?", (account_id,))]
    conn.execute("DELETE FROM portfolio WHERE account_id = ?", (account_id,))
    for code in codes:
        rows = conn.execute(
            """
            SELECT name, direction, price, shares, amount, commission, stamp_tax, transfer_fee
            FROM trades
            WHERE code = ? AND account_id = ?
            ORDER BY trade_time ASC, id ASC
            """,
            (code, account_id),
        ).fetchall()
        total_shares = 0.0
        total_cost = 0.0
        name = rows[0][0] if rows else code
        for row in rows:
            _, direction, _, shares, amount, commission, stamp_tax, transfer_fee = row
            if direction == "buy":
                total_shares += shares
                total_cost += amount + commission + stamp_tax + transfer_fee
            elif direction == "sell" and total_shares > 0:
                avg_before = total_cost / total_shares
                total_shares = max(0.0, total_shares - shares)
                total_cost = avg_before * total_shares
        total_shares = round(total_shares, 3)
        if total_shares > 0:
            avg_cost = round(total_cost / total_shares, 3)
            conn.execute(
                """
                INSERT INTO portfolio
                    (code, name, total_shares, available_shares, avg_cost, updated_at, account_id)
                VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
                """,
                (code, name, total_shares, total_shares, avg_cost, account_id),
            )
    conn.commit()


def insert_cash(conn: sqlite3.Connection, account_id: str, balance: float | None) -> dict:
    if balance is None:
        return {"balance": None, "imported": 0}
    key = f"cash_balance_{account_id}"
    rounded = round(float(balance), 3)
    conn.execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(rounded)),
    )
    conn.execute(
        """
        INSERT INTO cash_ledger (account_id, direction, amount, balance_after, source, notes)
        VALUES (?, 'adjust', ?, ?, 'init_script', '初始化脚本导入账户现金')
        """,
        (account_id, rounded, rounded),
    )
    conn.commit()
    return {"balance": rounded, "imported": 1}


def insert_trading_plans(conn: sqlite3.Connection, plans: list[TradingPlan], account_id: str) -> int:
    for plan in plans:
        plan_total_cost = round(plan.target_price * plan.shares, 3)
        conn.execute(
            """
            INSERT INTO trading_plans
                (code, name, direction, plan_type, target_price, condition_type,
                 plan_shares, plan_total_cost, status, reason, account_id)
            VALUES (?, ?, 'buy', 'near_target', ?, 'price_lte', ?, ?, 'pending', ?, ?)
            """,
            (plan.code, plan.name, plan.target_price, plan.shares, plan_total_cost, plan.notes, account_id),
        )
    conn.commit()
    return len(plans)


def initialize_database(
    db_path: Path,
    watchlist_path: Path,
    trades_path: Path,
    cash_balance: float | None,
    reset: bool,
    apply: bool,
    backup: bool,
    fee_config: FeeConfig,
    account_id: str = "default",
) -> dict:
    watch_items = parse_watchlist(watchlist_path.read_text(encoding="utf-8"))
    parsed_history = parse_trade_history(trades_path.read_text(encoding="utf-8"))
    trades = build_trade_records(parsed_history.closed_trades, fee_config)
    effective_cash = cash_balance if cash_balance is not None else parsed_history.cash_balance
    inferred_initial_capital = infer_initial_capital(effective_cash, trades)
    summary = {
        "apply": apply,
        "db_path": str(db_path),
        "backup_path": None,
        "watchlist": {
            "parsed": len(watch_items),
            "self_selected": sum(1 for item in watch_items if item.group_name != OBSERVATION_GROUP),
            "observation_pool": sum(1 for item in watch_items if item.group_name == OBSERVATION_GROUP),
            "imported": 0,
        },
        "trades": {"closed_positions": len(parsed_history.closed_trades), "parsed": len(trades), "imported": 0},
        "cash": {
            "balance": round(float(effective_cash), 3) if effective_cash is not None else None,
            "reported_initial_capital": parsed_history.initial_capital_reported,
            "inferred_initial_capital": inferred_initial_capital,
            "imported": 0,
        },
        "trading_plans": {"parsed": len(parsed_history.trading_plans), "imported": 0},
        "fee_model": asdict(fee_config),
    }
    if not apply:
        return summary

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if backup:
        backup_path = backup_database(db_path)
        summary["backup_path"] = str(backup_path) if backup_path else None
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        if reset:
            reset_imported_data(conn)
        summary["watchlist"]["imported"] = insert_watchlist(conn, watch_items)
        summary["trades"]["imported"] = insert_trades(conn, trades, account_id)
        cash_summary = insert_cash(conn, account_id, effective_cash)
        summary["cash"].update(cash_summary)
        summary["trading_plans"]["imported"] = insert_trading_plans(conn, parsed_history.trading_plans, account_id)
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize stock workbench database from Markdown files.")
    parser.add_argument("--watchlist", required=True, type=Path, help="自选股 Markdown 文件")
    parser.add_argument("--trades", required=True, type=Path, help="交易历史 Markdown 文件")
    parser.add_argument("--db", default=DB_PATH, type=Path, help="目标 SQLite 数据库")
    parser.add_argument("--cash", type=parse_money, default=None, help="账户现金，例如 253375.68")
    parser.add_argument("--account-id", default="default", help="账户 ID")
    parser.add_argument("--commission-rate", type=float, default=0.0001, help="综合佣金费率，默认万一")
    parser.add_argument("--min-commission", type=float, default=5.0, help="最低综合佣金，默认不免五")
    parser.add_argument("--transfer-fee-rate", type=float, default=0.00001, help="上海股票过户费率")
    parser.add_argument("--sell-stamp-tax-rate", type=float, default=0.0, help="卖出印花税率，默认按截图为 0")
    parser.add_argument("--reset", action="store_true", help="导入前清空自选、交易、持仓、交易计划和现金流水")
    parser.add_argument("--apply", action="store_true", help="实际写入数据库；不传则只预览")
    parser.add_argument("--no-backup", action="store_true", help="写入前不备份数据库")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    fee_config = FeeConfig(
        commission_rate=args.commission_rate,
        min_commission=args.min_commission,
        transfer_fee_rate=args.transfer_fee_rate,
        sell_stamp_tax_rate=args.sell_stamp_tax_rate,
    )
    summary = initialize_database(
        db_path=args.db,
        watchlist_path=args.watchlist,
        trades_path=args.trades,
        cash_balance=args.cash,
        reset=args.reset,
        apply=args.apply,
        backup=not args.no_backup,
        fee_config=fee_config,
        account_id=args.account_id,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.apply:
        print("预览模式：未写入数据库。确认后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
