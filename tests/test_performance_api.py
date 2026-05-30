import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.performance_api import router as performance_router


class PerformanceApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(performance_router, prefix="/api")
        self.client = TestClient(app)

    def test_overview_route_passes_filters(self):
        payload = {"signal": {"stats": {}}, "shadow": {"summary": {}}}
        with patch(
            "services.performance_service.overview",
            new=AsyncMock(return_value=payload),
        ) as overview:
            resp = self.client.get("/api/performance/overview?window=30&model_mode=balanced&depth=deep&limit=50")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signal"]["stats"], {})
        overview.assert_awaited_once_with(window="30", model_mode="balanced", depth="deep", limit=50)


if __name__ == "__main__":
    unittest.main()
