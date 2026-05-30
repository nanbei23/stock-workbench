import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.ai_api import _anomaly_log, router as ai_router
from services import ai_signal_service


def _suggestion(code="000001", anomaly=True):
    return {
        "code": code,
        "name": "平安银行",
        "price": 12.3,
        "change_pct": 3.2,
        "advice": "关注放量",
        "anomaly": {"type": "price_surge", "message": "快速上涨"} if anomaly else None,
    }


class AiSignalServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_suggestions_collects_indices_northbound_and_anomalies(self):
        with (
            patch("services.ai_signal_service.get_watchlist_and_portfolio", return_value=[{"code": "000001", "name": "平安银行"}]),
            patch("services.ai_signal_service.get_quote", return_value={"name": "平安银行", "price": 12.3}),
            patch("services.ai_signal_service.get_index_quotes", return_value=[{"code": "sh", "change_pct": 1.0}]),
            patch("services.ai_signal_service._get_northbound_summary", return_value={"total": 3.5, "direction": "net_in"}),
            patch("services.ai_signal_service.evaluate_suggestion", return_value=_suggestion()),
        ):
            result = await ai_signal_service.get_suggestions()

        self.assertEqual(len(result["suggestions"]), 1)
        self.assertEqual(result["indices"][0]["code"], "sh")
        self.assertEqual(result["northbound"]["direction"], "net_in")
        self.assertEqual(result["anomalies"][0]["code"], "000001")
        self.assertNotIn("l1_advice", result["anomalies"][0])

    async def test_trigger_all_appends_anomalies_with_advice(self):
        memory_log = []
        with (
            patch("services.ai_signal_service.get_watchlist_and_portfolio", return_value=[{"code": "000001", "name": "平安银行"}]),
            patch("services.ai_signal_service.get_quote", return_value={"name": "平安银行", "price": 12.3}),
            patch("services.ai_signal_service.evaluate_suggestion", return_value=_suggestion()),
        ):
            result = await ai_signal_service.trigger_all(memory_log)

        self.assertEqual(result["checked"], 1)
        self.assertEqual(len(memory_log), 1)
        self.assertEqual(memory_log[0]["l1_advice"], "关注放量")

    async def test_trigger_stock_returns_404_when_quote_missing(self):
        with patch("services.ai_signal_service.get_quote", return_value=None):
            with self.assertRaises(Exception) as ctx:
                await ai_signal_service.trigger_stock("000404", [])

        self.assertEqual(ctx.exception.status_code, 404)


class AiSignalApiTests(unittest.TestCase):
    def setUp(self):
        _anomaly_log.clear()
        app = FastAPI()
        app.include_router(ai_router, prefix="/api")
        self.client = TestClient(app)

    def tearDown(self):
        _anomaly_log.clear()

    def test_trigger_stock_route_uses_signal_service(self):
        with patch(
            "services.ai_signal_service.trigger_stock",
            return_value={"checked": 1, "anomalies": [], "suggestion": {"code": "000001"}},
        ) as trigger_stock:
            resp = self.client.post("/api/ai/trigger/000001")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["suggestion"]["code"], "000001")
        trigger_stock.assert_called_once_with("000001", _anomaly_log)


if __name__ == "__main__":
    unittest.main()
