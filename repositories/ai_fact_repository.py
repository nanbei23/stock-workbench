"""Persistence helpers for AI report fact-check workflows."""

import json
import sqlite3

from config import DB_PATH


def open_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_report_row(report_id: int):
    db = open_connection()
    try:
        return db.execute("SELECT * FROM analysis_reports WHERE id=?", (report_id,)).fetchone()
    finally:
        db.close()


def update_report_json_column(report_id: int, column: str, value: dict):
    if column not in {"fact_check", "bystander_verify"}:
        raise ValueError(f"unsupported report json column: {column}")

    db = open_connection()
    try:
        db.execute(
            f"UPDATE analysis_reports SET {column}=? WHERE id=?",
            (json.dumps(value, ensure_ascii=False), report_id),
        )
        db.commit()
    finally:
        db.close()
