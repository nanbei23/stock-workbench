"""Strategy parameter persistence queries."""


async def get_params(db, code: str):
    row = await (
        await db.execute("SELECT * FROM strategy_params WHERE code6=?", (code,))
    ).fetchone()
    return dict(row) if row else {}


async def upsert_params(db, code: str, params):
    await db.execute(
        """
        INSERT INTO strategy_params (code6, budget, entry_price, drop_pct, add_mult,
            bounce_pct, sell_pct, lot_size, target_profit_pct, low_water_manual, buy_prices)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, '[]'))
        ON CONFLICT(code6) DO UPDATE SET
            budget=excluded.budget,
            entry_price=excluded.entry_price,
            drop_pct=excluded.drop_pct,
            add_mult=excluded.add_mult,
            bounce_pct=excluded.bounce_pct,
            sell_pct=excluded.sell_pct,
            lot_size=excluded.lot_size,
            target_profit_pct=excluded.target_profit_pct,
            low_water_manual=excluded.low_water_manual,
            buy_prices=excluded.buy_prices
        """,
        (
            code,
            params.budget,
            params.entry_price,
            params.drop_pct,
            params.add_mult,
            params.bounce_pct,
            params.sell_pct,
            params.lot_size,
            params.target_profit_pct,
            params.low_water_manual,
            params.buy_prices,
        ),
    )
    await db.commit()
