import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.news_api import router as news_router


class NewsApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(news_router, prefix="/api")
        self.client = TestClient(app)

    def test_news_route_uses_service_layer(self):
        with patch(
            "services.news_service.get_stock_news_for_code",
            new=AsyncMock(return_value={"code": "000001", "news": []}),
        ) as get_news:
            resp = self.client.get("/api/news/000001?limit=5")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"code": "000001", "news": []})
        get_news.assert_awaited_once_with("000001", 5)

    def test_sentiment_route_uses_service_layer(self):
        with patch(
            "services.news_service.get_sentiment",
            new=AsyncMock(return_value={"code": "000001", "total": 0}),
        ) as get_sentiment:
            resp = self.client.get("/api/news/sentiment/000001")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["total"], 0)
        get_sentiment.assert_awaited_once_with("000001")


if __name__ == "__main__":
    unittest.main()
