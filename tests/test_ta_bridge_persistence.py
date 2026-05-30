import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from models import database
from scheduler import ta_bridge
from tasks import AnalysisTask


def _connect(path):
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    return db


class TaBridgePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_report_uses_model_mode_setting(self):
        task = AnalysisTask(
            task_id="task-1",
            code="000001",
            name="平安银行",
            status="completed",
            result={"confidence": 0.7},
            elapsed=3.5,
            depth="deep",
        )

        with (
            patch("scheduler.ta_bridge._get_db", side_effect=lambda: _connect(self.db_path)),
            patch("scheduler.ta_bridge.get_llm_config", return_value={"model_mode": "economy"}),
        ):
            ta_bridge._save_report_to_db(task)

        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT task_id, code, depth, model_mode FROM analysis_reports"
            ).fetchone()

        self.assertEqual(row, ("task-1", "000001", "deep", "economy"))
        self.assertIsNotNone(task.result["_reportId"])


if __name__ == "__main__":
    unittest.main()
