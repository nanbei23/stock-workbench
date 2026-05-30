"""APScheduler 定时任务管理 — 注册所有定时任务"""
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from scheduler.conditional_order_checker import check_conditional_orders
from scheduler.anomaly_checker import check_anomalies
from scheduler.report_runner import run_scheduled_report
from scheduler.signal_tracker import get_open_tracking_codes, update_prices
from services import ai_report_service
from services import settings_service

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


def _is_trading_hours():
    """判断当前是否在A股交易时间（粗略判断，用于间隔任务内部跳过）"""
    now = datetime.now()
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
    """每天23:59清除当天的异动日志"""
    try:
        count = await ai_report_service.clear_anomalies_for_date()
        logger.info("🗑️ 已清除当天异动日志 %d 条", count)
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
