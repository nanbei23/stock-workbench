"""定时任务调度器 — APScheduler"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from scheduler.signal_tracker import get_open_tracking_codes, update_prices
from data.helpers import tencent_quote_batch

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def update_signal_tracking_prices():
    """每日15:30更新信号跟踪价格"""
    try:
        codes = get_open_tracking_codes()
        if not codes:
            logger.info("无持仓中信号，跳过更新")
            return

        logger.info("更新信号跟踪价格: %s", codes)

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
            logger.info("信号跟踪更新完成: %s", result)
        else:
            logger.warning("未获取到任何行情数据")
    except Exception as e:
        logger.error("信号跟踪定时任务失败: %s", e)


def start_scheduler():
    """启动调度器"""
    # 每个交易日15:30更新信号跟踪价格
    scheduler.add_job(
        update_signal_tracking_prices,
        CronTrigger(hour=15, minute=30, day_of_week='mon-fri'),
        id='signal_tracking_update',
        name='信号跟踪价格更新',
        replace_existing=True
    )

    scheduler.start()
    logger.info("调度器已启动，注册任务: 信号跟踪价格更新 (每日15:30)")


def stop_scheduler():
    """停止调度器"""
    scheduler.shutdown()
    logger.info("调度器已停止")
