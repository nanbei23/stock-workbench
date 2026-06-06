import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.portfolio_api import router as portfolio_router
from services import investment_profile_service
from services import trade_memory_service


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


class BrokenEmbeddingProvider:
    model = "text-embedding-3-small"
    dimensions = 1536

    def embed_texts(self, texts):
        raise RuntimeError("401 invalid_api_key: Incorrect API key provided")


class FakeEmbeddingResponse:
    text = ""
    status_code = 200
    headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"index": 0, "embedding": [0.1] * 1536}]}


class CapturingHttpxClient:
    last_request = {}

    def __init__(self, timeout):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, headers, json):
        CapturingHttpxClient.last_request = {"url": url, "headers": headers, "json": json}
        return FakeEmbeddingResponse()


class TradeMemoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        asyncio.run(database.init_db())
        self._seed_closed_trades()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def _seed_closed_trades(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO trades
                    (id, code, name, direction, price, shares, amount,
                     commission, stamp_tax, transfer_fee, trade_time, account_id, notes)
                VALUES
                    (1, '601138', '工业富联', 'buy', 67.61, 1000, 67610, 6.761, 0, 0.676, '2026-06-01 09:30:00', 'default', '低位买入'),
                    (2, '601138', '工业富联', 'sell', 79.99, 1000, 79990, 7.999, 0, 0.8, '2026-06-02 15:00:00', 'default', '估值修复卖出'),
                    (3, '002156', '通富微电', 'buy', 71.2074, 3100, 220742.94, 0, 0, 0, '2026-06-04 03:22:56', 'default', '涨停后追入'),
                    (4, '002156', '通富微电', 'sell', 68.0, 3100, 210800, 0, 0, 0, '2026-06-05 05:27:53', 'default', '止损清仓')
                """
            )
            conn.execute(
                """
                INSERT INTO analysis_reports
                    (id, code, signal, confidence, risk_score, final_decision, created_at)
                VALUES
                    (10, '601138', 'HOLD', 0.35, 7, '短期过热，保护利润', '2026-06-02 21:50:55'),
                    (11, '002156', 'SELL', 0.95, 92, '涨停前信号冲突，卖出', '2026-06-04 03:00:00'),
                    (12, '002156', 'BUY', 0.88, 20, '清仓后重新看多，不能污染本次复盘', '2026-06-05 08:42:23')
                """
            )
            conn.commit()

    def test_default_embedding_provider_resolves_model_library_reference(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO model_providers
                    (id, name, base_url, api_key, models_json, default_model, embedding_model, embedding_dimensions, usage_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "provider-embedding",
                    "Provider Embedding",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "sk-dashscope",
                    '["text-embedding-v4"]',
                    "",
                    "text-embedding-v4",
                    1536,
                    '["embedding"]',
                ),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                [
                    ("embedding_provider_id", "provider-embedding"),
                    ("embedding_model", "text-embedding-v4"),
                    ("embedding_endpoint", "https://legacy.example.com/v1/embeddings"),
                    ("embedding_api_key", "sk-legacy"),
                ],
            )
            conn.commit()

        provider = trade_memory_service._default_embedding_provider(self.db_path)

        self.assertIsNotNone(provider)
        self.assertEqual(provider.api_key, "sk-dashscope")
        self.assertEqual(provider.endpoint, "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings")
        self.assertEqual(provider.model, "text-embedding-v4")

    def _activate_memory(self, code: str, **overrides):
        draft = trade_memory_service.generate_memory_draft(code, db_path=self.db_path)
        return trade_memory_service.save_trade_memory(
            {
                **draft,
                "status": "active",
                **overrides,
            },
            db_path=self.db_path,
        )

    def test_init_db_creates_trade_memories_table(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_memories'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_list_closed_trade_candidates_returns_completed_cycles(self):
        result = trade_memory_service.list_closed_trade_candidates(db_path=self.db_path)

        self.assertEqual(result["count"], 2)
        by_code = {item["code"]: item for item in result["candidates"]}
        self.assertAlmostEqual(by_code["601138"]["realized_pnl"], 12363.764, places=3)
        self.assertAlmostEqual(by_code["002156"]["realized_pnl"], -9942.94, places=2)
        self.assertEqual(by_code["601138"]["status"], "pending_review")

    def test_generate_memory_draft_classifies_success_and_failure_lessons(self):
        win = trade_memory_service.generate_memory_draft("601138", db_path=self.db_path)
        loss = trade_memory_service.generate_memory_draft("002156", db_path=self.db_path)

        self.assertEqual(win["code"], "601138")
        self.assertEqual(win["outcome"], "success")
        self.assertIn("低位", " ".join(win["lesson_tags"]))
        self.assertIn("控制仓位", " ".join(win["rules"]))

        self.assertEqual(loss["outcome"], "failure")
        self.assertIn("仓位", " ".join(loss["lesson_tags"]))
        self.assertIn("涨停", " ".join(loss["veto_lessons"]))

    def test_generate_memory_draft_uses_report_before_trade_not_after_close(self):
        loss = trade_memory_service.generate_memory_draft("002156", db_path=self.db_path)

        self.assertEqual(loss["report_context"]["id"], 11)
        self.assertEqual(loss["report_context"]["signal"], "SELL")
        self.assertNotEqual(loss["report_context"]["id"], 12)

    def test_save_active_memory_and_format_context(self):
        draft = trade_memory_service.generate_memory_draft("002156", db_path=self.db_path)
        saved = trade_memory_service.save_trade_memory(
            {
                **draft,
                "status": "active",
                "summary": "通富微电案例：高估值、涨停后追入、信号冲突、仓位过重导致亏损。",
            },
            db_path=self.db_path,
        )

        self.assertEqual(saved["status"], "active")
        context = trade_memory_service.trade_memory_context(db_path=self.db_path)
        self.assertIn("【交易复盘记忆】", context)
        self.assertIn("通富微电", context)
        self.assertIn("高估值", context)

    def test_investment_profile_context_includes_active_trade_memory(self):
        self._activate_memory("601138")

        profile = investment_profile_service.investment_profile_from_db(self.db_path)

        self.assertIn("【交易复盘记忆】", profile["context"])
        self.assertIn("工业富联", profile["context"])

    def test_related_trade_memories_ranks_same_code_and_scenario_match(self):
        self._activate_memory(
            "002156",
            summary="通富微电案例：涨停后追入、高估值、AI卖出信号冲突、仓位过重导致亏损。",
            lesson_tags=["亏损案例", "涨停追入", "高估值", "仓位过重", "信号冲突"],
            veto_lessons=["涨停后追入、高估值和信号冲突同时出现时禁止重仓。"],
        )
        self._activate_memory(
            "601138",
            summary="工业富联案例：低估值主线左侧买入，估值修复后止盈。",
            lesson_tags=["盈利案例", "低估值", "主线", "左侧成功"],
        )

        related = trade_memory_service.related_trade_memories(
            code="002156",
            report_text="涨停后追入，高估值，AI 卖出信号冲突",
            db_path=self.db_path,
        )

        self.assertGreaterEqual(related["count"], 1)
        self.assertEqual(related["matches"][0]["code"], "002156")
        self.assertIn("涨停", related["matches"][0]["match_reason"])
        self.assertIn("高估值", related["scenario_tags"])

    def test_related_trade_memories_uses_sqlite_vec_and_preserves_cycle_level_causality(self):
        first = trade_memory_service.save_trade_memory(
            {
                "memory_key": "default:000001:30-31",
                "account_id": "default",
                "code": "000001",
                "name": "平安银行",
                "status": "active",
                "outcome": "failure",
                "trade_ids": [30, 31],
                "opened_at": "2026-06-10 09:30:00",
                "closed_at": "2026-06-11 14:50:00",
                "realized_pnl": -100,
                "realized_pnl_pct": -10,
                "summary": "第一轮：涨停后追涨，高估值和信号冲突下重仓，亏损止损。",
                "lesson_tags": ["亏损案例", "涨停追入", "高估值", "仓位过重", "信号冲突"],
                "veto_lessons": ["涨停追入、高估值和信号冲突同时出现时禁止重仓。"],
            },
            db_path=self.db_path,
        )
        second = trade_memory_service.save_trade_memory(
            {
                "memory_key": "default:000001:32-33",
                "account_id": "default",
                "code": "000001",
                "name": "平安银行",
                "status": "active",
                "outcome": "success",
                "trade_ids": [32, 33],
                "opened_at": "2026-06-20 09:30:00",
                "closed_at": "2026-06-21 14:50:00",
                "realized_pnl": 100,
                "realized_pnl_pct": 10,
                "summary": "第二轮：低估值主线左侧买入，估值修复后止盈。",
                "lesson_tags": ["盈利案例", "低估值", "主线", "左侧成功"],
            },
            db_path=self.db_path,
        )

        provider = FakeEmbeddingProvider()
        indexed = trade_memory_service.backfill_trade_memory_embeddings(
            embedding_provider=provider,
            db_path=self.db_path,
        )
        related = trade_memory_service.related_trade_memories(
            code="000001",
            report_text="涨停后追涨，高估值，AI 卖出信号冲突",
            embedding_provider=provider,
            db_path=self.db_path,
        )

        self.assertGreaterEqual(indexed["indexed"], 2)
        self.assertEqual(related["retrieval_mode"], "hybrid_vector")
        self.assertEqual(related["matches"][0]["memory_key"], first["memory_key"])
        self.assertEqual(related["matches"][0]["trade_ids"], [30, 31])
        self.assertIn("向量召回", related["matches"][0]["match_reason"])
        by_key = {item["memory_key"]: item for item in related["matches"]}
        self.assertEqual(by_key[first["memory_key"]]["outcome"], "failure")
        self.assertEqual(by_key[second["memory_key"]]["trade_ids"], [32, 33])

    def test_active_memory_save_auto_indexes_embedding_when_provider_is_available(self):
        draft = trade_memory_service.generate_memory_draft("002156", db_path=self.db_path)
        provider = FakeEmbeddingProvider()

        saved = trade_memory_service.save_trade_memory(
            {
                **draft,
                "status": "active",
                "summary": "通富微电案例：涨停后追入、高估值、信号冲突、仓位过重导致亏损。",
            },
            embedding_provider=provider,
            db_path=self.db_path,
        )
        status = trade_memory_service.trade_memory_embedding_status(db_path=self.db_path)
        related = trade_memory_service.related_trade_memories(
            code="002156",
            report_text="涨停追涨 高估值 信号冲突",
            embedding_provider=provider,
            db_path=self.db_path,
        )

        self.assertEqual(saved["embedding_index"]["status"], "indexed")
        self.assertEqual(status["active_memories"], 1)
        self.assertEqual(status["indexed_memories"], 1)
        self.assertEqual(status["missing_embeddings"], 0)
        self.assertEqual(related["retrieval_mode"], "hybrid_vector")

    def test_embedding_status_reports_missing_and_indexed_memories(self):
        self._activate_memory("002156")
        self._activate_memory("601138")
        provider = FakeEmbeddingProvider()
        trade_memory_service.backfill_trade_memory_embeddings(
            code="002156",
            embedding_provider=provider,
            db_path=self.db_path,
        )

        status = trade_memory_service.trade_memory_embedding_status(db_path=self.db_path)

        self.assertTrue(status["sqlite_vec_available"])
        self.assertEqual(status["active_memories"], 2)
        self.assertEqual(status["indexed_memories"], 1)
        self.assertEqual(status["missing_embeddings"], 1)
        self.assertEqual(status["coverage_pct"], 50.0)
        self.assertEqual(status["missing"][0]["code"], "601138")

    def test_embedding_provider_does_not_reuse_primary_llm_api_key(self):
        self._activate_memory("002156")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('api_key', 'primary-llm-key')")
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('embedding_api_key', '')")
            conn.commit()
        old_env = os.environ.pop("OPENAI_API_KEY", None)
        try:
            result = trade_memory_service.backfill_trade_memory_embeddings(db_path=self.db_path)
        finally:
            if old_env is not None:
                os.environ["OPENAI_API_KEY"] = old_env

        self.assertFalse(result["enabled"])
        self.assertIn("embedding provider not configured", result["errors"])

    def test_test_embedding_connection_reports_missing_key(self):
        result = trade_memory_service.test_embedding_connection(
            api_key="",
            endpoint="https://api.openai.com/v1/embeddings",
            model="text-embedding-3-small",
            dimensions=1536,
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "missing_api_key")
        self.assertIn("API密钥未配置", result["message"])

    def test_test_embedding_connection_uses_provider_and_reports_success(self):
        result = trade_memory_service.test_embedding_connection(
            api_key="sk-test",
            endpoint="https://api.openai.com/v1/embeddings",
            model="text-embedding-3-small",
            dimensions=1536,
            embedding_provider=FakeEmbeddingProvider(),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["model"], "text-embedding-3-small")
        self.assertEqual(result["dimensions"], 1536)
        self.assertEqual(result["embedding_count"], 1)

    def test_test_embedding_connection_returns_diagnostic_error(self):
        result = trade_memory_service.test_embedding_connection(
            api_key="sk-bad",
            endpoint="https://api.openai.com/v1/embeddings",
            model="text-embedding-3-small",
            dimensions=1536,
            embedding_provider=BrokenEmbeddingProvider(),
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "connection_error")
        self.assertIn("Incorrect API key", result["message"])

    def test_dashscope_openai_compatible_base_url_appends_embeddings_path(self):
        with patch("services.trade_memory_service.httpx.Client", CapturingHttpxClient):
            provider = trade_memory_service.OpenAIEmbeddingProvider(
                api_key="dashscope-key",
                endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
                model="text-embedding-v4",
                dimensions=1536,
            )
            vectors = provider.embed_texts(["ping"])

        self.assertEqual(len(vectors[0]), 1536)
        self.assertEqual(
            CapturingHttpxClient.last_request["url"],
            "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        )
        self.assertEqual(CapturingHttpxClient.last_request["json"]["model"], "text-embedding-v4")
        self.assertEqual(CapturingHttpxClient.last_request["json"]["dimensions"], 1536)
        self.assertEqual(CapturingHttpxClient.last_request["json"]["encoding_format"], "float")

    def test_trade_memory_context_includes_v2_constraints_and_output_contract(self):
        self._activate_memory(
            "002156",
            summary="通富微电案例：涨停后追入、高估值、信号冲突、仓位过重导致亏损。",
            lesson_tags=["亏损案例", "涨停追入", "高估值", "仓位过重", "信号冲突"],
        )

        context = trade_memory_service.trade_memory_context(
            report_text="涨停后追入，高估值，AI 卖出信号冲突",
            db_path=self.db_path,
        )

        self.assertIn("【交易复盘记忆约束】", context)
        self.assertIn("只校准账户动作", context)
        self.assertIn("不得直接覆盖股票研究信号", context)
        self.assertIn("memory_match", context)
        self.assertIn("memory_adjustments", context)
        self.assertIn("通富微电", context)

    def test_investment_profile_context_includes_trade_memory_constraints(self):
        self._activate_memory(
            "002156",
            summary="通富微电案例：涨停后追入、高估值、信号冲突、仓位过重导致亏损。",
        )

        profile = investment_profile_service.investment_profile_from_db(self.db_path)

        self.assertIn("【交易复盘记忆约束】", profile["context"])
        self.assertIn("不得直接覆盖股票研究信号", profile["context"])

    def test_investment_profile_context_can_scope_trade_memory_to_report_case(self):
        self._activate_memory(
            "002156",
            summary="通富微电案例：涨停后追入、高估值、信号冲突、仓位过重导致亏损。",
            lesson_tags=["亏损案例", "涨停追入", "高估值", "仓位过重", "信号冲突"],
        )
        self._activate_memory(
            "601138",
            summary="工业富联案例：低估值主线左侧买入，估值修复后止盈。",
            lesson_tags=["盈利案例", "低估值", "主线", "左侧成功"],
        )

        profile = investment_profile_service.investment_profile_from_db(
            self.db_path,
            code="002156",
            report_text="涨停追入 高估值 信号冲突",
        )

        self.assertIn("通富微电", profile["context"])
        self.assertNotIn("工业富联案例", profile["context"])

    def test_context_injection_status_documents_all_report_paths(self):
        status = trade_memory_service.context_injection_status()

        self.assertEqual(status["version"], "trade-memory-v2")
        self.assertIn("single_stock_report", status["injection_points"])
        self.assertIn("batch_snapshot_report", status["injection_points"])
        self.assertIn("daily_holding_review", status["injection_points"])
        self.assertIn("position_plan", status["injection_points"])
        self.assertEqual(status["constraints"]["scope"], "account_action_only")
        self.assertIn("hermes_operation_parser", status["excluded_points"])
        self.assertIn("intent parser", status["excluded_points"]["hermes_operation_parser"]["reason"])


class TradeMemoryApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        asyncio.run(database.init_db())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO trades
                    (code, name, direction, price, shares, amount, trade_time, account_id)
                VALUES
                    ('601138', '工业富联', 'buy', 67.61, 1000, 67610, '2026-06-01 09:30:00', 'default'),
                    ('601138', '工业富联', 'sell', 79.99, 1000, 79990, '2026-06-02 15:00:00', 'default')
                """
            )
            conn.commit()
        app = FastAPI()
        app.include_router(portfolio_router, prefix="/api")
        self.client = TestClient(app)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_trade_memory_api_flow(self):
        candidates = self.client.get("/api/trade-memories/candidates")
        self.assertEqual(candidates.status_code, 200)
        self.assertEqual(candidates.json()["count"], 1)

        draft = self.client.post("/api/trade-memories/draft", json={"code": "601138"})
        self.assertEqual(draft.status_code, 200)
        self.assertEqual(draft.json()["code"], "601138")

        saved = self.client.post(
            "/api/trade-memories",
            json={**draft.json(), "status": "active", "summary": "工业富联案例：低位主线票快速估值修复。"},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["status"], "active")

        context = self.client.get("/api/trade-memories/context")
        self.assertEqual(context.status_code, 200)
        self.assertIn("工业富联", context.json()["context"])

    def test_trade_memory_related_and_injection_map_api(self):
        draft = self.client.post("/api/trade-memories/draft", json={"code": "601138"})
        self.assertEqual(draft.status_code, 200)
        saved = self.client.post(
            "/api/trade-memories",
            json={
                **draft.json(),
                "status": "active",
                "summary": "工业富联案例：低位主线票快速估值修复。",
                "lesson_tags": ["盈利案例", "低估值", "主线"],
            },
        )
        self.assertEqual(saved.status_code, 200)

        related = self.client.post(
            "/api/trade-memories/related",
            json={"code": "601138", "report_text": "AI服务器主线，低估值，回踩买入"},
        )
        self.assertEqual(related.status_code, 200)
        self.assertEqual(related.json()["matches"][0]["code"], "601138")

        injection = self.client.get("/api/trade-memories/injection-map")
        self.assertEqual(injection.status_code, 200)
        self.assertIn("position_plan", injection.json()["injection_points"])
        self.assertEqual(injection.json()["constraints"]["scope"], "account_action_only")

    def test_trade_memory_embedding_backfill_api(self):
        with patch(
            "api.portfolio_api.trade_memory_service.backfill_trade_memory_embeddings",
            return_value={"enabled": True, "model": "text-embedding-3-small", "dimensions": 1536, "indexed": 1, "skipped": 0, "errors": []},
        ) as backfill:
            resp = self.client.post("/api/trade-memories/embeddings/backfill", json={"account_id": "default", "limit": 20})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["model"], "text-embedding-3-small")
        self.assertEqual(resp.json()["indexed"], 1)
        backfill.assert_called_once_with(account_id="default", limit=20)

    def test_trade_memory_embedding_status_api(self):
        with patch(
            "api.portfolio_api.trade_memory_service.trade_memory_embedding_status",
            return_value={"active_memories": 2, "indexed_memories": 1, "missing_embeddings": 1, "coverage_pct": 50.0},
        ) as status:
            resp = self.client.get("/api/trade-memories/embeddings/status")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["coverage_pct"], 50.0)
        status.assert_called_once_with(account_id="default")

    def test_trade_memory_embedding_test_connection_api(self):
        with patch(
            "api.portfolio_api.trade_memory_service.test_embedding_connection",
            return_value={"status": "error", "error_type": "invalid_api_key", "message": "Incorrect API key provided"},
        ) as tester:
            resp = self.client.post(
                "/api/trade-memories/embeddings/test-connection",
                json={
                    "api_key": "sk-bad",
                    "endpoint": "https://api.openai.com/v1/embeddings",
                    "model": "text-embedding-3-small",
                    "dimensions": 1536,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["error_type"], "invalid_api_key")
        self.assertIn("Incorrect API key", resp.json()["message"])
        tester.assert_called_once_with(
            api_key="sk-bad",
            endpoint="https://api.openai.com/v1/embeddings",
            model="text-embedding-3-small",
            dimensions=1536,
        )


if __name__ == "__main__":
    unittest.main()
