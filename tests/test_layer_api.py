import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.layer_api import router as layer_router


class LayerApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(layer_router, prefix="/api")
        self.client = TestClient(app)

    def test_get_7layer_all_uses_strategy_service_for_strategy_data(self):
        patches = [
            patch("data.quote.get_realtime_quote", new=AsyncMock(return_value={"price": 10.5})),
            patch("data.signal.get_all_signals", new=AsyncMock(return_value={"northbound": {}})),
            patch("data.fund.get_all_fund_data", new=AsyncMock(return_value={"fund": "ok"})),
            patch("data.research.get_reports", new=AsyncMock(return_value=[])),
            patch("data.research.get_eps_forecast", new=AsyncMock(return_value={})),
            patch("data.news.get_stock_news", new=AsyncMock(return_value=[])),
            patch("data.info.get_stock_info", new=AsyncMock(return_value={"name": "平安银行"})),
            patch("data.announce.get_announcements", new=AsyncMock(return_value=[])),
            patch(
                "services.strategy_service.get_params_data",
                new=AsyncMock(return_value={"code6": "000001", "budget": 10000}),
            ),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8] as strategy:
            resp = self.client.get("/api/7layer/000001/all")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["strategy"]["budget"], 10000)
        strategy.assert_awaited_once_with("000001")


if __name__ == "__main__":
    unittest.main()
