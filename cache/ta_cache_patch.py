"""
TradingAgents 缓存注入 — Monkey-patch TradingAgents-astock 数据函数
PRD §共享数据缓存层设计: 在调用前查L1缓存，调用后写入L2缓存

Usage:
    from cache.ta_cache_patch import patch_tradingagents_cache
    patch_tradingagents_cache()  # 单次调用即可，幂等

注意：此为best-effort优化，TradingAgents未安装时静默跳过。
"""
import logging

logger = logging.getLogger(__name__)

_patched = False


def patch_tradingagents_cache():
    """Monkey-patch TradingAgents-astock 的数据获取函数，注入 SharedCache。
    
    策略：在每个目标函数的入口查缓存（L1 + L2），命中则直接返回；
    未命中则执行原函数并将结果写入缓存。
    
    幂等：多次调用只生效一次。
    """
    global _patched
    if _patched:
        return
    _patched = True

    try:
        # 尝试导入 SharedCache
        from cache.shared_cache import cache
    except ImportError:
        logger.warning("cache.shared_cache not available, skipping TA cache patch")
        return

    # 定义需要patch的函数路径 -> 缓存分类
    # TradingAgents-astock 的数据获取模块可能在不同路径，逐一尝试
    _patch_targets = [
        # (模块路径, 函数名, 缓存分类, 参数中code字段名)
        ("tradingagents.dataflows.a_stock", "get_stock_kline", "klines", "stock_code"),
        ("tradingagents.dataflows.a_stock", "get_stock_info", "fundamentals", "stock_code"),
        ("tradingagents.dataflows.a_stock", "get_stock_news", "news", "stock_code"),
        ("tradingagents.dataflows.a_stock", "get_stock_fund_flow", "signal", "stock_code"),
        ("tradingagents.dataflows.a_stock", "get_stock_indicators", "indicators", "stock_code"),
        # 也可能在其他路径
        ("a_stock", "get_stock_kline", "klines", "stock_code"),
        ("a_stock", "get_stock_info", "fundamentals", "stock_code"),
        ("a_stock", "get_stock_news", "news", "stock_code"),
    ]

    patched_count = 0

    for module_path, func_name, category, code_param in _patch_targets:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            original_fn = getattr(mod, func_name, None)
            if original_fn is None:
                continue

            # 避免重复patch
            if getattr(original_fn, '_cache_patched', False):
                continue

            cat = category
            cp = code_param

            def _make_wrapper(orig_fn, cat, cp):
                def _wrapped(*args, **kwargs):
                    # 尝试从参数中提取code
                    code = kwargs.get(cp) or (args[0] if args else None)
                    if code and isinstance(code, str):
                        # 查缓存
                        cached = cache.read(cat, code)
                        if cached is not None:
                            return cached
                    
                    # 缓存未命中，执行原函数
                    result = orig_fn(*args, **kwargs)
                    
                    # 写缓存
                    if code and isinstance(code, str) and result is not None:
                        cache.write(cat, code, result)
                    
                    return result
                
                _wrapped._cache_patched = True
                _wrapped.__name__ = orig_fn.__name__
                _wrapped.__qualname__ = orig_fn.__qualname__
                return _wrapped

            wrapper = _make_wrapper(original_fn, cat, cp)
            setattr(mod, func_name, wrapper)
            patched_count += 1
            logger.info("Patched %s.%s with %s cache", module_path, func_name, cat)

        except (ImportError, AttributeError, Exception) as e:
            # 静默跳过：模块可能不存在
            logger.debug("Skip patching %s.%s: %s", module_path, func_name, e)
            continue

    if patched_count > 0:
        logger.info("TradingAgents cache injection: patched %d functions", patched_count)
    else:
        logger.info("TradingAgents cache injection: no functions patched (modules may not be installed)")
