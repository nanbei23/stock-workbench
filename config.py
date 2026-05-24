import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "workbench.db"
CACHE_DIR = Path.home() / ".tradingagents" / "cache"
GBRAIN_BIN = Path.home() / ".bun" / "bin" / "gbrain"

# Server
HOST = "0.0.0.0"
PORT = 8000

# Fee model
COMMISSION_RATE = 0.0003    # 佣金万3
COMMISSION_MIN = 5.0        # 最低5元
STAMP_TAX_RATE = 0.0005     # 印花税0.5‰（卖出）
TRANSFER_FEE_RATE = 0.00001 # 过户费0.01‰

# AI Engine
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-3db9867965124686b3ba53596119691e")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
TA_MAX_CONCURRENT = 2
TA_MAX_QUEUE = 5
TA_TIMEOUT = 480  # 8分钟

# LLM models
DEEP_THINK_MODEL = "deepseek-v4-pro"
QUICK_THINK_MODEL = "deepseek-v4-flash"
OUTPUT_LANGUAGE = "Chinese"

# Cache TTL (seconds)
CACHE_TTL = {
    "quotes": 1, "orderbook": 0, "klines": 300,
    "fundamentals": 86400, "news": 3600, "research": 3600,
    "signal": 300, "indicators": 300,
}

# Strategy
STRATEGY_STATES = ["watch", "near_buy", "buy", "near_sell", "sell"]

# Anomaly thresholds
ANOMALY_THRESHOLDS = {
    "price_change_pct": 3.0,
    "volume_ratio": 2.0,
    "north_flow_minute": 5.0,
    "dragon_tiger_net": 5000,
}
