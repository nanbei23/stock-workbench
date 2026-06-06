import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from models import database
from services import report_selection_service


class ReportSelectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_database_path = database.DB_PATH
        self.original_service_path = report_selection_service.DB_PATH
        database.DB_PATH = self.db_path
        report_selection_service.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        database.DB_PATH = self.original_database_path
        report_selection_service.DB_PATH = self.original_service_path
        self.tmp.cleanup()

    def test_create_and_get_selection_set_normalizes_unique_codes(self):
        created = report_selection_service.create_selection_set(
            {
                "source_page": "smart_watch",
                "source_label": "智能盯盘批量选择",
                "codes": ["000001", "000001", "sz000002", " 600000 "],
                "filters": {"market": "tradable"},
            }
        )

        loaded = report_selection_service.get_selection_set(created["selection_id"])

        self.assertEqual(loaded["source_page"], "smart_watch")
        self.assertEqual(loaded["source_label"], "智能盯盘批量选择")
        self.assertEqual(loaded["codes"], ["000001", "000002", "600000"])
        self.assertEqual(loaded["count"], 3)
        self.assertEqual(loaded["filters"], {"market": "tradable"})

    def test_expired_selection_set_is_not_returned(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO report_selection_sets
                    (selection_id, source_page, source_label, codes_json, filters_json, expires_at)
                VALUES (?, ?, ?, ?, ?, datetime('now', '-1 minute'))
                """,
                ("sel-expired", "smart_watch", "Expired", '["000001"]', "{}",),
            )
            db.commit()

        with self.assertRaises(HTTPException) as ctx:
            report_selection_service.get_selection_set("sel-expired")

        self.assertEqual(ctx.exception.status_code, 404)

    def test_delete_selection_set_removes_it(self):
        created = report_selection_service.create_selection_set({"codes": ["000001"]})

        result = report_selection_service.delete_selection_set(created["selection_id"])

        self.assertTrue(result["deleted"])
        with self.assertRaises(HTTPException):
            report_selection_service.get_selection_set(created["selection_id"])
