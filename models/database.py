"""SQLite数据库初始化 — 9张表"""
import aiosqlite
from pathlib import Path
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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
    duration_seconds REAL,
    market_snapshot TEXT,
    fact_check TEXT,
    bystander_verify TEXT,
    depth TEXT DEFAULT 'standard',
    model_mode TEXT DEFAULT 'balanced'
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

CREATE TABLE IF NOT EXISTS cash_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT DEFAULT 'default',
    direction TEXT NOT NULL DEFAULT 'adjust',
    amount REAL NOT NULL,
    balance_after REAL NOT NULL,
    source TEXT DEFAULT 'manual',
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
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

CREATE TABLE IF NOT EXISTS trading_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    direction TEXT NOT NULL DEFAULT 'buy',  -- buy / sell
    plan_type TEXT NOT NULL DEFAULT 'watch', -- watch / near_target / conditional
    target_price REAL,
    condition_type TEXT DEFAULT 'price_lte', -- price_lte / price_gte / change_pct_gte / change_pct_lte
    plan_shares INTEGER DEFAULT 100,
    plan_total_cost REAL,
    status TEXT DEFAULT 'pending',           -- pending / triggered / filled / cancelled
    reason TEXT,
    triggered_at TIMESTAMP,
    expires_at TIMESTAMP,
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

CREATE TABLE IF NOT EXISTS analysis_progress (
    task_id TEXT NOT NULL,
    code TEXT NOT NULL,
    stage_id TEXT NOT NULL,
    report_text TEXT,
    completed_at TEXT,
    PRIMARY KEY (task_id, stage_id)
);

CREATE TABLE IF NOT EXISTS signal_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    signal TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    entry_price REAL NOT NULL,
    target_price REAL,
    stop_loss_price REAL,
    current_price REAL,
    highest_price REAL,
    lowest_price REAL,
    exit_price REAL,
    exit_date TEXT,
    exit_reason TEXT,
    pnl_pct REAL,
    hold_days INTEGER,
    benchmark_return REAL,
    excess_return REAL,
    status TEXT DEFAULT 'open',
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analysis_tasks (
    task_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    queue_status TEXT,
    depth TEXT DEFAULT 'standard',
    selected_analysts TEXT,
    debate_rounds INTEGER,
    risk_rounds INTEGER,
    stages TEXT DEFAULT '{}',
    result TEXT,
    error TEXT,
    started_at TEXT,
    completed_at TEXT,
    elapsed REAL,
    payload TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS hermes_console_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    message TEXT NOT NULL,
    draft_json TEXT,
    result_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS hermes_tool_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    draft_id TEXT,
    tool TEXT NOT NULL,
    args_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS hermes_tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    source_text TEXT,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'waiting_confirm',
    summary TEXT,
    draft_json TEXT,
    result_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS hermes_task_steps (
    task_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,
    action TEXT,
    title TEXT,
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'waiting_confirm',
    payload_json TEXT,
    tool_json TEXT,
    impact_json TEXT,
    result_json TEXT,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (task_id, step_id)
);

CREATE INDEX IF NOT EXISTS idx_signal_tracking_status ON signal_tracking(status);
CREATE INDEX IF NOT EXISTS idx_tracking_code ON signal_tracking(code);
CREATE INDEX IF NOT EXISTS idx_tracking_signal ON signal_tracking(signal);
CREATE INDEX IF NOT EXISTS idx_tracking_date ON signal_tracking(signal_date);
CREATE INDEX IF NOT EXISTS idx_trades_code_time ON trades(code, trade_time);
CREATE INDEX IF NOT EXISTS idx_conditional_orders_status ON conditional_orders(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_conditional_orders_code_status ON conditional_orders(code, status);
CREATE INDEX IF NOT EXISTS idx_analysis_reports_code_created ON analysis_reports(code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_logs_code_created ON anomaly_logs(code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_cache_code_cached ON news_cache(code6, cached_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_cache_url ON news_cache(url);
CREATE INDEX IF NOT EXISTS idx_analysis_tasks_status_updated ON analysis_tasks(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_tasks_code_updated ON analysis_tasks(code, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_hermes_console_session_created
    ON hermes_console_events(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hermes_tool_runs_session_created
    ON hermes_tool_runs(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hermes_tasks_session_updated
    ON hermes_tasks(session_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_hermes_task_steps_status
    ON hermes_task_steps(task_id, status);
CREATE INDEX IF NOT EXISTS idx_cash_ledger_account_created
    ON cash_ledger(account_id, created_at DESC);
"""

MIGRATIONS = [
    (
        1,
        "baseline_schema_tracking",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    (
        2,
        "analysis_task_state",
        """
        CREATE TABLE IF NOT EXISTS analysis_tasks (
            task_id TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            name TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            queue_status TEXT,
            depth TEXT DEFAULT 'standard',
            selected_analysts TEXT,
            debate_rounds INTEGER,
            risk_rounds INTEGER,
            stages TEXT DEFAULT '{}',
            result TEXT,
            error TEXT,
            started_at TEXT,
            completed_at TEXT,
            elapsed REAL,
            payload TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_analysis_tasks_status_updated
            ON analysis_tasks(status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_analysis_tasks_code_updated
            ON analysis_tasks(code, updated_at DESC);
        """,
    ),
    (
        3,
        "hermes_tool_runs",
        """
        CREATE TABLE IF NOT EXISTS hermes_tool_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            draft_id TEXT,
            tool TEXT NOT NULL,
            args_json TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            result_json TEXT,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            confirmed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_hermes_tool_runs_session_created
            ON hermes_tool_runs(session_id, created_at DESC);
        """,
    ),
    (
        4,
        "hermes_task_timeline",
        """
        CREATE TABLE IF NOT EXISTS hermes_tasks (
            task_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            draft_id TEXT NOT NULL,
            source_text TEXT,
            title TEXT,
            status TEXT NOT NULL DEFAULT 'waiting_confirm',
            summary TEXT,
            draft_json TEXT,
            result_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS hermes_task_steps (
            task_id TEXT NOT NULL,
            step_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            kind TEXT NOT NULL,
            action TEXT,
            title TEXT,
            summary TEXT,
            status TEXT NOT NULL DEFAULT 'waiting_confirm',
            payload_json TEXT,
            tool_json TEXT,
            impact_json TEXT,
            result_json TEXT,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (task_id, step_id)
        );
        CREATE INDEX IF NOT EXISTS idx_hermes_tasks_session_updated
            ON hermes_tasks(session_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_hermes_task_steps_status
            ON hermes_task_steps(task_id, status);
        """,
    ),
    (
        5,
        "cash_ledger",
        """
        CREATE TABLE IF NOT EXISTS cash_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT DEFAULT 'default',
            direction TEXT NOT NULL DEFAULT 'adjust',
            amount REAL NOT NULL,
            balance_after REAL NOT NULL,
            source TEXT DEFAULT 'manual',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_cash_ledger_account_created
            ON cash_ledger(account_id, created_at DESC);
        """,
    ),
]


async def run_migrations(db):
    """Apply ordered schema migrations idempotently."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    rows = await db.execute_fetchall("SELECT version FROM schema_migrations")
    applied = {row[0] for row in rows}
    for version, name, sql in MIGRATIONS:
        if version in applied:
            continue
        await db.executescript(sql)
        await db.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
            (version, name),
        )


async def init_db():
    """初始化数据库，创建所有表"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.executescript(SCHEMA)
        await run_migrations(db)
        await db.execute("INSERT OR IGNORE INTO accounts (id, name) VALUES ('default', '默认账户')")
        await db.commit()
    return True

async def get_db():
    """获取数据库连接"""
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    return db
