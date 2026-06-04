#!/usr/bin/env python3
"""Replay trade cash effects into settings and cash_ledger.

Default is dry-run. Use --apply after reviewing the proposed cash balance.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DB_PATH


def cash_key(account_id: str) -> str:
    return "cash_balance_default" if account_id == "default" else f"cash_balance_{account_id}"


def num(value, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def trade_delta(row: sqlite3.Row) -> float:
    amount = num(row["amount"])
    fees = num(row["commission"]) + num(row["stamp_tax"]) + num(row["transfer_fee"])
    direction = str(row["direction"] or "").lower()
    if direction == "buy":
        return round(-(amount + fees), 3)
    if direction == "sell":
        return round(amount - fees, 3)
    return 0.0


def existing_trade_cash_entry(conn: sqlite3.Connection, account_id: str, trade_id: int) -> bool:
    row = conn.execute(
        """
        SELECT id
        FROM cash_ledger
        WHERE account_id = ?
          AND source IN ('trade', 'trade_replay')
          AND notes LIKE ?
        LIMIT 1
        """,
        (account_id, f"%#{trade_id}%"),
    ).fetchone()
    return row is not None


def replay(db_path: Path, account_id: str, since: str, starting_cash: float | None, apply: bool) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        key = cash_key(account_id)
        current_row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        current_cash = num(current_row["value"]) if current_row else 0.0
        running_cash = round(starting_cash if starting_cash is not None else current_cash, 3)
        rows = conn.execute(
            """
            SELECT *
            FROM trades
            WHERE account_id = ?
              AND datetime(trade_time) >= datetime(?)
            ORDER BY datetime(trade_time) ASC, id ASC
            """,
            (account_id, since),
        ).fetchall()
        entries = []
        skipped = 0
        for row in rows:
            if existing_trade_cash_entry(conn, account_id, int(row["id"])):
                skipped += 1
                continue
            delta = trade_delta(row)
            running_cash = round(running_cash + delta, 3)
            direction = f"trade_{str(row['direction'] or 'adjust').lower()}"
            note = f"重放交易现金变动：{row['name'] or row['code']} {row['code']} #{row['id']}"
            entries.append(
                {
                    "trade_id": int(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "direction": direction,
                    "delta": delta,
                    "balance_after": running_cash,
                    "notes": note,
                }
            )
        if apply:
            conn.execute(
                """
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(running_cash)),
            )
            for entry in entries:
                conn.execute(
                    """
                    INSERT INTO cash_ledger (account_id, direction, amount, balance_after, source, notes)
                    VALUES (?, ?, ?, ?, 'trade_replay', ?)
                    """,
                    (account_id, entry["direction"], entry["delta"], entry["balance_after"], entry["notes"]),
                )
            conn.commit()
        return {
            "db_path": str(db_path),
            "account_id": account_id,
            "since": since,
            "apply": apply,
            "starting_cash": round(starting_cash if starting_cash is not None else current_cash, 3),
            "current_cash_before": round(current_cash, 3),
            "ending_cash": running_cash,
            "trade_count": len(rows),
            "replayed": len(entries),
            "skipped_existing": skipped,
            "entries": entries,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay trade cash ledger from local trades.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite database path")
    parser.add_argument("--account", default="default", help="Account id, default: default")
    parser.add_argument("--since", required=True, help="Replay trades since this datetime/date, e.g. 2026-06-04")
    parser.add_argument("--starting-cash", type=float, default=None, help="Cash before the replay window; defaults to current configured cash")
    parser.add_argument("--apply", action="store_true", help="Write settings and cash_ledger")
    args = parser.parse_args()
    result = replay(args.db, args.account, args.since, args.starting_cash, args.apply)
    print(f"db={result['db_path']}")
    print(f"account={result['account_id']} since={result['since']} apply={result['apply']}")
    print(f"starting_cash={result['starting_cash']:.3f}")
    print(f"current_cash_before={result['current_cash_before']:.3f}")
    print(f"ending_cash={result['ending_cash']:.3f}")
    print(f"trades={result['trade_count']} replayed={result['replayed']} skipped_existing={result['skipped_existing']}")
    for entry in result["entries"]:
        print(
            f"#{entry['trade_id']} {entry['code']} {entry['name'] or ''} "
            f"{entry['direction']} delta={entry['delta']:.3f} balance={entry['balance_after']:.3f}"
        )
    if not args.apply:
        print("dry-run only; rerun with --apply to write changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
