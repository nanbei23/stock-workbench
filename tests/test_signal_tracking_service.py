import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from services import signal_tracking_service


class SignalTrackingServiceTests(unittest.TestCase):
    def test_add_tracking_returns_created_message(self):
        req = SimpleNamespace(
            code="000001",
            name="平安银行",
            signal="BUY",
            entry_price=10.5,
            target_price=12.0,
        )
        with patch("scheduler.signal_tracker.create_tracking", return_value=123) as create_tracking:
            result = signal_tracking_service.add_tracking(req)

        self.assertEqual(result["id"], 123)
        self.assertEqual(result["status"], "open")
        create_tracking.assert_called_once_with(
            report_id=0,
            code="000001",
            name="平安银行",
            signal="BUY",
            entry_price=10.5,
            target_price=12.0,
        )

    def test_add_tracking_raises_when_create_fails(self):
        req = SimpleNamespace(
            code="000001",
            name="平安银行",
            signal="UNKNOWN",
            entry_price=10.5,
            target_price=None,
        )
        with patch("scheduler.signal_tracker.create_tracking", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                signal_tracking_service.add_tracking(req)

        self.assertEqual(ctx.exception.status_code, 500)

    def test_close_tracking_raises_404_when_missing(self):
        with patch("scheduler.signal_tracker.close_tracking_manual", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                signal_tracking_service.close_tracking(99, 11.0)

        self.assertEqual(ctx.exception.status_code, 404)

    def test_latest_signals_keeps_latest_open_record_per_code(self):
        with patch(
            "scheduler.signal_tracker.get_tracking_list",
            return_value=[
                {"id": 1, "code": "000001", "signal": "BUY"},
                {"id": 3, "code": "000001", "signal": "SELL"},
                {"id": 2, "code": "600519", "signal": "HOLD"},
            ],
        ):
            result = signal_tracking_service.get_latest_signals()

        self.assertEqual(result["signals"]["000001"]["id"], 3)
        self.assertEqual(result["signals"]["600519"]["id"], 2)


if __name__ == "__main__":
    unittest.main()
