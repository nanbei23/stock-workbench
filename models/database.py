"""SQLite数据库初始化 — 9张表"""
import aiosqlite
import hashlib
from pathlib import Path
from config import DB_PATH

DEFAULT_LOGIN_USER_ID = "admin"
LEGACY_LOGIN_USER_ID = "local_owner"
DEFAULT_LOGIN_USERNAME = "admin"
DEFAULT_LOGIN_PASSWORD = "123456"

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

CREATE TABLE IF NOT EXISTS login_users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT DEFAULT '',
    display_name TEXT,
    role TEXT DEFAULT 'owner',
    status TEXT DEFAULT 'active',
    default_securities_account_id TEXT DEFAULT 'default',
    must_change_credentials INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS login_sessions (
    session_id TEXT PRIMARY KEY,
    login_user_id TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT,
    last_seen_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(login_user_id) REFERENCES login_users(id)
);

CREATE TABLE IF NOT EXISTS securities_accounts (
    id TEXT PRIMARY KEY,
    login_user_id TEXT NOT NULL DEFAULT 'admin',
    name TEXT NOT NULL,
    broker TEXT DEFAULT '',
    account_no_mask TEXT DEFAULT '',
    is_default INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    notes TEXT DEFAULT '',
    display_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(login_user_id) REFERENCES login_users(id)
);
CREATE INDEX IF NOT EXISTS idx_securities_accounts_owner
    ON securities_accounts(login_user_id, status, display_order, created_at);

CREATE TABLE IF NOT EXISTS watchlist (
    code TEXT NOT NULL,
    name TEXT,
    group_name TEXT DEFAULT "默认",
    sort_order INTEGER DEFAULT 0,
    strategy_state TEXT DEFAULT "watch",
    target_buy_price REAL,
    target_sell_price REAL,
    stop_loss_price REAL,
    notes TEXT,
    login_user_id TEXT DEFAULT 'admin',
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    strategy_state_updated_at TIMESTAMP,
    UNIQUE(login_user_id, code)
);

CREATE TABLE IF NOT EXISTS portfolio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    total_shares REAL DEFAULT 0,
    available_shares REAL DEFAULT 0,
    avg_cost REAL DEFAULT 0,
    current_price REAL DEFAULT 0,
    market_value REAL DEFAULT 0,
    unrealized_pnl REAL DEFAULT 0,
    unrealized_pnl_pct REAL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    account_id TEXT DEFAULT 'default',
    UNIQUE(account_id, code)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    direction TEXT NOT NULL,
    price REAL NOT NULL,
    shares REAL NOT NULL,
    amount REAL NOT NULL,
    commission REAL DEFAULT 0,
    stamp_tax REAL DEFAULT 0,
    transfer_fee REAL DEFAULT 0,
    total_cost REAL DEFAULT 0,
    trade_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    account_id TEXT DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS trade_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_key TEXT UNIQUE NOT NULL,
    account_id TEXT DEFAULT 'default',
    code TEXT NOT NULL,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    outcome TEXT NOT NULL DEFAULT 'neutral',
    trade_ids_json TEXT NOT NULL DEFAULT '[]',
    opened_at TEXT,
    closed_at TEXT,
    holding_days REAL DEFAULT 0,
    buy_amount REAL DEFAULT 0,
    sell_amount REAL DEFAULT 0,
    fees REAL DEFAULT 0,
    realized_pnl REAL DEFAULT 0,
    realized_pnl_pct REAL DEFAULT 0,
    summary TEXT DEFAULT '',
    facts_json TEXT DEFAULT '{}',
    lesson_tags_json TEXT DEFAULT '[]',
    rules_json TEXT DEFAULT '[]',
    veto_lessons_json TEXT DEFAULT '[]',
    report_context_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS trade_memory_embeddings (
    memory_id INTEGER PRIMARY KEY,
    memory_key TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    embedding_text TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(memory_id) REFERENCES trade_memories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS self_evolution_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT UNIQUE NOT NULL,
    version TEXT NOT NULL DEFAULT 'self-evolution-v3',
    status TEXT NOT NULL DEFAULT 'active',
    system_score REAL DEFAULT 0,
    source_counts_json TEXT DEFAULT '{}',
    layers_json TEXT DEFAULT '{}',
    rules_json TEXT DEFAULT '[]',
    constraints_json TEXT DEFAULT '{}',
    context TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recommendation_attributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    outcome TEXT NOT NULL DEFAULT 'neutral',
    realized_pnl REAL DEFAULT 0,
    realized_pnl_pct REAL DEFAULT 0,
    tracking_pnl_pct REAL,
    source_report_ids_json TEXT DEFAULT '[]',
    trade_ids_json TEXT DEFAULT '[]',
    memory_ids_json TEXT DEFAULT '[]',
    evidence_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conditional_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    condition_type TEXT NOT NULL,
    target_price REAL NOT NULL,
    action TEXT NOT NULL,
    shares REAL,
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
    model_mode TEXT DEFAULT 'balanced',
    login_user_id TEXT DEFAULT 'admin'
);

CREATE TABLE IF NOT EXISTS stock_data_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    snapshot_json TEXT NOT NULL,
    validation_json TEXT DEFAULT '{}',
    summary_json TEXT DEFAULT '{}',
    source TEXT DEFAULT 'batch_research',
    run_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batch_report_jobs (
    job_id TEXT PRIMARY KEY,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    total_count INTEGER DEFAULT 0,
    submitted_count INTEGER DEFAULT 0,
    completed_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    payload_json TEXT DEFAULT '{}',
    result_json TEXT DEFAULT '{}',
    error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batch_report_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    task_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batch_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    total_count INTEGER DEFAULT 0,
    completed_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    waiting_count INTEGER DEFAULT 0,
    running_count INTEGER DEFAULT 0,
    current_code TEXT,
    payload_json TEXT DEFAULT '{}',
    result_json TEXT DEFAULT '{}',
    error TEXT,
    worker_id TEXT,
    heartbeat_at TEXT,
    lease_owner TEXT,
    lease_token TEXT,
    lease_until TEXT,
    pause_requested INTEGER DEFAULT 0,
    paused_at TEXT,
    input_snapshot_json TEXT DEFAULT '{}',
    quality_json TEXT DEFAULT '{}',
    post_actions_json TEXT DEFAULT '{}',
    runtime_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batch_job_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    snapshot_id INTEGER,
    locked_snapshot_id INTEGER,
    report_id INTEGER,
    task_id TEXT,
    error TEXT,
    error_type TEXT,
    next_retry_at TEXT,
    lease_owner TEXT,
    lease_token TEXT,
    lease_until TEXT,
    quality_json TEXT DEFAULT '{}',
    retry_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batch_job_item_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    role_key TEXT NOT NULL,
    role_name TEXT,
    step_order INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    content TEXT DEFAULT '',
    error TEXT,
    error_type TEXT,
    next_retry_at TEXT,
    input_hash TEXT,
    model_config_json TEXT DEFAULT '{}',
    duration_ms INTEGER,
    token_usage_json TEXT DEFAULT '{}',
    retry_count INTEGER DEFAULT 0,
    heartbeat_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(item_id, role_key)
);

CREATE TABLE IF NOT EXISTS batch_job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    item_id INTEGER,
    step_id INTEGER,
    level TEXT NOT NULL DEFAULT 'info',
    event TEXT NOT NULL,
    message TEXT,
    data_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batch_job_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    title TEXT,
    path TEXT,
    data_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS batch_worker_heartbeats (
    worker_id TEXT PRIMARY KEY,
    pid INTEGER,
    state TEXT NOT NULL DEFAULT 'idle',
    model_provider_ids_json TEXT DEFAULT '[]',
    model_tier TEXT DEFAULT '',
    last_seen_at TEXT DEFAULT (datetime('now')),
    last_loop_at TEXT,
    last_claim_at TEXT,
    current_job_id TEXT,
    current_job_type TEXT,
    current_item_id INTEGER,
    current_code TEXT,
    current_stage TEXT,
    last_result_json TEXT DEFAULT '{}',
    error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS position_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT UNIQUE NOT NULL,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    adoption_status TEXT NOT NULL DEFAULT 'draft',
    stage TEXT NOT NULL DEFAULT 'final',
    parent_plan_id TEXT,
    context_strategy TEXT NOT NULL DEFAULT 'auto',
    source_report_ids TEXT DEFAULT '[]',
    candidate_count INTEGER DEFAULT 0,
    selected_count INTEGER DEFAULT 0,
    model_strategy TEXT DEFAULT 'single',
    model_config_json TEXT DEFAULT '{}',
    role_models_json TEXT DEFAULT '{}',
    cash_snapshot_json TEXT DEFAULT '{}',
    portfolio_snapshot_json TEXT DEFAULT '{}',
    decision_market_snapshot_json TEXT DEFAULT '{}',
    market_context_captured_at TEXT,
    summary TEXT,
    risk_controls_json TEXT DEFAULT '[]',
    role_discussion_json TEXT DEFAULT '[]',
    recommendations_json TEXT DEFAULT '[]',
    review_result_json TEXT DEFAULT '{}',
    output_markdown TEXT,
    output_json TEXT DEFAULT '{}',
    batch_job_id TEXT,
    confirmed_at TEXT,
    confirmed_by TEXT,
    confirmed_snapshot_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS position_plan_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    action TEXT NOT NULL DEFAULT 'watch',
    suggested_amount REAL DEFAULT 0,
    position_pct REAL DEFAULT 0,
    suggested_shares REAL DEFAULT 0,
    confidence REAL,
    risk_score REAL,
    reason TEXT,
            entry_plan TEXT,
            stop_loss TEXT,
            risk_note TEXT,
            source_report_id INTEGER,
            adoption_status TEXT NOT NULL DEFAULT 'pending',
            adopted_at TEXT,
            adopted_by TEXT,
            adoption_note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

CREATE TABLE IF NOT EXISTS holding_daily_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT UNIQUE NOT NULL,
    date TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'pending',
    holding_count INTEGER DEFAULT 0,
    candidate_count INTEGER DEFAULT 0,
    trigger_count INTEGER DEFAULT 0,
    critical_count INTEGER DEFAULT 0,
    candidate_scope TEXT DEFAULT 'none',
    rerun_report_codes_json TEXT DEFAULT '[]',
    source_report_ids_json TEXT DEFAULT '[]',
    source_snapshot_ids_json TEXT DEFAULT '[]',
    asset_snapshot_json TEXT DEFAULT '{}',
    portfolio_snapshot_json TEXT DEFAULT '{}',
    candidate_context_json TEXT DEFAULT '{}',
    market_snapshot_json TEXT DEFAULT '{}',
    summary TEXT,
    tomorrow_plan_markdown TEXT,
    tomorrow_plan_json TEXT DEFAULT '{}',
    model_config_json TEXT DEFAULT '{}',
    batch_job_id TEXT,
    error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS holding_review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL,
    date TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT 'default',
    item_type TEXT NOT NULL DEFAULT 'holding',
    code TEXT NOT NULL,
    name TEXT,
    source_group TEXT,
    shares REAL DEFAULT 0,
    avg_cost REAL DEFAULT 0,
    price REAL DEFAULT 0,
    change_pct REAL DEFAULT 0,
    market_value REAL DEFAULT 0,
    position_pct REAL DEFAULT 0,
    holding_pnl REAL DEFAULT 0,
    holding_pnl_pct REAL DEFAULT 0,
    latest_signal TEXT,
    latest_risk_score REAL,
    latest_report_id INTEGER,
    latest_report_created_at TEXT,
    needs_report INTEGER DEFAULT 0,
    action_hint TEXT,
    decision_action TEXT NOT NULL DEFAULT 'watch',
    decision_status TEXT NOT NULL DEFAULT 'not_executed',
    decision_reason TEXT,
    target_position_pct REAL DEFAULT 0,
    suggested_amount REAL DEFAULT 0,
    decision_meta_json TEXT DEFAULT '{}',
    decision_updated_at TEXT,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS holding_trigger_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL,
    date TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT 'default',
    code TEXT NOT NULL,
    name TEXT,
    flag_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    source TEXT NOT NULL DEFAULT 'holding_review',
    description TEXT,
    evidence_json TEXT DEFAULT '{}',
    requires_full_report INTEGER DEFAULT 0,
    source_report_id INTEGER,
    source_snapshot_id INTEGER,
    resolved INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
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
    account_id TEXT DEFAULT 'default',
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
    shares REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (account_id, date, code6)
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

CREATE TABLE IF NOT EXISTS model_providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT DEFAULT '',
    models_json TEXT DEFAULT '[]',
    quick_model TEXT DEFAULT '',
    deep_model TEXT DEFAULT '',
    default_model TEXT DEFAULT '',
    context_length TEXT DEFAULT '',
    embedding_model TEXT DEFAULT '',
    embedding_dimensions INTEGER DEFAULT 1536,
    usage_json TEXT DEFAULT '[]',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_model_providers_updated
    ON model_providers(updated_at DESC);

CREATE TABLE IF NOT EXISTS report_selection_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    selection_id TEXT UNIQUE NOT NULL,
    source_page TEXT NOT NULL DEFAULT 'unknown',
    source_label TEXT DEFAULT '',
    codes_json TEXT NOT NULL DEFAULT '[]',
    filters_json TEXT NOT NULL DEFAULT '{}',
    login_user_id TEXT DEFAULT 'admin',
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_report_selection_sets_expires
    ON report_selection_sets(expires_at);

CREATE TABLE IF NOT EXISTS pending_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    target_buy_price REAL,
    plan_shares REAL DEFAULT 100,
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
    plan_shares REAL DEFAULT 100,
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
    shares REAL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS ai_shadow_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER UNIQUE,
    code TEXT NOT NULL,
    name TEXT,
    action TEXT NOT NULL,
    signal TEXT,
    suggested_price REAL,
    fill_price REAL,
    target_price REAL,
    stop_loss_price REAL,
    shares REAL DEFAULT 0,
    confidence REAL,
    risk_score REAL,
    status TEXT DEFAULT 'pending',
    source_reason TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    filled_at TEXT,
    closed_at TEXT,
    close_price REAL,
    pnl REAL,
    pnl_pct REAL
);

CREATE TABLE IF NOT EXISTS ai_shadow_positions (
    code TEXT PRIMARY KEY,
    name TEXT,
    total_shares REAL DEFAULT 0,
    avg_cost REAL DEFAULT 0,
    current_price REAL DEFAULT 0,
    market_value REAL DEFAULT 0,
    unrealized_pnl REAL DEFAULT 0,
    unrealized_pnl_pct REAL DEFAULT 0,
    realized_pnl REAL DEFAULT 0,
    source_order_ids TEXT DEFAULT '[]',
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
CREATE INDEX IF NOT EXISTS idx_ai_shadow_orders_report ON ai_shadow_orders(report_id);
CREATE INDEX IF NOT EXISTS idx_ai_shadow_orders_code_status ON ai_shadow_orders(code, status);
CREATE INDEX IF NOT EXISTS idx_ai_shadow_orders_created ON ai_shadow_orders(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_code_time ON trades(code, trade_time);
CREATE INDEX IF NOT EXISTS idx_trade_memories_status_code ON trade_memories(status, code, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_trade_memories_account_status ON trade_memories(account_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_trade_memory_embeddings_model ON trade_memory_embeddings(model, dimensions, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_self_evolution_snapshots_status ON self_evolution_snapshots(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recommendation_attributions_snapshot ON recommendation_attributions(snapshot_id, code);
CREATE INDEX IF NOT EXISTS idx_conditional_orders_status ON conditional_orders(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_conditional_orders_code_status ON conditional_orders(code, status);
CREATE INDEX IF NOT EXISTS idx_analysis_reports_code_created ON analysis_reports(code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stock_data_snapshots_code_created ON stock_data_snapshots(code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stock_data_snapshots_run ON stock_data_snapshots(run_id, code);
CREATE INDEX IF NOT EXISTS idx_batch_report_jobs_status_created ON batch_report_jobs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_batch_report_items_job_status ON batch_report_items(job_id, status);
CREATE INDEX IF NOT EXISTS idx_batch_job_item_steps_item_order ON batch_job_item_steps(item_id, step_order);
CREATE INDEX IF NOT EXISTS idx_batch_job_item_steps_job_status ON batch_job_item_steps(job_id, status);
CREATE INDEX IF NOT EXISTS idx_batch_job_logs_job_created ON batch_job_logs(job_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_batch_job_artifacts_job_created ON batch_job_artifacts(job_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_batch_worker_heartbeats_seen ON batch_worker_heartbeats(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_position_plans_status_created ON position_plans(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_position_plans_stage_created ON position_plans(stage, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_position_plan_items_plan ON position_plan_items(plan_id);
CREATE INDEX IF NOT EXISTS idx_position_plan_items_code ON position_plan_items(code);
CREATE INDEX IF NOT EXISTS idx_holding_daily_reviews_date ON holding_daily_reviews(date DESC, account_id);
CREATE INDEX IF NOT EXISTS idx_holding_daily_reviews_status ON holding_daily_reviews(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_holding_review_items_review ON holding_review_items(review_id, item_type);
CREATE INDEX IF NOT EXISTS idx_holding_review_items_code ON holding_review_items(code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_holding_trigger_flags_review ON holding_trigger_flags(review_id, severity);
CREATE INDEX IF NOT EXISTS idx_holding_trigger_flags_code ON holding_trigger_flags(code, created_at DESC);
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
    (
        6,
        "ai_shadow_portfolio",
        """
        CREATE TABLE IF NOT EXISTS ai_shadow_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER UNIQUE,
            code TEXT NOT NULL,
            name TEXT,
            action TEXT NOT NULL,
            signal TEXT,
            suggested_price REAL,
            fill_price REAL,
            target_price REAL,
            stop_loss_price REAL,
            shares REAL DEFAULT 0,
            confidence REAL,
            risk_score REAL,
            status TEXT DEFAULT 'pending',
            source_reason TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            filled_at TEXT,
            closed_at TEXT,
            close_price REAL,
            pnl REAL,
            pnl_pct REAL
        );
        CREATE TABLE IF NOT EXISTS ai_shadow_positions (
            code TEXT PRIMARY KEY,
            name TEXT,
            total_shares REAL DEFAULT 0,
            avg_cost REAL DEFAULT 0,
            current_price REAL DEFAULT 0,
            market_value REAL DEFAULT 0,
            unrealized_pnl REAL DEFAULT 0,
            unrealized_pnl_pct REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            source_order_ids TEXT DEFAULT '[]',
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ai_shadow_orders_report
            ON ai_shadow_orders(report_id);
        CREATE INDEX IF NOT EXISTS idx_ai_shadow_orders_code_status
            ON ai_shadow_orders(code, status);
        CREATE INDEX IF NOT EXISTS idx_ai_shadow_orders_created
            ON ai_shadow_orders(created_at DESC);
        """,
    ),
    (
        7,
        "stock_data_snapshots",
        """
        CREATE TABLE IF NOT EXISTS stock_data_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            snapshot_json TEXT NOT NULL,
            validation_json TEXT DEFAULT '{}',
            summary_json TEXT DEFAULT '{}',
            source TEXT DEFAULT 'batch_research',
            run_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_stock_data_snapshots_code_created
            ON stock_data_snapshots(code, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_stock_data_snapshots_run
            ON stock_data_snapshots(run_id, code);
        """,
    ),
    (
        8,
        "batch_report_jobs",
        """
        CREATE TABLE IF NOT EXISTS batch_report_jobs (
            job_id TEXT PRIMARY KEY,
            name TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            total_count INTEGER DEFAULT 0,
            submitted_count INTEGER DEFAULT 0,
            completed_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            payload_json TEXT DEFAULT '{}',
            result_json TEXT DEFAULT '{}',
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS batch_report_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            task_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_batch_report_jobs_status_created
            ON batch_report_jobs(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_batch_report_items_job_status
            ON batch_report_items(job_id, status);
        """,
    ),
    (
        9,
        "batch_research_jobs",
        """
        CREATE TABLE IF NOT EXISTS batch_jobs (
            job_id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            name TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            total_count INTEGER DEFAULT 0,
            completed_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            skipped_count INTEGER DEFAULT 0,
            waiting_count INTEGER DEFAULT 0,
            running_count INTEGER DEFAULT 0,
            current_code TEXT,
            payload_json TEXT DEFAULT '{}',
            result_json TEXT DEFAULT '{}',
            error TEXT,
            worker_id TEXT,
            heartbeat_at TEXT,
            lease_owner TEXT,
            lease_token TEXT,
            lease_until TEXT,
            pause_requested INTEGER DEFAULT 0,
            paused_at TEXT,
            input_snapshot_json TEXT DEFAULT '{}',
            quality_json TEXT DEFAULT '{}',
            post_actions_json TEXT DEFAULT '{}',
            runtime_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS batch_job_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            snapshot_id INTEGER,
            locked_snapshot_id INTEGER,
            report_id INTEGER,
            task_id TEXT,
            error TEXT,
            error_type TEXT,
            next_retry_at TEXT,
            lease_owner TEXT,
            lease_token TEXT,
            lease_until TEXT,
            quality_json TEXT DEFAULT '{}',
            retry_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS batch_job_item_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            job_id TEXT NOT NULL,
            role_key TEXT NOT NULL,
            role_name TEXT,
            step_order INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            content TEXT DEFAULT '',
            error TEXT,
            error_type TEXT,
            next_retry_at TEXT,
            input_hash TEXT,
            model_config_json TEXT DEFAULT '{}',
            duration_ms INTEGER,
            token_usage_json TEXT DEFAULT '{}',
            retry_count INTEGER DEFAULT 0,
            heartbeat_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(item_id, role_key)
        );
        CREATE TABLE IF NOT EXISTS batch_job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            item_id INTEGER,
            step_id INTEGER,
            level TEXT NOT NULL DEFAULT 'info',
            event TEXT NOT NULL,
            message TEXT,
            data_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS batch_job_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            title TEXT,
            path TEXT,
            data_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_batch_jobs_type_status_created
            ON batch_jobs(job_type, status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_batch_job_items_job_status
            ON batch_job_items(job_id, status);
        CREATE INDEX IF NOT EXISTS idx_batch_job_items_code
            ON batch_job_items(code);
        CREATE INDEX IF NOT EXISTS idx_batch_job_item_steps_item_order
            ON batch_job_item_steps(item_id, step_order);
        CREATE INDEX IF NOT EXISTS idx_batch_job_item_steps_job_status
            ON batch_job_item_steps(job_id, status);
        CREATE INDEX IF NOT EXISTS idx_batch_job_logs_job_created
            ON batch_job_logs(job_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_batch_job_artifacts_job_created
            ON batch_job_artifacts(job_id, created_at DESC);
        """,
    ),
    (
        10,
        "position_plans",
        """
        CREATE TABLE IF NOT EXISTS position_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT UNIQUE NOT NULL,
            title TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            adoption_status TEXT NOT NULL DEFAULT 'draft',
            stage TEXT NOT NULL DEFAULT 'final',
            parent_plan_id TEXT,
            context_strategy TEXT NOT NULL DEFAULT 'auto',
            source_report_ids TEXT DEFAULT '[]',
            candidate_count INTEGER DEFAULT 0,
            selected_count INTEGER DEFAULT 0,
            model_strategy TEXT DEFAULT 'single',
            model_config_json TEXT DEFAULT '{}',
            role_models_json TEXT DEFAULT '{}',
            cash_snapshot_json TEXT DEFAULT '{}',
            portfolio_snapshot_json TEXT DEFAULT '{}',
            decision_market_snapshot_json TEXT DEFAULT '{}',
            market_context_captured_at TEXT,
            summary TEXT,
            risk_controls_json TEXT DEFAULT '[]',
            role_discussion_json TEXT DEFAULT '[]',
            recommendations_json TEXT DEFAULT '[]',
            review_result_json TEXT DEFAULT '{}',
            output_markdown TEXT,
            output_json TEXT DEFAULT '{}',
            batch_job_id TEXT,
            confirmed_at TEXT,
            confirmed_by TEXT,
            confirmed_snapshot_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS position_plan_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            action TEXT NOT NULL DEFAULT 'watch',
            suggested_amount REAL DEFAULT 0,
            position_pct REAL DEFAULT 0,
            suggested_shares REAL DEFAULT 0,
            confidence REAL,
            risk_score REAL,
            reason TEXT,
    entry_plan TEXT,
    stop_loss TEXT,
    risk_note TEXT,
    source_report_id INTEGER,
    adoption_status TEXT NOT NULL DEFAULT 'pending',
    adopted_at TEXT,
    adopted_by TEXT,
    adoption_note TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
        CREATE INDEX IF NOT EXISTS idx_position_plans_status_created
            ON position_plans(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_position_plans_adoption_created
            ON position_plans(adoption_status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_position_plans_stage_created
            ON position_plans(stage, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_position_plan_items_plan
            ON position_plan_items(plan_id);
        CREATE INDEX IF NOT EXISTS idx_position_plan_items_code
            ON position_plan_items(code);
        """,
    ),
    (
        11,
        "batch_research_runtime_ops",
        """
        CREATE TABLE IF NOT EXISTS batch_job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            item_id INTEGER,
            step_id INTEGER,
            level TEXT NOT NULL DEFAULT 'info',
            event TEXT NOT NULL,
            message TEXT,
            data_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS batch_job_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            title TEXT,
            path TEXT,
            data_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_batch_job_logs_job_created
            ON batch_job_logs(job_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_batch_job_artifacts_job_created
            ON batch_job_artifacts(job_id, created_at DESC);
        """,
    ),
    (
        12,
        "holding_daily_reviews",
        """
        CREATE TABLE IF NOT EXISTS holding_daily_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id TEXT UNIQUE NOT NULL,
            date TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'default',
            status TEXT NOT NULL DEFAULT 'pending',
            holding_count INTEGER DEFAULT 0,
            candidate_count INTEGER DEFAULT 0,
            trigger_count INTEGER DEFAULT 0,
            critical_count INTEGER DEFAULT 0,
            candidate_scope TEXT DEFAULT 'none',
            rerun_report_codes_json TEXT DEFAULT '[]',
            source_report_ids_json TEXT DEFAULT '[]',
            source_snapshot_ids_json TEXT DEFAULT '[]',
            asset_snapshot_json TEXT DEFAULT '{}',
            portfolio_snapshot_json TEXT DEFAULT '{}',
            candidate_context_json TEXT DEFAULT '{}',
            market_snapshot_json TEXT DEFAULT '{}',
            summary TEXT,
            tomorrow_plan_markdown TEXT,
            tomorrow_plan_json TEXT DEFAULT '{}',
            model_config_json TEXT DEFAULT '{}',
            batch_job_id TEXT,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS holding_review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id TEXT NOT NULL,
            date TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'default',
            item_type TEXT NOT NULL DEFAULT 'holding',
            code TEXT NOT NULL,
            name TEXT,
            source_group TEXT,
            shares REAL DEFAULT 0,
            avg_cost REAL DEFAULT 0,
            price REAL DEFAULT 0,
            change_pct REAL DEFAULT 0,
            market_value REAL DEFAULT 0,
            position_pct REAL DEFAULT 0,
            holding_pnl REAL DEFAULT 0,
            holding_pnl_pct REAL DEFAULT 0,
            latest_signal TEXT,
            latest_risk_score REAL,
            latest_report_id INTEGER,
            latest_report_created_at TEXT,
            needs_report INTEGER DEFAULT 0,
            action_hint TEXT,
            decision_action TEXT NOT NULL DEFAULT 'watch',
            decision_status TEXT NOT NULL DEFAULT 'not_executed',
            decision_reason TEXT,
            target_position_pct REAL DEFAULT 0,
            suggested_amount REAL DEFAULT 0,
            decision_meta_json TEXT DEFAULT '{}',
            decision_updated_at TEXT,
            reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS holding_trigger_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id TEXT NOT NULL,
            date TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'default',
            code TEXT NOT NULL,
            name TEXT,
            flag_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            source TEXT NOT NULL DEFAULT 'holding_review',
            description TEXT,
            evidence_json TEXT DEFAULT '{}',
            requires_full_report INTEGER DEFAULT 0,
            source_report_id INTEGER,
            source_snapshot_id INTEGER,
            resolved INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_holding_daily_reviews_date
            ON holding_daily_reviews(date DESC, account_id);
        CREATE INDEX IF NOT EXISTS idx_holding_daily_reviews_status
            ON holding_daily_reviews(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_holding_review_items_review
            ON holding_review_items(review_id, item_type);
        CREATE INDEX IF NOT EXISTS idx_holding_review_items_code
            ON holding_review_items(code, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_holding_trigger_flags_review
            ON holding_trigger_flags(review_id, severity);
        CREATE INDEX IF NOT EXISTS idx_holding_trigger_flags_code
            ON holding_trigger_flags(code, created_at DESC);
        """,
    ),
    (
        13,
        "model_provider_library",
        """
        CREATE TABLE IF NOT EXISTS model_providers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            api_key TEXT DEFAULT '',
            models_json TEXT DEFAULT '[]',
            quick_model TEXT DEFAULT '',
            deep_model TEXT DEFAULT '',
            default_model TEXT DEFAULT '',
            context_length TEXT DEFAULT '',
            embedding_model TEXT DEFAULT '',
            embedding_dimensions INTEGER DEFAULT 1536,
            usage_json TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_model_providers_updated
            ON model_providers(updated_at DESC);
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


async def ensure_position_plan_adoption_columns(db):
    """Backfill position plan adoption columns for databases that already ran v10."""
    rows = await db.execute_fetchall("PRAGMA table_info(position_plans)")
    columns = {row[1] for row in rows}
    additions = {
        "adoption_status": "ALTER TABLE position_plans ADD COLUMN adoption_status TEXT NOT NULL DEFAULT 'draft'",
        "confirmed_at": "ALTER TABLE position_plans ADD COLUMN confirmed_at TEXT",
        "confirmed_by": "ALTER TABLE position_plans ADD COLUMN confirmed_by TEXT",
        "confirmed_snapshot_json": "ALTER TABLE position_plans ADD COLUMN confirmed_snapshot_json TEXT DEFAULT '{}'",
    }
    for column, sql in additions.items():
        if column not in columns:
            await db.execute(sql)
    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_position_plans_adoption_created
            ON position_plans(adoption_status, created_at DESC)
        """
    )


async def ensure_position_plan_market_context_columns(db):
    """Backfill decision-time market context columns for existing position plan tables."""
    rows = await db.execute_fetchall("PRAGMA table_info(position_plans)")
    columns = {row[1] for row in rows}
    additions = {
        "decision_market_snapshot_json": "ALTER TABLE position_plans ADD COLUMN decision_market_snapshot_json TEXT DEFAULT '{}'",
        "market_context_captured_at": "ALTER TABLE position_plans ADD COLUMN market_context_captured_at TEXT",
    }
    for column, sql in additions.items():
        if column not in columns:
            await db.execute(sql)


async def ensure_position_plan_item_adoption_columns(db):
    """Backfill per-item adoption columns for existing position plan items."""
    rows = await db.execute_fetchall("PRAGMA table_info(position_plan_items)")
    columns = {row[1] for row in rows}
    additions = {
        "adoption_status": "ALTER TABLE position_plan_items ADD COLUMN adoption_status TEXT NOT NULL DEFAULT 'pending'",
        "adopted_at": "ALTER TABLE position_plan_items ADD COLUMN adopted_at TEXT",
        "adopted_by": "ALTER TABLE position_plan_items ADD COLUMN adopted_by TEXT",
        "adoption_note": "ALTER TABLE position_plan_items ADD COLUMN adoption_note TEXT",
    }
    for column, sql in additions.items():
        if column not in columns:
            await db.execute(sql)


async def ensure_batch_job_item_steps_table(db):
    """Backfill role-level batch item checkpoints for databases that already ran v9."""
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS batch_job_item_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            job_id TEXT NOT NULL,
            role_key TEXT NOT NULL,
            role_name TEXT,
            step_order INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            content TEXT DEFAULT '',
            error TEXT,
            retry_count INTEGER DEFAULT 0,
            heartbeat_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(item_id, role_key)
        );
        CREATE INDEX IF NOT EXISTS idx_batch_job_item_steps_item_order
            ON batch_job_item_steps(item_id, step_order);
        CREATE INDEX IF NOT EXISTS idx_batch_job_item_steps_job_status
            ON batch_job_item_steps(job_id, status);
        """
    )


async def ensure_batch_runtime_ops(db):
    """Backfill long-running batch job runtime columns and operational tables."""
    async def add_missing_columns(table: str, additions: dict[str, str]) -> None:
        rows = await db.execute_fetchall(f"PRAGMA table_info({table})")
        columns = {row[1] for row in rows}
        for column, sql in additions.items():
            if column not in columns:
                await db.execute(sql)

    await add_missing_columns(
        "batch_jobs",
        {
            "worker_id": "ALTER TABLE batch_jobs ADD COLUMN worker_id TEXT",
            "heartbeat_at": "ALTER TABLE batch_jobs ADD COLUMN heartbeat_at TEXT",
            "lease_owner": "ALTER TABLE batch_jobs ADD COLUMN lease_owner TEXT",
            "lease_token": "ALTER TABLE batch_jobs ADD COLUMN lease_token TEXT",
            "lease_until": "ALTER TABLE batch_jobs ADD COLUMN lease_until TEXT",
            "pause_requested": "ALTER TABLE batch_jobs ADD COLUMN pause_requested INTEGER DEFAULT 0",
            "paused_at": "ALTER TABLE batch_jobs ADD COLUMN paused_at TEXT",
            "input_snapshot_json": "ALTER TABLE batch_jobs ADD COLUMN input_snapshot_json TEXT DEFAULT '{}'",
            "quality_json": "ALTER TABLE batch_jobs ADD COLUMN quality_json TEXT DEFAULT '{}'",
            "post_actions_json": "ALTER TABLE batch_jobs ADD COLUMN post_actions_json TEXT DEFAULT '{}'",
            "runtime_json": "ALTER TABLE batch_jobs ADD COLUMN runtime_json TEXT DEFAULT '{}'",
        },
    )
    await add_missing_columns(
        "batch_job_items",
        {
            "locked_snapshot_id": "ALTER TABLE batch_job_items ADD COLUMN locked_snapshot_id INTEGER",
            "error_type": "ALTER TABLE batch_job_items ADD COLUMN error_type TEXT",
            "next_retry_at": "ALTER TABLE batch_job_items ADD COLUMN next_retry_at TEXT",
            "lease_owner": "ALTER TABLE batch_job_items ADD COLUMN lease_owner TEXT",
            "lease_token": "ALTER TABLE batch_job_items ADD COLUMN lease_token TEXT",
            "lease_until": "ALTER TABLE batch_job_items ADD COLUMN lease_until TEXT",
            "quality_json": "ALTER TABLE batch_job_items ADD COLUMN quality_json TEXT DEFAULT '{}'",
        },
    )
    await add_missing_columns(
        "batch_job_item_steps",
        {
            "heartbeat_at": "ALTER TABLE batch_job_item_steps ADD COLUMN heartbeat_at TEXT",
            "error_type": "ALTER TABLE batch_job_item_steps ADD COLUMN error_type TEXT",
            "next_retry_at": "ALTER TABLE batch_job_item_steps ADD COLUMN next_retry_at TEXT",
            "input_hash": "ALTER TABLE batch_job_item_steps ADD COLUMN input_hash TEXT",
            "model_config_json": "ALTER TABLE batch_job_item_steps ADD COLUMN model_config_json TEXT DEFAULT '{}'",
            "duration_ms": "ALTER TABLE batch_job_item_steps ADD COLUMN duration_ms INTEGER",
            "token_usage_json": "ALTER TABLE batch_job_item_steps ADD COLUMN token_usage_json TEXT DEFAULT '{}'",
        },
    )
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS batch_job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            item_id INTEGER,
            step_id INTEGER,
            level TEXT NOT NULL DEFAULT 'info',
            event TEXT NOT NULL,
            message TEXT,
            data_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS batch_job_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            title TEXT,
            path TEXT,
            data_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_batch_job_logs_job_created
            ON batch_job_logs(job_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_batch_job_artifacts_job_created
            ON batch_job_artifacts(job_id, created_at DESC);
        """
    )


async def ensure_batch_worker_heartbeats_table(db):
    """Backfill worker process heartbeat table for operational diagnostics."""
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS batch_worker_heartbeats (
            worker_id TEXT PRIMARY KEY,
            pid INTEGER,
            state TEXT NOT NULL DEFAULT 'idle',
            model_provider_ids_json TEXT DEFAULT '[]',
            model_tier TEXT DEFAULT '',
            last_seen_at TEXT DEFAULT (datetime('now')),
            last_loop_at TEXT,
            last_claim_at TEXT,
            current_job_id TEXT,
            current_job_type TEXT,
            current_item_id INTEGER,
            current_code TEXT,
            current_stage TEXT,
            last_result_json TEXT DEFAULT '{}',
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_batch_worker_heartbeats_seen
            ON batch_worker_heartbeats(last_seen_at DESC);
        """
    )


async def ensure_holding_daily_review_tables(db):
    """Backfill holding daily review tables for existing databases."""
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS holding_daily_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id TEXT UNIQUE NOT NULL,
            date TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'default',
            status TEXT NOT NULL DEFAULT 'pending',
            holding_count INTEGER DEFAULT 0,
            candidate_count INTEGER DEFAULT 0,
            trigger_count INTEGER DEFAULT 0,
            critical_count INTEGER DEFAULT 0,
            candidate_scope TEXT DEFAULT 'none',
            rerun_report_codes_json TEXT DEFAULT '[]',
            source_report_ids_json TEXT DEFAULT '[]',
            source_snapshot_ids_json TEXT DEFAULT '[]',
            asset_snapshot_json TEXT DEFAULT '{}',
            portfolio_snapshot_json TEXT DEFAULT '{}',
            candidate_context_json TEXT DEFAULT '{}',
            market_snapshot_json TEXT DEFAULT '{}',
            summary TEXT,
            tomorrow_plan_markdown TEXT,
            tomorrow_plan_json TEXT DEFAULT '{}',
            model_config_json TEXT DEFAULT '{}',
            batch_job_id TEXT,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS holding_review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id TEXT NOT NULL,
            date TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'default',
            item_type TEXT NOT NULL DEFAULT 'holding',
            code TEXT NOT NULL,
            name TEXT,
            source_group TEXT,
            shares REAL DEFAULT 0,
            avg_cost REAL DEFAULT 0,
            price REAL DEFAULT 0,
            change_pct REAL DEFAULT 0,
            market_value REAL DEFAULT 0,
            position_pct REAL DEFAULT 0,
            holding_pnl REAL DEFAULT 0,
            holding_pnl_pct REAL DEFAULT 0,
            latest_signal TEXT,
            latest_risk_score REAL,
            latest_report_id INTEGER,
            latest_report_created_at TEXT,
            needs_report INTEGER DEFAULT 0,
            action_hint TEXT,
            decision_action TEXT NOT NULL DEFAULT 'watch',
            decision_status TEXT NOT NULL DEFAULT 'not_executed',
            decision_reason TEXT,
            target_position_pct REAL DEFAULT 0,
            suggested_amount REAL DEFAULT 0,
            decision_meta_json TEXT DEFAULT '{}',
            decision_updated_at TEXT,
            reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS holding_trigger_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id TEXT NOT NULL,
            date TEXT NOT NULL,
            account_id TEXT NOT NULL DEFAULT 'default',
            code TEXT NOT NULL,
            name TEXT,
            flag_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            source TEXT NOT NULL DEFAULT 'holding_review',
            description TEXT,
            evidence_json TEXT DEFAULT '{}',
            requires_full_report INTEGER DEFAULT 0,
            source_report_id INTEGER,
            source_snapshot_id INTEGER,
            resolved INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_holding_daily_reviews_date
            ON holding_daily_reviews(date DESC, account_id);
        CREATE INDEX IF NOT EXISTS idx_holding_daily_reviews_status
            ON holding_daily_reviews(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_holding_review_items_review
            ON holding_review_items(review_id, item_type);
        CREATE INDEX IF NOT EXISTS idx_holding_review_items_code
            ON holding_review_items(code, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_holding_trigger_flags_review
            ON holding_trigger_flags(review_id, severity);
        CREATE INDEX IF NOT EXISTS idx_holding_trigger_flags_code
            ON holding_trigger_flags(code, created_at DESC);
        """
    )
    rows = await db.execute_fetchall("PRAGMA table_info(holding_review_items)")
    columns = {row[1] for row in rows}
    additions = {
        "decision_action": "ALTER TABLE holding_review_items ADD COLUMN decision_action TEXT NOT NULL DEFAULT 'watch'",
        "decision_status": "ALTER TABLE holding_review_items ADD COLUMN decision_status TEXT NOT NULL DEFAULT 'not_executed'",
        "decision_reason": "ALTER TABLE holding_review_items ADD COLUMN decision_reason TEXT",
        "target_position_pct": "ALTER TABLE holding_review_items ADD COLUMN target_position_pct REAL DEFAULT 0",
        "suggested_amount": "ALTER TABLE holding_review_items ADD COLUMN suggested_amount REAL DEFAULT 0",
        "decision_meta_json": "ALTER TABLE holding_review_items ADD COLUMN decision_meta_json TEXT DEFAULT '{}'",
        "decision_updated_at": "ALTER TABLE holding_review_items ADD COLUMN decision_updated_at TEXT",
    }
    for column, sql in additions.items():
        if column not in columns:
            await db.execute(sql)


async def ensure_portfolio_account_key(db):
    """Migrate portfolio from code-only primary key to account/code uniqueness."""
    rows = await db.execute_fetchall("PRAGMA table_info(portfolio)")
    if not rows:
        return
    pk_columns = [row[1] for row in rows if row[5]]
    indexes = await db.execute_fetchall("PRAGMA index_list(portfolio)")
    has_account_code_unique = False
    for index in indexes:
        if not index[2]:
            continue
        columns = await db.execute_fetchall(f"PRAGMA index_info({index[1]})")
        names = [column[2] for column in columns]
        if names == ["account_id", "code"]:
            has_account_code_unique = True
            break
    if has_account_code_unique and pk_columns != ["code"]:
        return

    await db.execute("DROP TABLE IF EXISTS portfolio_account_key_backup")
    await db.execute("ALTER TABLE portfolio RENAME TO portfolio_account_key_backup")
    await db.executescript(
        """
        CREATE TABLE portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            total_shares REAL DEFAULT 0,
            available_shares REAL DEFAULT 0,
            avg_cost REAL DEFAULT 0,
            current_price REAL DEFAULT 0,
            market_value REAL DEFAULT 0,
            unrealized_pnl REAL DEFAULT 0,
            unrealized_pnl_pct REAL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            account_id TEXT DEFAULT 'default',
            UNIQUE(account_id, code)
        );
        INSERT INTO portfolio (
            code, name, total_shares, available_shares, avg_cost, current_price,
            market_value, unrealized_pnl, unrealized_pnl_pct, updated_at, account_id
        )
        SELECT
            code,
            MAX(name),
            ROUND(SUM(COALESCE(total_shares, 0)), 3),
            ROUND(SUM(COALESCE(available_shares, total_shares, 0)), 3),
            CASE
                WHEN SUM(COALESCE(total_shares, 0)) > 0
                THEN ROUND(SUM(COALESCE(avg_cost, 0) * COALESCE(total_shares, 0)) / SUM(COALESCE(total_shares, 0)), 3)
                ELSE 0
            END,
            MAX(COALESCE(current_price, 0)),
            SUM(COALESCE(market_value, 0)),
            SUM(COALESCE(unrealized_pnl, 0)),
            CASE
                WHEN SUM(COALESCE(avg_cost, 0) * COALESCE(total_shares, 0)) > 0
                THEN ROUND(SUM(COALESCE(unrealized_pnl, 0)) / SUM(COALESCE(avg_cost, 0) * COALESCE(total_shares, 0)) * 100, 3)
                ELSE 0
            END,
            MAX(updated_at),
            COALESCE(account_id, 'default')
        FROM portfolio_account_key_backup
        WHERE code IS NOT NULL AND code != ''
        GROUP BY COALESCE(account_id, 'default'), code;
        DROP TABLE portfolio_account_key_backup;
        """
    )


def _hash_password(password: str) -> str:
    return "sha256$" + hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()


async def _table_columns(db, table_name: str) -> set[str]:
    rows = await db.execute_fetchall(f"PRAGMA table_info({table_name})")
    return {row[1] for row in rows}


async def _add_missing_columns(db, table_name: str, additions: dict[str, str]) -> None:
    columns = await _table_columns(db, table_name)
    for column, sql in additions.items():
        if column not in columns:
            await db.execute(sql)


async def ensure_identity_tables(db):
    """Create login users and securities accounts while preserving legacy accounts."""
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS login_users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT DEFAULT '',
            display_name TEXT,
            role TEXT DEFAULT 'owner',
            status TEXT DEFAULT 'active',
            default_securities_account_id TEXT DEFAULT 'default',
            must_change_credentials INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS login_sessions (
            session_id TEXT PRIMARY KEY,
            login_user_id TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT,
            last_seen_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(login_user_id) REFERENCES login_users(id)
        );
        CREATE TABLE IF NOT EXISTS securities_accounts (
            id TEXT PRIMARY KEY,
            login_user_id TEXT NOT NULL DEFAULT 'admin',
            name TEXT NOT NULL,
            broker TEXT DEFAULT '',
            account_no_mask TEXT DEFAULT '',
            is_default INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            notes TEXT DEFAULT '',
            display_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(login_user_id) REFERENCES login_users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_securities_accounts_owner
            ON securities_accounts(login_user_id, status, display_order, created_at);
        """
    )
    await _add_missing_columns(
        db,
        "accounts",
        {
            "broker": "ALTER TABLE accounts ADD COLUMN broker TEXT DEFAULT ''",
            "created_at": "ALTER TABLE accounts ADD COLUMN created_at TEXT",
        },
    )
    await _add_missing_columns(
        db,
        "login_users",
        {
            "password_hash": "ALTER TABLE login_users ADD COLUMN password_hash TEXT DEFAULT ''",
            "display_name": "ALTER TABLE login_users ADD COLUMN display_name TEXT",
            "role": "ALTER TABLE login_users ADD COLUMN role TEXT DEFAULT 'owner'",
            "status": "ALTER TABLE login_users ADD COLUMN status TEXT DEFAULT 'active'",
            "default_securities_account_id": "ALTER TABLE login_users ADD COLUMN default_securities_account_id TEXT DEFAULT 'default'",
            "must_change_credentials": "ALTER TABLE login_users ADD COLUMN must_change_credentials INTEGER DEFAULT 0",
            "created_at": "ALTER TABLE login_users ADD COLUMN created_at TEXT",
            "updated_at": "ALTER TABLE login_users ADD COLUMN updated_at TEXT",
        },
    )
    await _add_missing_columns(
        db,
        "securities_accounts",
        {
            "login_user_id": "ALTER TABLE securities_accounts ADD COLUMN login_user_id TEXT DEFAULT 'admin'",
            "name": "ALTER TABLE securities_accounts ADD COLUMN name TEXT DEFAULT ''",
            "broker": "ALTER TABLE securities_accounts ADD COLUMN broker TEXT DEFAULT ''",
            "account_no_mask": "ALTER TABLE securities_accounts ADD COLUMN account_no_mask TEXT DEFAULT ''",
            "is_default": "ALTER TABLE securities_accounts ADD COLUMN is_default INTEGER DEFAULT 0",
            "status": "ALTER TABLE securities_accounts ADD COLUMN status TEXT DEFAULT 'active'",
            "notes": "ALTER TABLE securities_accounts ADD COLUMN notes TEXT DEFAULT ''",
            "display_order": "ALTER TABLE securities_accounts ADD COLUMN display_order INTEGER DEFAULT 0",
            "created_at": "ALTER TABLE securities_accounts ADD COLUMN created_at TEXT",
            "updated_at": "ALTER TABLE securities_accounts ADD COLUMN updated_at TEXT",
        },
    )

    legacy_user = await (
        await db.execute("SELECT * FROM login_users WHERE id = ?", (LEGACY_LOGIN_USER_ID,))
    ).fetchone()
    admin_user = await (
        await db.execute("SELECT * FROM login_users WHERE id = ?", (DEFAULT_LOGIN_USER_ID,))
    ).fetchone()
    if legacy_user and not admin_user:
        await db.execute(
            """
            UPDATE login_users
            SET id = ?,
                username = ?,
                password_hash = ?,
                display_name = ?,
                role = COALESCE(NULLIF(role, ''), 'owner'),
                status = 'active',
                default_securities_account_id = 'default',
                must_change_credentials = 1,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                DEFAULT_LOGIN_USER_ID,
                DEFAULT_LOGIN_USERNAME,
                _hash_password(DEFAULT_LOGIN_PASSWORD),
                "管理员",
                LEGACY_LOGIN_USER_ID,
            ),
        )
    await db.execute(
        """
        INSERT OR IGNORE INTO login_users (
            id, username, password_hash, display_name, role, default_securities_account_id, must_change_credentials
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            DEFAULT_LOGIN_USER_ID,
            DEFAULT_LOGIN_USERNAME,
            _hash_password(DEFAULT_LOGIN_PASSWORD),
            "管理员",
            "owner",
            "default",
            1,
        ),
    )
    await db.execute(
        "INSERT OR IGNORE INTO accounts (id, name, broker) VALUES ('default', '默认账户', '')"
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO securities_accounts (
            id, login_user_id, name, broker, is_default, display_order, created_at
        )
        SELECT
            id,
            ?,
            COALESCE(NULLIF(name, ''), id),
            COALESCE(broker, ''),
            CASE WHEN id = 'default' THEN 1 ELSE 0 END,
            CASE WHEN id = 'default' THEN 0 ELSE 100 END,
            COALESCE(created_at, datetime('now'))
        FROM accounts
        """,
        (DEFAULT_LOGIN_USER_ID,),
    )
    await _migrate_legacy_login_user_refs(db)
    await db.execute(
        """
        UPDATE login_users
        SET default_securities_account_id = 'default'
        WHERE id = ?
          AND (
            default_securities_account_id IS NULL
            OR default_securities_account_id = ''
            OR NOT EXISTS (
                SELECT 1 FROM securities_accounts
                WHERE id = login_users.default_securities_account_id
                  AND login_user_id = login_users.id
            )
          )
        """,
        (DEFAULT_LOGIN_USER_ID,),
    )
    await db.commit()


async def _migrate_legacy_login_user_refs(db):
    """Assign legacy local-user rows to the admin login account."""
    for table in ("login_sessions", "securities_accounts", "watchlist", "analysis_reports", "report_selection_sets"):
        columns = await _table_columns(db, table)
        if "login_user_id" not in columns:
            continue
        await db.execute(
            f"""
            UPDATE {table}
            SET login_user_id = ?
            WHERE login_user_id IS NULL OR login_user_id = '' OR login_user_id = ?
            """,
            (DEFAULT_LOGIN_USER_ID, LEGACY_LOGIN_USER_ID),
        )
    await db.execute("DELETE FROM login_users WHERE id = ?", (LEGACY_LOGIN_USER_ID,))


async def ensure_daily_pnl_account_key(db):
    """Migrate daily_pnl from global day/code rows to account-scoped rows."""
    rows = await db.execute_fetchall("PRAGMA table_info(daily_pnl)")
    if not rows:
        return
    columns = [row[1] for row in rows]
    pk_columns = [row[1] for row in rows if row[5]]
    if pk_columns == ["account_id", "date", "code6"]:
        return

    select_account = "COALESCE(account_id, 'default')" if "account_id" in columns else "'default'"
    await db.execute("DROP TABLE IF EXISTS daily_pnl_account_key_backup")
    await db.execute("ALTER TABLE daily_pnl RENAME TO daily_pnl_account_key_backup")
    await db.executescript(
        """
        CREATE TABLE daily_pnl (
            date TEXT NOT NULL,
            account_id TEXT DEFAULT 'default',
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
            shares REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (account_id, date, code6)
        );
        """
    )
    await db.execute(
        f"""
        INSERT OR REPLACE INTO daily_pnl (
            date, account_id, code6, total_assets, cash, market_value,
            realized_pnl, unrealized_pnl, total_pnl, total_pnl_pct,
            pnl, close_price, shares, created_at
        )
        SELECT
            date,
            {select_account},
            COALESCE(code6, ''),
            total_assets,
            cash,
            market_value,
            realized_pnl,
            unrealized_pnl,
            total_pnl,
            total_pnl_pct,
            pnl,
            close_price,
            shares,
            created_at
        FROM daily_pnl_account_key_backup
        WHERE date IS NOT NULL AND date != ''
        """
    )
    await db.execute("DROP TABLE daily_pnl_account_key_backup")
    await db.commit()


async def ensure_login_user_scope_columns(db):
    """Add login-user ownership to user-private research/worklist tables."""
    watchlist_columns = await _table_columns(db, "watchlist")
    if "login_user_id" not in watchlist_columns:
        await db.execute("ALTER TABLE watchlist ADD COLUMN login_user_id TEXT DEFAULT 'admin'")
    await ensure_watchlist_login_user_key(db)
    await _migrate_legacy_login_user_refs(db)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_watchlist_login_user_sort ON watchlist(login_user_id, sort_order, added_at)"
    )

    report_columns = await _table_columns(db, "analysis_reports")
    if "login_user_id" not in report_columns:
        await db.execute("ALTER TABLE analysis_reports ADD COLUMN login_user_id TEXT DEFAULT 'admin'")
    await _migrate_legacy_login_user_refs(db)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_analysis_reports_user_code_created ON analysis_reports(login_user_id, code, created_at DESC)"
    )

    selection_columns = await _table_columns(db, "report_selection_sets")
    if "login_user_id" not in selection_columns:
        await db.execute("ALTER TABLE report_selection_sets ADD COLUMN login_user_id TEXT DEFAULT 'admin'")
    await _migrate_legacy_login_user_refs(db)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_report_selection_sets_user_expires ON report_selection_sets(login_user_id, expires_at)"
    )
    await db.commit()


async def ensure_watchlist_login_user_key(db):
    """Migrate watchlist uniqueness from global code to per-login-user code."""
    rows = await db.execute_fetchall("PRAGMA table_info(watchlist)")
    if not rows:
        return
    pk_columns = [row[1] for row in rows if row[5]]
    indexes = await db.execute_fetchall("PRAGMA index_list(watchlist)")
    has_user_code_unique = False
    for index in indexes:
        if not index[2]:
            continue
        info = await db.execute_fetchall(f"PRAGMA index_info({index[1]})")
        columns = [item[2] for item in info]
        if columns == ["login_user_id", "code"]:
            has_user_code_unique = True
            break
    if pk_columns != ["code"] and has_user_code_unique:
        return

    await db.execute("DROP TABLE IF EXISTS watchlist_login_scope_backup")
    await db.execute("ALTER TABLE watchlist RENAME TO watchlist_login_scope_backup")
    await db.executescript(
        """
        CREATE TABLE watchlist (
            code TEXT NOT NULL,
            name TEXT,
            group_name TEXT DEFAULT "默认",
            sort_order INTEGER DEFAULT 0,
            strategy_state TEXT DEFAULT "watch",
            target_buy_price REAL,
            target_sell_price REAL,
            stop_loss_price REAL,
            notes TEXT,
            login_user_id TEXT DEFAULT 'admin',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            strategy_state_updated_at TIMESTAMP,
            UNIQUE(login_user_id, code)
        );
        """
    )
    backup_columns = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(watchlist_login_scope_backup)")}

    def expr(column: str, fallback: str) -> str:
        return column if column in backup_columns else fallback

    await db.execute(
        f"""
        INSERT OR IGNORE INTO watchlist (
            code, name, group_name, sort_order, strategy_state,
            target_buy_price, target_sell_price, stop_loss_price, notes,
            login_user_id, added_at, strategy_state_updated_at
        )
        SELECT
            code,
            {expr('name', "''")},
            {expr('group_name', "'默认'")},
            {expr('sort_order', '0')},
            {expr('strategy_state', "'watch'")},
            {expr('target_buy_price', 'NULL')},
            {expr('target_sell_price', 'NULL')},
            {expr('stop_loss_price', 'NULL')},
            {expr('notes', "''")},
            CASE
                WHEN COALESCE({expr('login_user_id', "'admin'")}, 'admin') IN ('', 'local_owner') THEN 'admin'
                ELSE COALESCE({expr('login_user_id', "'admin'")}, 'admin')
            END,
            {expr('added_at', "datetime('now')")},
            {expr('strategy_state_updated_at', 'NULL')}
        FROM watchlist_login_scope_backup
        WHERE code IS NOT NULL AND code != ''
        """
    )
    await db.execute("DROP TABLE watchlist_login_scope_backup")
    await db.commit()


async def ensure_securities_account_alias_views(db):
    """Expose securities-account semantics without renaming legacy columns."""
    await db.executescript(
        """
        CREATE VIEW IF NOT EXISTS portfolio_securities_view AS
        SELECT *, account_id AS securities_account_id FROM portfolio;

        CREATE VIEW IF NOT EXISTS trades_securities_view AS
        SELECT *, account_id AS securities_account_id FROM trades;

        CREATE VIEW IF NOT EXISTS cash_ledger_securities_view AS
        SELECT *, account_id AS securities_account_id FROM cash_ledger;
        """
    )
    await db.commit()


async def init_db():
    """初始化数据库，创建所有表"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.executescript(SCHEMA)
        await run_migrations(db)
        await ensure_batch_job_item_steps_table(db)
        await ensure_batch_runtime_ops(db)
        await ensure_batch_worker_heartbeats_table(db)
        await ensure_holding_daily_review_tables(db)
        await ensure_identity_tables(db)
        await ensure_login_user_scope_columns(db)
        await ensure_portfolio_account_key(db)
        await ensure_daily_pnl_account_key(db)
        await ensure_securities_account_alias_views(db)
        await ensure_position_plan_adoption_columns(db)
        await ensure_position_plan_market_context_columns(db)
        await ensure_position_plan_item_adoption_columns(db)
        await db.execute("INSERT OR IGNORE INTO accounts (id, name) VALUES ('default', '默认账户')")
        await db.commit()
    return True

async def get_db():
    """获取数据库连接"""
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    return db
