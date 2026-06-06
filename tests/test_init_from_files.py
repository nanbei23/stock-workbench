import sqlite3
import tempfile
import unittest
from pathlib import Path

from models.database import SCHEMA
from scripts.init_from_files import (
    FeeConfig,
    build_trade_records,
    calculate_fees,
    initialize_database,
    parse_trade_history,
    parse_watchlist,
)


WATCHLIST_MD = """
## 自选股（2只）

| # | 股票名称 | 代码 |
|---|---------|------|
| 1 | 深科技 | 000021 |
| 2 | 国泰通信ETF | 515880 |

## 观察池（1只）

| # | 股票名称 | 代码 |
|---|---------|------|
| 1 | 中兴通讯 | 000063 |
"""


TRADE_MD = """
更新时间：2026-06-02

## 交易明细

| 标的 | 代码 | 类型 | 成本价 | 卖出价 | 数量 | 买入日期 | 卖出日期 | 买入总额 | 卖出总额 | 盈亏 | 收益率 |
|------|------|------|--------|--------|------|----------|----------|----------|----------|------|--------|
| 国泰通信ETF | 515880 | ETF | 1.5182 | 1.5336 | 55,900份 | — | 5/21前 | 84,867 | 85,728 | +861 | +1.01% |
| 均胜电子 | 600699 | 股票 | 31.04 | 31.50 | 1,300股 | 5/19 | 5/25 | 40,352 | 40,950 | +598 | +1.48% |

## 账户总结

| 项目 | 金额 |
|------|------|
| 当前现金 | 253,375.68元 |

## 待建仓计划

| 标的 | 代码 | 买入价 | 数量 | 金额 | 止盈 | 止损 |
|------|------|--------|------|------|------|------|
| 扬杰科技 | 300373 | 75.00 | 500股 | 3.75万 | 90/100 | 70 |
| 安集科技 | 688019 | 290-300 | 100股 | ~3万 | 360/400 | 260 |
"""


class InitFromFilesTests(unittest.TestCase):
    def test_parse_watchlist_keeps_observation_pool_group(self):
        items = parse_watchlist(WATCHLIST_MD)

        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].group_name, "默认")
        self.assertEqual(items[2].code, "000063")
        self.assertEqual(items[2].group_name, "观察池")

    def test_fee_model_matches_waanyi_bumianwu_and_transfer_fee(self):
        config = FeeConfig()

        buy_stock = calculate_fees(40352, "buy", "600699", "股票", config)
        sell_etf = calculate_fees(85732.32, "sell", "515880", "ETF", config)

        self.assertEqual(buy_stock.commission, 5.0)
        self.assertEqual(buy_stock.transfer_fee, 0.404)
        self.assertEqual(buy_stock.stamp_tax, 0.0)
        self.assertEqual(sell_etf.commission, 8.573)
        self.assertEqual(sell_etf.transfer_fee, 0.0)
        self.assertEqual(sell_etf.stamp_tax, 0.0)

    def test_parse_trade_history_builds_trades_cash_and_plans(self):
        parsed = parse_trade_history(TRADE_MD)
        trades = build_trade_records(parsed.closed_trades, FeeConfig())

        self.assertEqual(parsed.cash_balance, 253375.68)
        self.assertNotEqual(parsed.initial_capital_reported, parsed.cash_balance)
        self.assertEqual(len(trades), 4)
        self.assertEqual(trades[0].direction, "buy")
        self.assertEqual(trades[1].direction, "sell")
        self.assertEqual(trades[0].trade_time, "2026-05-20 09:30:00")
        self.assertEqual(trades[2].transfer_fee, 0.404)
        self.assertEqual(len(parsed.trading_plans), 2)
        self.assertEqual(parsed.trading_plans[1].target_price, 290.0)

    def test_initialize_database_writes_watchlist_trades_cash_and_plans(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workbench.db"
            watchlist_path = Path(tmp) / "watchlist.md"
            trades_path = Path(tmp) / "trades.md"
            watchlist_path.write_text(WATCHLIST_MD, encoding="utf-8")
            trades_path.write_text(TRADE_MD, encoding="utf-8")
            with sqlite3.connect(db_path) as conn:
                conn.executescript(SCHEMA)

            summary = initialize_database(
                db_path=db_path,
                watchlist_path=watchlist_path,
                trades_path=trades_path,
                cash_balance=253375.68,
                reset=True,
                apply=True,
                backup=False,
                fee_config=FeeConfig(),
            )

            with sqlite3.connect(db_path) as conn:
                watchlist_count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
                pool_count = conn.execute("SELECT COUNT(*) FROM watchlist WHERE group_name='观察池'").fetchone()[0]
                trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
                position_count = conn.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0]
                cash = conn.execute("SELECT value FROM settings WHERE key='cash_balance_default'").fetchone()[0]
                plan_count = conn.execute("SELECT COUNT(*) FROM trading_plans").fetchone()[0]

            self.assertEqual(summary["watchlist"]["imported"], 3)
            self.assertEqual(summary["trades"]["imported"], 4)
            self.assertEqual(summary["trading_plans"]["imported"], 2)
            self.assertEqual(summary["cash"]["balance"], 253375.68)
            self.assertLess(summary["cash"]["inferred_initial_capital"], 253375.68)
            self.assertNotEqual(summary["cash"]["inferred_initial_capital"], 300000.0)
            self.assertEqual(watchlist_count, 3)
            self.assertEqual(pool_count, 1)
            self.assertEqual(trade_count, 4)
            self.assertEqual(position_count, 0)
            self.assertEqual(float(cash), 253375.68)
            self.assertEqual(plan_count, 2)


if __name__ == "__main__":
    unittest.main()
