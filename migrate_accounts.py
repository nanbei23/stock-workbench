"""Add account_id column to relevant tables + create accounts table."""
import sqlite3
DB = 'data/workbench.db'
conn = sqlite3.connect(DB)
c = conn.cursor()

# Create accounts table
c.execute('''CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, broker TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
c.execute("INSERT OR IGNORE INTO accounts (id, name) VALUES ('default', '默认账户')")

# Add account_id to tables that need it
for table in ['portfolio', 'trades', 'conditional_orders', 'pending_positions', 'strategy_params', 'strategy_records']:
    try:
        c.execute(f'ALTER TABLE {table} ADD COLUMN account_id TEXT DEFAULT "default"')
        print(f'  Added account_id to {table}')
    except sqlite3.OperationalError as e:
        if 'duplicate column' in str(e).lower():
            print(f'  {table} already has account_id')
        else:
            print(f'  {table} error: {e}')

conn.commit()
conn.close()
print('Migration complete.')
