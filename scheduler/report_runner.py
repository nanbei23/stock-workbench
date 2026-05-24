"""定时AI报告生成器 — 定时触发L1分析并保存摘要"""
import datetime
import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "workbench.db"
def _gbrain_save_report(slug: str, title: str, content: str):
    """Fire-and-forget gbrain write using subprocess (sync, called in thread)"""
    import subprocess
    try:
        gbrain_cli = Path.home() / ".bun" / "bin" / "gbrain"
        if not gbrain_cli.exists():
            return
        tmp_path = Path("/tmp") / f"gbrain_{slug.replace('/', '_')}.md"
        tmp_path.write_text(content, encoding="utf-8")
        subprocess.Popen(
            [str(gbrain_cli), "put", slug, "--title", title, "--file", str(tmp_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.debug("gbrain write skipped: %s", e)

# 报告类型与对应设置key的映射
REPORT_SETTINGS = {
    "open":        "schedule_open_report",     # 09:30 开盘报告
    "am_close":    "schedule_am_close",        # 11:30 上午收盘
    "pm_open":     "schedule_pm_open",         # 13:00 下午开盘
    "close":       "schedule_close_report",    # 15:00 收盘报告
    "review":      "schedule_review",          # 15:05 策略复盘
}


def _get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def _get_setting(key):
    """获取设置值"""
    db = _get_db()
    try:
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row['value'] if row else None
    finally:
        db.close()


def _get_watchlist_stocks():
    """获取自选股列表"""
    db = _get_db()
    try:
        rows = db.execute(
            "SELECT w.code, w.name, COALESCE(p.total_shares, 0) as total_shares, "
            "COALESCE(p.avg_cost, 0) as avg_cost "
            "FROM watchlist w LEFT JOIN portfolio p ON w.code = p.code"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def _get_strategy_prices(code: str) -> dict:
    """从 strategy_params 和 watchlist 获取策略价格"""
    db = _get_db()
    try:
        result = {"stop_loss": None, "target_sell": None, "target_buy": None, "strategy_state": "watch"}
        row = db.execute(
            "SELECT stop_loss_price, target_sell_price, target_buy_price, strategy_state FROM watchlist WHERE code = ?",
            (code,)
        ).fetchone()
        if row:
            result["stop_loss"] = row["stop_loss_price"]
            result["target_sell"] = row["target_sell_price"]
            result["target_buy"] = row["target_buy_price"]
            result["strategy_state"] = row["strategy_state"] or "watch"
        code6 = code[:6]
        sp = db.execute("SELECT entry_price, drop_pct, target_profit_pct FROM strategy_params WHERE code6 = ?", (code6,)).fetchone()
        if sp:
            ep = sp["entry_price"] or 0
            if ep > 0:
                if result["stop_loss"] is None and sp["drop_pct"]:
                    result["stop_loss"] = round(ep * (1 - sp["drop_pct"] / 100), 2)
                if result["target_sell"] is None and sp["target_profit_pct"]:
                    result["target_sell"] = round(ep * (1 + sp["target_profit_pct"] / 100), 2)
        return result
    finally:
        db.close()


def _run_l1_evaluation(stock, quote):
    """L1规则引擎评估（简化版，使用策略参数）"""
    code = stock["code"]
    name = stock["name"]
    total_shares = stock.get("total_shares", 0)
    avg_cost = stock.get("avg_cost", 0)
    price = quote.get("price", 0)
    change_pct = quote.get("change_pct", 0)

    # 获取策略价格
    sp = _get_strategy_prices(code)
    stop_loss_price = sp["stop_loss"]
    target_sell_price = sp["target_sell"]
    target_buy_price = sp["target_buy"]
    strategy_state = sp["strategy_state"]

    result = {
        "code": code,
        "name": name,
        "price": price,
        "change_pct": change_pct,
        "total_shares": total_shares,
        "avg_cost": avg_cost,
        "strategy_state": strategy_state,
        "signal": "HOLD",
        "advice": "观望",
        "detail": "",
    }

    if total_shares > 0 and avg_cost > 0:
        pnl_pct = (price - avg_cost) / avg_cost * 100
        result["pnl_pct"] = round(pnl_pct, 2)

        # 止损：优先用 DB 止损价，否则 -20% 默认
        if stop_loss_price and price <= stop_loss_price:
            result["signal"] = "SELL"
            result["advice"] = "立即止损"
            result["detail"] = f"现价{price:.2f}≤止损价{stop_loss_price:.2f}（亏损{pnl_pct:.1f}%）"
        elif pnl_pct <= -20:
            result["signal"] = "SELL"
            result["advice"] = "立即止损"
            result["detail"] = f"亏损{pnl_pct:.1f}%，超过20%止损线"

        # 止盈：优先用 DB 目标卖出价，否则 +15% 默认
        elif target_sell_price and price >= target_sell_price:
            result["signal"] = "SELL"
            result["advice"] = "考虑减仓"
            result["detail"] = f"现价{price:.2f}≥目标卖出{target_sell_price:.2f}（盈利{pnl_pct:.1f}%）"
        elif pnl_pct >= 15:
            result["signal"] = "SELL"
            result["advice"] = "考虑减仓"
            result["detail"] = f"盈利{pnl_pct:.1f}%，可分批止盈"
        else:
            result["detail"] = f"持仓{total_shares}股，均价{avg_cost:.2f}"

    # 未持仓：目标买入价判断
    elif target_buy_price and price:
        if price <= target_buy_price:
            result["signal"] = "BUY"
            result["advice"] = "到达买入区间"
            result["detail"] = f"现价{price:.2f}≤目标买入{target_buy_price:.2f}"
        elif price <= target_buy_price * 1.03:
            result["signal"] = "WATCH"
            result["advice"] = "接近买点"
            result["detail"] = f"现价{price:.2f}距买入价{((price/target_buy_price-1)*100):.1f}%"

    if change_pct >= 5:
        result["signal"] = "WATCH"
        result["advice"] = "涨幅异动"
        result["detail"] += f" | 涨幅+{change_pct:.1f}%触发异动"
    elif change_pct <= -5:
        result["signal"] = "WATCH"
        result["advice"] = "跌幅异动"
        result["detail"] += f" | 跌幅{change_pct:.1f}%触发异动"

    return result


def _gbrain_search_for_l1(code: str, name: str) -> str:
    """Search gbrain for L1 context enhancement"""
    import subprocess
    try:
        gbrain_cli = Path.home() / ".bun" / "bin" / "gbrain"
        if not gbrain_cli.exists():
            return ""
        result = subprocess.run(
            [str(gbrain_cli), "search", f"{code} {name}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()[:500]
    except Exception:
        pass
    return ""


async def _run_scheduled_report(report_type):
    """异步运行定时报告"""
    from data.quote import get_batch_quotes

    setting_key = REPORT_SETTINGS.get(report_type)
    if not setting_key:
        logger.warning("未知报告类型: %s", report_type)
        return

    enabled = _get_setting(setting_key)
    if enabled not in ("true", "1", "True", "yes"):
        # 策略复盘默认启用
        if report_type != "review":
            logger.info("📄 定时报告 [%s] 未启用，跳过", report_type)
            return

    stocks = _get_watchlist_stocks()
    if not stocks:
        logger.info("📄 无自选股，跳过报告 [%s]", report_type)
        return

    codes = [s["code"] for s in stocks]
    quotes = await get_batch_quotes(codes)

    if not quotes:
        logger.warning("📄 获取行情失败，跳过报告 [%s]", report_type)
        return

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    results = []

    db = _get_db()
    try:
        if report_type == "review":
            # 策略复盘：比较当前价格与策略目标，记录状态变化
            for stock in stocks:
                q = quotes.get(stock["code"])
                if not q:
                    continue

                price = q.get("price", 0)
                change_pct = q.get("change_pct", 0)

                # 获取策略目标价
                target_buy = None
                target_sell = None
                stop_loss = None
                try:
                    row = db.execute(
                        "SELECT target_buy_price, target_sell_price, stop_loss_price FROM watchlist WHERE code = ?",
                        (stock["code"],)
                    ).fetchone()
                    if row:
                        target_buy = row[0]
                        target_sell = row[1]
                        stop_loss = row[2]
                except Exception:
                    pass

                review = {
                    "code": stock["code"],
                    "name": stock["name"],
                    "price": price,
                    "change_pct": change_pct,
                    "target_buy_price": target_buy,
                    "target_sell_price": target_sell,
                    "stop_loss_price": stop_loss,
                    "signal": "HOLD",
                    "advice": "观望",
                    "detail": "",
                }

                # 比较价格与策略目标
                if target_buy and price:
                    if price <= target_buy:
                        review["signal"] = "BUY"
                        review["advice"] = "到达买入区间"
                        review["detail"] = f"现价{price:.2f}≤目标买入{target_buy:.2f}"
                    elif price <= target_buy * 1.03:
                        review["signal"] = "WATCH"
                        review["advice"] = "接近买点"
                        review["detail"] = f"现价{price:.2f}距买入价{((price/target_buy-1)*100):.1f}%"

                if target_sell and price:
                    if price >= target_sell:
                        review["signal"] = "SELL"
                        review["advice"] = "到达卖出区间"
                        review["detail"] = f"现价{price:.2f}≥目标卖出{target_sell:.2f}"

                if stop_loss and price:
                    if price <= stop_loss:
                        review["signal"] = "SELL"
                        review["advice"] = "触及止损"
                        review["detail"] = f"现价{price:.2f}≤止损价{stop_loss:.2f}"

                results.append(review)

                # 保存到 analysis_reports
                summary = json.dumps(review, ensure_ascii=False)
                db.execute(
                    "INSERT INTO analysis_reports "
                    "(code, task_id, signal, final_decision, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        stock["code"],
                        f"scheduled_review_{now}",
                        review["signal"],
                        summary,
                        now,
                    )
                )

                # 记录状态变化到 strategy_records
                if review["signal"] in ("BUY", "SELL"):
                    db.execute(
                        "INSERT INTO strategy_records (code, old_state, new_state, reason, changed_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (stock["code"], "watch", review["signal"].lower(), review["advice"], now)
                    )

            db.commit()
            logger.info("📊 策略复盘完成，共 %d 只股票", len(results))
        else:
            for stock in stocks:
                q = quotes.get(stock["code"])
                if not q:
                    continue

                evaluation = _run_l1_evaluation(stock, q)

                # L3: gbrain read-enhancement for L1
                gbrain_ctx = _gbrain_search_for_l1(stock["code"], stock["name"])
                if gbrain_ctx:
                    evaluation["gbrain_context"] = gbrain_ctx

                results.append(evaluation)

                # 保存到 analysis_reports
                summary = json.dumps(evaluation, ensure_ascii=False)
                db.execute(
                    "INSERT INTO analysis_reports "
                    "(code, task_id, signal, final_decision, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        stock["code"],
                        f"scheduled_{report_type}_{now}",
                        evaluation["signal"],
                        summary,
                        now,
                    )
                )

                # 保存 per-stock daily_pnl 记录
                code6 = stock["code"][:6]
                today = now[:10]
                price = q.get("price", 0)
                prev_close = q.get("prev_close", 0)
                total_shares = stock.get("total_shares", 0)
                avg_cost = stock.get("avg_cost", 0)
                stock_pnl = 0
                if total_shares > 0 and prev_close > 0 and price > 0:
                    stock_pnl = round((price - prev_close) * total_shares, 2)
                db.execute(
                    "INSERT OR REPLACE INTO daily_pnl (date, code6, pnl, close_price, shares) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (today, code6, stock_pnl, price, total_shares)
                )

            db.commit()
            logger.info(
                "📊 定时报告 [%s] 完成，共 %d 只股票",
                report_type, len(results)
            )
    except Exception as e:
        logger.error("保存定时报告失败: %s", e)
    finally:
        db.close()

    return results


async def run_scheduled_report(report_type: str):
    """异步入口 — 运行定时报告 + 自动触发L2深度分析"""
    try:
        results = await _run_scheduled_report(report_type)
        # Auto-trigger L2 for stocks with actionable signals
        if results:
            await _trigger_l2_for_actionable_signals(results, report_type)
        # gbrain 回写（fire-and-forget）
        _write_report_to_gbrain(report_type, results)
    except Exception as e:
        logger.error("定时报告任务异常 [%s]: %s", report_type, e)


# gbrain slug 映射
_GBRAIN_SLUGS = {
    "open":     lambda d: f"daily-report/{d}-am",
    "am_close": lambda d: f"daily-report/{d}-noon",
    "pm_open":  lambda d: f"daily-report/{d}-pm",
    "close":    lambda d: f"daily-report/{d}-close",
    "review":   lambda d: f"strategy-review/{d}",
}


def _write_report_to_gbrain(report_type: str, results: list):
    """将定时报告摘要写入 gbrain（fire-and-forget Popen）"""
    if not results:
        return
    slug_fn = _GBRAIN_SLUGS.get(report_type)
    if not slug_fn:
        return
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    slug = slug_fn(today)
    title_map = {
        "open": f"早盘报告 {today}",
        "am_close": f"午盘报告 {today}",
        "pm_open": f"午后报告 {today}",
        "close": f"收盘报告 {today}",
        "review": f"策略复盘 {today}",
    }
    title = title_map.get(report_type, f"{report_type} {today}")
    lines = [f"# {title}", ""]
    for r in results[:20]:  # 最多20只
        sig = r.get("signal", "HOLD")
        name = r.get("name", "")
        code = r.get("code", "")
        detail = r.get("detail", "")[:100]
        lines.append(f"- **{name}({code})** [{sig}] {detail}")
    content = "\n".join(lines)
    _gbrain_save_report(slug, title, content)


async def _trigger_l2_for_actionable_signals(results: list, report_type: str):
    """Trigger L2 deep analysis for stocks with actionable L1 signals.
    Only fires for BUY/SELL signals, or WATCH with |change%| >= 7.
    """
    from api.ai_api import trigger_l2_for_stock

    trade_date = datetime.datetime.now().strftime('%Y-%m-%d')
    triggered = 0

    for r in results:
        signal = r.get("signal", "HOLD")
        code = r.get("code")
        if not code:
            continue

        should_trigger = False
        reason = ""

        if signal in ("BUY", "SELL"):
            should_trigger = True
            reason = f"signal={signal}"
        elif signal == "WATCH" and abs(r.get("change_pct", 0)) >= 7:
            should_trigger = True
            reason = f"大幅异动 {r['change_pct']:+.1f}%"

        if should_trigger:
            task_id = await trigger_l2_for_stock(code, trade_date)
            if task_id:
                triggered += 1
                logger.info(
                    "🔬 L2自动触发: %s(%s) %s (来自%s报告)",
                    r.get("name", ""), code, reason, report_type,
                )

    if triggered:
        logger.info("📊 [%s] 报告触发了 %d 个L2深度分析", report_type, triggered)
