import sys
sys.path.insert(0, '.')

# Test data layer imports
try:
    from data.helpers import (
        get_session, close_session, eastmoney_datacenter, tencent_quote_batch,
        get_prefix, _pure_code, _secid, _tencent_code, _safe_float,
        HEADERS, UA, PUSH2_URL, PUSH2_HIS_URL, PUSH2_MINUTE_URL,
        TENCENT_QUOTE_URL, DATACENTER_URL
    )
    print("OK data.helpers")
except Exception as e:
    print(f"FAIL data.helpers: {e}")

try:
    from data.quote import get_realtime_quote, get_batch_quotes, get_orderbook
    print("OK data.quote")
except Exception as e:
    print(f"FAIL data.quote: {e}")

try:
    from data.kline import get_kline, get_kline_with_ma
    print("OK data.kline")
except Exception as e:
    print(f"FAIL data.kline: {e}")

try:
    from data.research import get_reports, get_eps_forecast
    print("OK data.research")
except Exception as e:
    print(f"FAIL data.research: {e}")

try:
    from data.signal import (
        get_concept_blocks, get_hot_reasons, get_northbound,
        get_fund_flow_minute, get_dragon_tiger, get_lockup_expiry,
        get_industry_ranking, get_all_signals
    )
    print("OK data.signal")
except Exception as e:
    print(f"FAIL data.signal: {e}")

try:
    from data.fund import (
        get_margin_trading, get_block_trade, get_holder_change,
        get_dividend_history, get_fund_flow_120d, get_all_fund_data
    )
    print("OK data.fund")
except Exception as e:
    print(f"FAIL data.fund: {e}")

try:
    from data.news import (
        get_stock_news, get_cls_telegraph, get_global_news, get_global_news_724
    )
    print("OK data.news")
except Exception as e:
    print(f"FAIL data.news: {e}")

try:
    from data.info import get_stock_info, get_business_segments
    print("OK data.info")
except Exception as e:
    print(f"FAIL data.info: {e}")

try:
    from data.announce import get_announcements
    print("OK data.announce")
except Exception as e:
    print(f"FAIL data.announce: {e}")

# Test that public functions are async
import asyncio
import inspect

async def _verify():
    from data.quote import get_realtime_quote, get_batch_quotes
    from data.kline import get_kline, get_kline_with_ma
    from data.research import get_reports, get_eps_forecast
    from data.signal import get_all_signals, get_concept_blocks, get_northbound, get_industry_ranking
    from data.fund import get_all_fund_data, get_margin_trading, get_fund_flow_120d
    from data.news import get_stock_news, get_cls_telegraph, get_global_news_724
    from data.info import get_stock_info, get_business_segments
    from data.announce import get_announcements

    # Async functions
    async_funcs = [
        get_realtime_quote, get_batch_quotes,
        get_kline_with_ma,
        get_reports, get_eps_forecast,
        get_all_signals, get_concept_blocks, get_northbound, get_industry_ranking,
        get_all_fund_data, get_margin_trading, get_fund_flow_120d,
        get_stock_news, get_cls_telegraph, get_global_news, get_global_news_724,
        get_stock_info, get_business_segments,
        get_announcements,
        eastmoney_datacenter, tencent_quote_batch,
    ]

    for fn in async_funcs:
        if not inspect.iscoroutinefunction(fn):
            print(f"NOT ASYNC: {fn.__module__}.{fn.__name__}")

    # Sync functions (should remain sync)
    sync_funcs = [get_kline, get_orderbook]
    for fn in sync_funcs:
        if inspect.iscoroutinefunction(fn):
            print(f"SHOULD BE SYNC: {fn.__module__}.{fn.__name__}")

    print("OK async/sync verification passed")

asyncio.run(_verify())
