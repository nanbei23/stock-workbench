import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.strategy_api import router as strategy_router


class StrategyApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(strategy_router, prefix="/api")
        self.client = TestClient(app)

    def test_get_params_route_uses_service_layer(self):
        with patch(
            "services.strategy_service.get_params",
            new=AsyncMock(return_value={"ok": True, "data": {"code6": "000001"}}),
        ) as get_params:
            resp = self.client.get("/api/strategy/000001/params")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["code6"], "000001")
        get_params.assert_awaited_once_with("000001")

    def test_update_params_route_uses_service_layer(self):
        with patch(
            "services.strategy_service.update_params",
            new=AsyncMock(return_value={"ok": True}),
        ) as update_params:
            resp = self.client.put(
                "/api/strategy/000001/params",
                json={"budget": 10000, "entry_price": 10},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True})
        update_params.assert_awaited_once()
        self.assertEqual(update_params.await_args.args[0], "000001")

    def test_get_state_route_uses_service_layer(self):
        with patch(
            "services.strategy_service.get_state",
            new=AsyncMock(return_value={"ok": True, "data": {"state": "watch"}}),
        ) as get_state:
            resp = self.client.get("/api/strategy/000001/state")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["state"], "watch")
        get_state.assert_awaited_once_with("000001")


if __name__ == "__main__":
    unittest.main()
