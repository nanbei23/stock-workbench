"""SQLite数据库初始化 — 9张表"""
import aiosqlite
from pathlib import Path
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    broker TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS watchlist (
    code TEXT PRIMARY KEY,
    name TEXT,
    group_name TEXT DEFAULT "默认",
    sort_order INTEGER DEFAULT 0,
    strategy_state TEXT DEFAULT "watch",
    target_buy_price REAL,
    target_sell_price REAL,
    stop_loss_price REAL,
    notes TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    strategy_state_updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio (
    code TEXT PRIMARY KEY,
    name TEXT,
    total_shares INTEGER DEFAULT 0,
    available_shares INTEGER DEFAULT 0,
    avg_cost REAL DEFAULT 0,
    current_price REAL DEFAULT 0,
    market_value REAL DEFAULT 0,
    unrealized_pnl REAL DEFAULT 0,
    unrealized_pnl_pct REAL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    account_id TEXT DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    direction TEXT NOT NULL,
    price REAL NOT NULL,
    shares INTEGER NOT NULL,
    amount REAL NOT NULL,
    commission REAL DEFAULT 0,
    stamp_tax REAL DEFAULT 0,
    transfer_fee REAL DEFAULT 0,
    total_cost REAL DEFAULT 0,
    trade_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    account_id TEXT DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS conditional_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    condition_type TEXT NOT NULL,
    target_price REAL NOT NULL,
    action TEXT NOT NULL,
    shares INTEGER,
    status TEXT DEFAULT "pending",
    triggered_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    notes TEXT,
    account_id TEXT DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS strategy_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    old_state TEXT,
    new_state TEXT NOT NULL,
    reason TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    account_id TEXT DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS strategy_params (
    code6 TEXT PRIMARY KEY,
    budget REAL DEFAULT 0,
    entry_price REAL DEFAULT 0,
    drop_pct REAL DEFAULT 3,
    add_mult REAL DEFAULT 1,
    bounce_pct REAL DEFAULT 5,
    sell_pct REAL DEFAULT 50,
    lot_size INTEGER DEFAULT 100,
    target_profit_pct REAL DEFAULT 5,
    low_water_manual REAL,
    buy_prices TEXT DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    account_id TEXT DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS analysis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    task_id TEXT UNIQUE,
    signal TEXT,
    confidence REAL,
    risk_score REAL,
    market_report TEXT,
    sentiment_report TEXT,
    news_report TEXT,
    fundamentals_report TEXT,
    policy_report TEXT,
    hot_money_report TEXT,
    lockup_report TEXT,
    investment_debate TEXT,
    risk_debate TEXT,
    final_decision TEXT,
    trader_plan TEXT,
    raw_state TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration_seconds REAL
);

CREATE TABLE IF NOT EXISTS anomaly_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    anomaly_type TEXT NOT NULL,
    description TEXT,
    severity TEXT DEFAULT "info",
    l1_suggestion TEXT,
    l2_task_id TEXT,
    l2_result TEXT,
    gbrain_context TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    date TEXT NOT NULL,
    code6 TEXT DEFAULT '',
    total_assets REAL,
    cash REAL,
    market_value REAL,
    realized_pnl REAL,
    unrealized_pnl REAL,
    total_pnl REAL,
    total_pnl_pct REAL,
    pnl REAL,
    close_price REAL,
    shares INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, code6)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    target_buy_price REAL,
    plan_shares INTEGER DEFAULT 100,
    plan_total_cost REAL,
    reason TEXT,
    strategy_state TEXT DEFAULT "watch",
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    account_id TEXT DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS buy_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    price REAL NOT NULL,
    shares INTEGER DEFAULT 0,
    reason TEXT,
    status TEXT DEFAULT "pending",
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS news_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code6 TEXT,
    source TEXT,
    title TEXT,
    content TEXT,
    url TEXT,
    sentiment TEXT DEFAULT 'neutral',
    published_at TEXT,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

async def init_db():
    """初始化数据库，创建所有表"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.executescript(SCHEMA)
        await db.execute("INSERT OR IGNORE INTO accounts (id, name) VALUES ('default', '默认账户')")
        await db.commit()
    return True

async def get_db():
    """获取数据库连接"""
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    return db
