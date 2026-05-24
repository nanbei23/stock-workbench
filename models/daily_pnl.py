"""盈亏日历"""
from models.database import get_db


async def record_daily_snapshot(code6, date, pnl, close_price=None, shares=None):
    """记录每日盈亏快照（per-stock）"""
    db = await get_db()
    try:
        await db.execute(
            '''INSERT OR REPLACE INTO daily_pnl (date, code6, pnl, close_price, shares)
               VALUES (?, ?, ?, ?, ?)''',
            (date, code6, pnl, close_price, shares)
        )
        await db.commit()
    finally:
        await db.close()


async def get_monthly_pnl(year, month):
    """
    获取某月盈亏日历。
    返回 {date_str: {total_pnl, stocks: [{code6, pnl, close_price, shares}]}}
    """
    db = await get_db()
    try:
        start = f'{year}-{month:02d}-01'
        end = f'{year}-{month:02d}-31'
        cursor = await db.execute(
            'SELECT * FROM daily_pnl WHERE date BETWEEN ? AND ? ORDER BY date ASC',
            (start, end)
        )
        rows = await cursor.fetchall()

        calendar = {}
        for r in rows:
            r = dict(r)
            d = r['date']
            if d not in calendar:
                calendar[d] = {'total_pnl': 0, 'stocks': []}
            calendar[d]['total_pnl'] += r.get('pnl') or 0
            if r.get('code6'):
                calendar[d]['stocks'].append({
                    'code6': r['code6'],
                    'pnl': r.get('pnl'),
                    'close_price': r.get('close_price'),
                    'shares': r.get('shares'),
                })

        # 四舍五入
        for d in calendar:
            calendar[d]['total_pnl'] = round(calendar[d]['total_pnl'], 2)

        return calendar
    finally:
        await db.close()


async def get_pnl_stats(start_date, end_date):
    """
    获取时间段盈亏统计。
    返回 {total_pnl, win_days, loss_days, win_rate, best_day, worst_day}
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            '''SELECT date, SUM(pnl) as day_pnl
               FROM daily_pnl WHERE date BETWEEN ? AND ?
               GROUP BY date ORDER BY date ASC''',
            (start_date, end_date)
        )
        rows = await cursor.fetchall()

        if not rows:
            return {
                'total_pnl': 0, 'win_days': 0, 'loss_days': 0,
                'win_rate': 0, 'best_day': None, 'worst_day': None,
            }

        total = 0
        win = 0
        loss = 0
        best = {'date': None, 'pnl': float('-inf')}
        worst = {'date': None, 'pnl': float('inf')}

        for r in rows:
            dp = r['day_pnl'] or 0
            total += dp
            if dp > 0:
                win += 1
            elif dp < 0:
                loss += 1
            if dp > best['pnl']:
                best = {'date': r['date'], 'pnl': round(dp, 2)}
            if dp < worst['pnl']:
                worst = {'date': r['date'], 'pnl': round(dp, 2)}

        days = win + loss
        return {
            'total_pnl': round(total, 2),
            'win_days': win,
            'loss_days': loss,
            'win_rate': round(win / days * 100, 1) if days > 0 else 0,
            'best_day': best,
            'worst_day': worst,
        }
    finally:
        await db.close()
