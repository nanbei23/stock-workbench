"""APScheduler 定时任务管理 — 注册所有定时任务"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from scheduler.conditional_order_checker import check_conditional_orders
from scheduler.anomaly_checker import check_anomalies
from scheduler.report_runner import run_scheduled_report
from scheduler.signal_tracker import get_open_tracking_codes, update_prices
from services import ai_report_service
from services import holding_review_service
from services import portfolio_service
from services import settings_service

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
CN_TZ = ZoneInfo("Asia/Shanghai")


def _is_trading_hours():
    """判断当前是否在A股交易时间（粗略判断，用于间隔任务内部跳过）"""
    now = datetime.now(CN_TZ)
    weekday = now.weekday()
    if weekday >= 5:  # 周六日
        return False

    hour, minute = now.hour, now.minute
    t = hour * 60 + minute

    # 上午 9:25 ~ 11:35
    if 9 * 60 + 25 <= t <= 11 * 60 + 35:
        return True
    # 下午 12:55 ~ 15:05
    if 12 * 60 + 55 <= t <= 15 * 60 + 5:
        return True

    return False


async def conditional_order_job():
    """条件单检查任务（带交易时间过滤）"""
    if not _is_trading_hours():
        return
    await check_conditional_orders()


async def anomaly_job():
    """异动检测任务（带交易时间过滤 + 开关检查）"""
    if not _is_trading_hours():
        return
    # 检查异动监控开关
    try:
        enabled = settings_service.get_setting("anomaly_monitor_enabled")["value"]
        if enabled != "true":
            return
    except Exception:
        pass
    await check_anomalies()


async def clear_anomaly_logs_job():
    """每天清除历史异动日志，仅保留当天看盘记录。"""
    try:
        count = await ai_report_service.clear_stale_anomalies()
        logger.info("🗑️ 已清除历史异动日志 %d 条", count)
    except Exception as e:
        logger.error("清除异动日志失败: %s", e)


async def signal_tracking_job():
    """每日15:30更新信号跟踪价格"""
    try:
        from data.helpers import tencent_quote_batch

        codes = get_open_tracking_codes()
        if not codes:
            logger.info("无持仓中信号，跳过更新")
            return

        logger.info("📊 更新信号跟踪价格: %s", codes)

        # 批量获取行情
        price_map = {}
        for code in codes:
            try:
                quotes = await tencent_quote_batch([code])
                if quotes and code in quotes:
                    price_map[code] = quotes[code].get("price", 0)
            except Exception as e:
                logger.warning("获取 %s 行情失败: %s", code, e)

        if price_map:
            result = update_prices(price_map)
            logger.info("✅ 信号跟踪更新完成: %s", result)
        else:
            logger.warning("未获取到任何行情数据")
    except Exception as e:
        logger.error("❌ 信号跟踪定时任务失败: %s", e)


async def daily_pnl_snapshot_job():
    """每日收盘后写入持仓盈亏日历快照。"""
    try:
        result = await portfolio_service.ensure_daily_pnl_snapshot()
        logger.info("持仓盈亏快照写入完成: %s", result)
    except Exception as e:
        logger.error("持仓盈亏快照写入失败: %s", e)


def _setting_value(key: str, default: str = "") -> str:
    try:
        value = settings_service.get_setting(key)["value"]
        return default if value in (None, "") else str(value)
    except Exception:
        return default


def _setting_enabled(key: str, default: bool = False) -> bool:
    value = _setting_value(key, "true" if default else "false")
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def daily_decision_report_job(now: datetime | None = None):
    """Generate the daily AI decision report once at the configured time."""
    now = now or datetime.now(CN_TZ)
    if now.weekday() >= 5:
        return
    if not _setting_enabled("daily_decision_auto_enabled", False):
        return
    target_time = _setting_value("daily_decision_auto_time", "15:20").strip() or "15:20"
    if now.strftime("%H:%M") != target_time:
        return
    account_id = _setting_value("daily_decision_account_id", "default").strip() or "default"
    date_text = now.strftime("%Y-%m-%d")
    if await holding_review_service.review_exists_for_date(date_text=date_text, account_id=account_id):
        logger.info("每日 AI 决策报告已存在，跳过: %s %s", account_id, date_text)
        return
    try:
        result = await holding_review_service.run_scheduled_daily_decision_report(account_id=account_id, date_text=date_text)
        logger.info("每日 AI 决策报告调度完成: %s", result.get("review_id"))
    except Exception as e:
        logger.error("每日 AI 决策报告调度失败: %s", e)


async def report_job(report_type: str):
    """定时报告任务"""
    logger.info("⏰ 触发定时报告: %s", report_type)
    await run_scheduled_report(report_type)


def setup_scheduler():
    """注册所有定时任务并启动调度器"""
    # ── 条件单检查：每30秒（交易时段内） ──
    scheduler.add_job(
        conditional_order_job,
        trigger=IntervalTrigger(seconds=30),
        id="conditional_order_checker",
        name="条件单检查",
        replace_existing=True,
    )

    # ── 异动检测：每60秒（交易时段内） ──
    scheduler.add_job(
        anomaly_job,
        trigger=IntervalTrigger(seconds=60),
        id="anomaly_checker",
        name="异动检测",
        replace_existing=True,
    )

    # ── 定时AI报告 ──
    # 开盘报告 09:30
    scheduler.add_job(
        report_job,
        trigger=CronTrigger(hour=9, minute=30),
        args=["open"],
        id="report_open",
        name="开盘报告",
        replace_existing=True,
    )

    # 上午收盘 11:30
    scheduler.add_job(
        report_job,
        trigger=CronTrigger(hour=11, minute=30),
        args=["am_close"],
        id="report_am_close",
        name="上午收盘报告",
        replace_existing=True,
    )

    # 下午开盘 13:00
    scheduler.add_job(
        report_job,
        trigger=CronTrigger(hour=13, minute=0),
        args=["pm_open"],
        id="report_pm_open",
        name="下午开盘报告",
        replace_existing=True,
    )

    # 收盘报告 15:00
    scheduler.add_job(
        report_job,
        trigger=CronTrigger(hour=15, minute=0),
        args=["close"],
        id="report_close",
        name="收盘报告",
        replace_existing=True,
    )

    # 策略复盘 15:05
    scheduler.add_job(
        report_job,
        trigger=CronTrigger(hour=15, minute=5),
        args=["review"],
        id="report_review",
        name="策略复盘",
        replace_existing=True,
    )

    # ── 持仓盈亏日历快照：每日15:10（收盘后） ──
    scheduler.add_job(
        daily_pnl_snapshot_job,
        trigger=CronTrigger(hour=15, minute=10, day_of_week='mon-fri'),
        id="daily_pnl_snapshot",
        name="持仓盈亏快照",
        replace_existing=True,
    )

    # ── 每日 AI 决策报告：每分钟检查一次设置，到点后只生成一次 ──
    scheduler.add_job(
        daily_decision_report_job,
        trigger=IntervalTrigger(minutes=1),
        id="daily_decision_report_guard",
        name="每日 AI 决策报告调度检查",
        replace_existing=True,
    )

    # ── 每天23:59清除当天异动日志 ──
    scheduler.add_job(
        clear_anomaly_logs_job,
        trigger=CronTrigger(hour=23, minute=59),
        id="clear_anomaly_logs",
        name="清除当天异动",
        replace_existing=True,
    )

    # ── 信号跟踪价格更新：每日15:30（收盘后30分钟） ──
    scheduler.add_job(
        signal_tracking_job,
        trigger=CronTrigger(hour=15, minute=30, day_of_week='mon-fri'),
        id="signal_tracking_update",
        name="信号跟踪价格更新",
        replace_existing=True,
    )

    scheduler.start()

    # 打印已注册的任务
    jobs = scheduler.get_jobs()
    for job in jobs:
        logger.info("📋 已注册任务: [%s] %s — %s", job.id, job.name, job.trigger)

    return scheduler
