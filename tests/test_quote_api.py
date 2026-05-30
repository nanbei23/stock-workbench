import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.quote_api import router as quote_router


class QuoteApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(quote_router, prefix="/api")
        self.client = TestClient(app)

    def test_quote_route_uses_service_layer(self):
        with patch(
            "services.quote_service.get_quote",
            new=AsyncMock(return_value={"code": "000001", "price": 10.5}),
        ) as get_quote:
            resp = self.client.get("/api/quote/000001")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["price"], 10.5)
        get_quote.assert_awaited_once_with("000001")

    def test_batch_route_uses_service_layer(self):
        with patch(
            "services.quote_service.get_batch",
            new=AsyncMock(return_value={"000001": {"price": 10.5}}),
        ) as get_batch:
            resp = self.client.get("/api/quote/batch?codes=000001")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["000001"]["price"], 10.5)
        get_batch.assert_awaited_once_with("000001")

    def test_index_route_uses_service_layer(self):
        with patch(
            "services.quote_service.get_indices",
            new=AsyncMock(return_value={"sh": {"price": 3000}}),
        ) as get_indices:
            resp = self.client.get("/api/index")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sh"]["price"], 3000)
        get_indices.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
