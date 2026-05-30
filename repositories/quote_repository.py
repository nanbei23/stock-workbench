"""Quote-related database lookups."""


async def get_position_cost(db, code: str):
    row = await (
        await db.execute(
            "SELECT avg_cost, total_shares FROM portfolio WHERE code = ?",
            (code,),
        )
    ).fetchone()
    return dict(row) if row else None
