import py_compile
files = [
    'data/helpers.py',
    'data/quote.py',
    'data/kline.py',
    'data/research.py',
    'data/signal.py',
    'data/fund.py',
    'data/news.py',
    'data/info.py',
    'data/announce.py',
    'api/quote_api.py',
    'api/layer_api.py',
    'api/news_api.py',
    'api/strategy_api.py',
    'api/portfolio_api.py',
    'scheduler/anomaly_checker.py',
    'scheduler/conditional_order_checker.py',
    'scheduler/report_runner.py',
    'app.py',
]
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        print(f'OK {f}')
    except py_compile.PyCompileError as e:
        print(f'FAIL {f}: {e}')
