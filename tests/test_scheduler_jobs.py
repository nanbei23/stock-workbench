import unittest
from unittest.mock import AsyncMock, patch

from scheduler import jobs


class SchedulerJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_anomaly_job_skips_when_monitor_disabled(self):
        with (
            patch("scheduler.jobs._is_trading_hours", return_value=True),
            patch(
                "scheduler.jobs.settings_service.get_setting",
                return_value={"key": "anomaly_monitor_enabled", "value": "false"},
            ),
            patch("scheduler.jobs.check_anomalies", new=AsyncMock()) as check_anomalies,
        ):
            await jobs.anomaly_job()

        check_anomalies.assert_not_awaited()

    async def test_anomaly_job_runs_when_monitor_enabled(self):
        with (
            patch("scheduler.jobs._is_trading_hours", return_value=True),
            patch(
                "scheduler.jobs.settings_service.get_setting",
                return_value={"key": "anomaly_monitor_enabled", "value": "true"},
            ),
            patch("scheduler.jobs.check_anomalies", new=AsyncMock()) as check_anomalies,
        ):
            await jobs.anomaly_job()

        check_anomalies.assert_awaited_once()

    async def test_clear_anomaly_logs_job_uses_report_service(self):
        with patch(
            "scheduler.jobs.ai_report_service.clear_anomalies_for_date",
            new=AsyncMock(return_value=3),
        ) as clear_anomalies:
            await jobs.clear_anomaly_logs_job()

        clear_anomalies.assert_awaited_once_with()

    async def test_signal_tracking_job_uses_open_tracking_codes(self):
        async def fake_quotes(codes):
            return {codes[0]: {"price": 10.5}}

        with (
            patch("scheduler.jobs.get_open_tracking_codes", return_value=["000001"]),
            patch("data.helpers.tencent_quote_batch", new=AsyncMock(side_effect=fake_quotes)),
            patch("scheduler.jobs.update_prices", return_value={"updated": 1, "closed": 0}) as update_prices,
        ):
            await jobs.signal_tracking_job()

        update_prices.assert_called_once_with({"000001": 10.5})

    async def test_signal_tracking_job_skips_without_open_codes(self):
        with (
            patch("scheduler.jobs.get_open_tracking_codes", return_value=[]),
            patch("data.helpers.tencent_quote_batch", new=AsyncMock()) as quote_batch,
            patch("scheduler.jobs.update_prices") as update_prices,
        ):
            await jobs.signal_tracking_job()

        quote_batch.assert_not_awaited()
        update_prices.assert_not_called()


if __name__ == "__main__":
    unittest.main()
