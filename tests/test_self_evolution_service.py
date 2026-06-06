import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.self_evolution_api import router as self_evolution_router
from services import investment_profile_service, self_evolution_service


class FakeEmbeddingProvider:
    model = "text-embedding-3-small"
    dimensions = 1536

    def embed_texts(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            if any(token in text for token in ("涨停", "追涨", "高估值", "信号冲突", "仓位过重")):
                vector[0] = 1.0
            elif any(token in text for token in ("低估值", "主线", "左侧", "止盈")):
                vector[1] = 1.0
            else:
                vector[2] = 1.0
            vectors.append(vector)
        return vectors


class SelfEvolutionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        asyncio.run(database.init_db())
        self._seed_learning_data()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def _seed_learning_data(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO analysis_reports
                    (id, code, task_id, signal, confidence, risk_score, raw_state, created_at)
                VALUES
                    (1, '002156', 'task-loss', 'BUY', 0.82, 78,
                     ?, '2026-06-01 09:00:00'),
                    (2, '601138', 'task-win', 'BUY', 0.76, 38,
                     ?, '2026-06-01 09:00:00'),
                    (3, '002156', 'task-after-close', 'BUY', 0.91, 35,
                     ?, '2026-06-04 10:00:00')
                """,
                (
                    json.dumps({"research_signal": "BUY", "account_signal": "BUY", "memory_match": {"matched": True}}, ensure_ascii=False),
                    json.dumps({"research_signal": "BUY", "account_signal": "OVERWEIGHT"}, ensure_ascii=False),
                    json.dumps({"research_signal": "BUY", "account_signal": "BUY", "note": "after trade was closed"}, ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                INSERT INTO signal_tracking
                    (report_id, code, name, signal, signal_date, entry_price, current_price,
                     pnl_pct, excess_return, status, created_at)
                VALUES
                    (1, '002156', '通富微电', 'BUY', '2026-06-01', 71.2, 65.5,
                     -8.0, -10.0, 'closed', '2026-06-01 10:01:00'),
                    (2, '601138', '工业富联', 'BUY', '2026-06-01', 67.6, 79.9,
                     18.2, 12.0, 'closed', '2026-06-01 10:31:00')
                """
            )
            conn.execute(
                """
                INSERT INTO trades
                    (id, code, name, direction, price, shares, amount,
                     commission, stamp_tax, transfer_fee, trade_time, account_id, notes)
                VALUES
                    (10, '002156', '通富微电', 'buy', 71.2, 1000, 71200, 0, 0, 0,
                     '2026-06-01 09:30:00', 'default', 'AI推荐后追入'),
                    (11, '002156', '通富微电', 'sell', 65.5, 1000, 65500, 0, 0, 0,
                     '2026-06-03 14:50:00', 'default', '止损'),
                    (12, '601138', '工业富联', 'buy', 67.6, 1000, 67600, 0, 0, 0,
                     '2026-06-01 09:35:00', 'default', '低估值主线'),
                    (13, '601138', '工业富联', 'sell', 79.9, 1000, 79900, 0, 0, 0,
                     '2026-06-05 14:50:00', 'default', '止盈')
                """
            )
            conn.execute(
                """
                INSERT INTO trade_memories
                    (memory_key, account_id, code, name, status, outcome,
                     trade_ids_json, realized_pnl, realized_pnl_pct, summary,
                     lesson_tags_json, rules_json, veto_lessons_json)
                VALUES
                    ('default:002156:10-11', 'default', '002156', '通富微电',
                     'active', 'failure', '[10,11]', -5700, -8.0,
                     '通富微电案例：涨停后追入、高估值、仓位过重导致亏损。',
                     '["失败案例","涨停追入","高估值","仓位失控"]',
                     '["高估值题材只能小仓试错。"]',
                     '["涨停后追入、高估值和信号冲突同时出现时禁止重仓。"]'),
                    ('default:601138:12-13', 'default', '601138', '工业富联',
                     'active', 'success', '[12,13]', 12300, 18.2,
                     '工业富联案例：低位主线票估值修复。',
                     '["成功案例","低估值","主线"]',
                     '["低估值主线可以分批持有到估值修复。"]',
                     '[]')
                """
            )
            conn.execute(
                """
                INSERT INTO position_plans
                    (plan_id, title, status, adoption_status, stage, context_strategy,
                     recommendations_json, confirmed_at, created_at)
                VALUES
                    ('pp-v3', 'v3 adopted plan', 'active', 'adopted', 'final',
                     'candidate_screening', '[]', '2026-06-01 11:00:00',
                     '2026-06-01 10:59:00')
                """
            )
            conn.execute(
                """
                INSERT INTO position_plan_items
                    (plan_id, code, name, action, suggested_amount, position_pct,
                     suggested_shares, confidence, risk_score, source_report_id)
                VALUES
                    ('pp-v3', '002156', '通富微电', 'buy', 70000, 0.3,
                     1000, 0.82, 78, 1)
                """
            )
            conn.commit()

    def test_init_db_creates_self_evolution_snapshots_table(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='self_evolution_snapshots'"
            ).fetchone()

        self.assertIsNotNone(row)

    def test_build_snapshot_contains_four_layers_and_rules(self):
        snapshot = self_evolution_service.build_snapshot(db_path=self.db_path)

        self.assertEqual(snapshot["version"], "self-evolution-v3")
        self.assertIn("research_signal", snapshot["layers"])
        self.assertIn("account_action", snapshot["layers"])
        self.assertIn("trade_memory", snapshot["layers"])
        self.assertIn("realized_outcome", snapshot["layers"])
        self.assertLess(snapshot["system_score"], 85)
        self.assertTrue(snapshot["rules"])
        self.assertEqual(snapshot["constraints"]["scope"], "account_action_only")

    def test_snapshot_context_and_profile_injection(self):
        persisted = self_evolution_service.run_cycle(db_path=self.db_path)

        context = self_evolution_service.latest_context(db_path=self.db_path)
        profile = investment_profile_service.investment_profile_from_db(self.db_path)

        self.assertEqual(persisted["status"], "active")
        self.assertIn("【AI自我进化画像】", context)
        self.assertIn("research_signal", context)
        self.assertIn("account_action", context)
        self.assertIn("不得改写股票研究信号", context)
        self.assertIn("【AI自我进化画像】", profile["context"])

    def test_rules_include_provenance_and_recommendation_attribution(self):
        persisted = self_evolution_service.run_cycle(db_path=self.db_path)

        self.assertTrue(persisted["rules"])
        first_rule = persisted["rules"][0]
        self.assertIn("evidence", first_rule)
        self.assertTrue(first_rule["evidence"])
        self.assertIn(first_rule["evidence"][0]["source_type"], {"trade_memory", "signal_tracking", "trade_cycle", "position_plan"})

        attributions = self_evolution_service.list_recommendation_attributions(db_path=self.db_path)
        self.assertGreaterEqual(attributions["count"], 2)
        by_code = {item["code"]: item for item in attributions["items"]}
        self.assertEqual(by_code["002156"]["outcome"], "loss")
        self.assertLess(by_code["002156"]["realized_pnl"], 0)
        self.assertIn(1, by_code["002156"]["source_report_ids"])
        self.assertNotIn(3, by_code["002156"]["source_report_ids"])

    def test_recommendation_attribution_pairs_reports_to_individual_trade_cycles(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO analysis_reports
                    (id, code, task_id, signal, confidence, risk_score, raw_state, created_at)
                VALUES
                    (20, '000001', 'cycle-loss-report', 'BUY', 0.80, 30, '{}', '2026-06-10 09:00:00'),
                    (21, '000001', 'cycle-win-report', 'BUY', 0.82, 28, '{}', '2026-06-20 09:00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO trades
                    (id, code, name, direction, price, shares, amount,
                     commission, stamp_tax, transfer_fee, trade_time, account_id, notes)
                VALUES
                    (30, '000001', '平安银行', 'buy', 10, 100, 1000, 0, 0, 0,
                     '2026-06-10 09:30:00', 'default', '第一轮信号后买入'),
                    (31, '000001', '平安银行', 'sell', 9, 100, 900, 0, 0, 0,
                     '2026-06-11 14:50:00', 'default', '第一轮止损'),
                    (32, '000001', '平安银行', 'buy', 10, 100, 1000, 0, 0, 0,
                     '2026-06-20 09:30:00', 'default', '第二轮信号后买入'),
                    (33, '000001', '平安银行', 'sell', 11, 100, 1100, 0, 0, 0,
                     '2026-06-21 14:50:00', 'default', '第二轮止盈')
                """
            )
            conn.commit()

        self_evolution_service.run_cycle(db_path=self.db_path)
        attributions = self_evolution_service.list_recommendation_attributions(db_path=self.db_path, limit=20)
        paired = [item for item in attributions["items"] if item["code"] == "000001"]

        self.assertEqual(len(paired), 2)
        by_reports = {tuple(item["source_report_ids"]): item for item in paired}
        self.assertEqual(by_reports[(20,)]["outcome"], "loss")
        self.assertEqual(by_reports[(20,)]["trade_ids"], [30, 31])
        self.assertLess(by_reports[(20,)]["realized_pnl"], 0)
        self.assertEqual(by_reports[(21,)]["outcome"], "win")
        self.assertEqual(by_reports[(21,)]["trade_ids"], [32, 33])
        self.assertGreater(by_reports[(21,)]["realized_pnl"], 0)

    def test_semantic_memory_search_matches_query_to_failure_case(self):
        from services import trade_memory_service

        provider = FakeEmbeddingProvider()
        trade_memory_service.backfill_trade_memory_embeddings(
            embedding_provider=provider,
            db_path=self.db_path,
        )
        result = self_evolution_service.semantic_memory_search(
            "高估值 涨停追入 仓位太重 信号冲突",
            embedding_provider=provider,
            db_path=self.db_path,
        )

        self.assertEqual(result["retrieval_mode"], "hybrid_vector")
        self.assertGreaterEqual(result["count"], 1)
        self.assertEqual(result["matches"][0]["code"], "002156")
        self.assertIn("高估值", result["matches"][0]["matched_terms"])
        self.assertEqual(result["matches"][0]["trade_ids"], [10, 11])
        self.assertGreater(result["matches"][0]["score"], 0)


class SelfEvolutionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        asyncio.run(database.init_db())
        app = FastAPI()
        app.include_router(self_evolution_router, prefix="/api")
        self.client = TestClient(app)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_self_evolution_api_run_latest_and_context(self):
        run_resp = self.client.post("/api/self-evolution/run")
        self.assertEqual(run_resp.status_code, 200)
        self.assertEqual(run_resp.json()["version"], "self-evolution-v3")

        latest = self.client.get("/api/self-evolution/latest")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.json()["status"], "active")

        context = self.client.get("/api/self-evolution/context")
        self.assertEqual(context.status_code, 200)
        self.assertIn("【AI自我进化画像】", context.json()["context"])

    def test_self_evolution_api_attributions_and_semantic_search(self):
        self.client.post("/api/self-evolution/run")

        attributions = self.client.get("/api/self-evolution/attributions")
        self.assertEqual(attributions.status_code, 200)
        self.assertIn("items", attributions.json())

        search = self.client.post(
            "/api/self-evolution/semantic-search",
            json={"query": "涨停追入 高估值 仓位过重", "limit": 5},
        )
        self.assertEqual(search.status_code, 200)
        self.assertIn("matches", search.json())


if __name__ == "__main__":
    unittest.main()
