import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.signal_api import router as signal_router


class SignalApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(signal_router, prefix="/api")
        self.client = TestClient(app)

    def test_track_route_uses_service_layer(self):
        with patch(
            "services.signal_tracking_service.add_tracking",
            return_value={"id": 1, "status": "open", "message": "ok"},
        ) as add_tracking:
            resp = self.client.post(
                "/api/signal/track",
                json={
                    "code": "000001",
                    "name": "平安银行",
                    "signal": "BUY",
                    "entry_price": 10.5,
                    "target_price": 12.0,
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], 1)
        add_tracking.assert_called_once()

    def test_list_tracking_route_uses_service_layer(self):
        with patch(
            "services.signal_tracking_service.list_tracking",
            return_value=[{"id": 1, "code": "000001"}],
        ) as list_tracking:
            resp = self.client.get("/api/signal/tracking?status=open")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["code"], "000001")
        list_tracking.assert_called_once_with(
            status="open",
            signal=None,
            code=None,
            window="all",
            model_mode=None,
            depth=None,
        )

    def test_close_tracking_route_uses_service_layer(self):
        with patch(
            "services.signal_tracking_service.close_tracking",
            return_value={"message": "已平仓"},
        ) as close_tracking:
            resp = self.client.post("/api/signal/tracking/5/close", json={"exit_price": 11.0})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["message"], "已平仓")
        close_tracking.assert_called_once_with(5, 11.0)

    def test_latest_signals_route_uses_service_layer(self):
        with patch(
            "services.signal_tracking_service.get_latest_signals",
            return_value={"signals": {"000001": {"id": 1}}},
        ) as latest:
            resp = self.client.get("/api/signal/signals/latest")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["signals"]["000001"]["id"], 1)
        latest.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
