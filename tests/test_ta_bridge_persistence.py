import json
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

    def test_save_report_uses_account_signal_and_keeps_research_signal(self):
        task = AnalysisTask(
            task_id="task-held",
            code="002241",
            name="歌尔股份",
            status="completed",
            result={
                "research_signal": "BUY",
                "account_signal": "HOLD",
                "signal": "HOLD",
                "position_action": "hold",
                "action_reason": "已有持仓，等待回到成本线。",
                "holding_context": {"is_holding": True, "avg_cost": 26.006, "shares": 1000},
                "confidence": 0.71,
            },
            elapsed=3.5,
            depth="deep",
        )

        with (
            patch("scheduler.ta_bridge._get_db", side_effect=lambda: _connect(self.db_path)),
            patch("scheduler.ta_bridge.get_llm_config", return_value={"model_mode": "balanced"}),
        ):
            ta_bridge._save_report_to_db(task)

        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT signal, raw_state FROM analysis_reports WHERE task_id = ?", ("task-held",)).fetchone()

        self.assertEqual(row["signal"], "HOLD")
        raw_state = json.loads(row["raw_state"])
        self.assertEqual(raw_state["research_signal"], "BUY")
        self.assertEqual(raw_state["account_signal"], "HOLD")
        self.assertEqual(raw_state["position_action"], "hold")
        self.assertEqual(raw_state["holding_context"]["avg_cost"], 26.006)

    def test_apply_holding_context_defaults_positive_research_to_hold_for_existing_position(self):
        result = {"signal": "BUY", "reasoning": "研究结论偏多"}
        holding_context = {"is_holding": True, "shares": 1000, "avg_cost": 26.006}

        normalized = ta_bridge.apply_holding_context_to_result(result, holding_context)

        self.assertEqual(normalized["research_signal"], "BUY")
        self.assertEqual(normalized["account_signal"], "HOLD")
        self.assertEqual(normalized["signal"], "HOLD")
        self.assertEqual(normalized["position_action"], "hold")
        self.assertEqual(normalized["holding_context"]["avg_cost"], 26.006)


if __name__ == "__main__":
    unittest.main()
