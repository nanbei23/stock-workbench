import unittest
from datetime import datetime
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

    async def test_clear_anomaly_logs_job_clears_stale_logs_only(self):
        with patch(
            "scheduler.jobs.ai_report_service.clear_stale_anomalies",
            new=AsyncMock(return_value=3),
        ) as clear_anomalies:
            await jobs.clear_anomaly_logs_job()

        clear_anomalies.assert_awaited_once_with()

    async def test_daily_pnl_snapshot_job_uses_portfolio_service(self):
        with patch(
            "scheduler.jobs.portfolio_service.ensure_daily_pnl_snapshot",
            new=AsyncMock(return_value={"status": "ok", "written": 2}),
        ) as snapshot:
            await jobs.daily_pnl_snapshot_job()

        snapshot.assert_awaited_once_with()

    async def test_daily_decision_report_job_runs_once_at_configured_time(self):
        settings = {
            "daily_decision_auto_enabled": "true",
            "daily_decision_auto_time": "15:20",
            "daily_decision_account_id": "default",
        }

        def fake_get_setting(key):
            return {"key": key, "value": settings.get(key, "")}

        with (
            patch("scheduler.jobs.settings_service.get_setting", side_effect=fake_get_setting),
            patch("scheduler.jobs.holding_review_service.review_exists_for_date", new=AsyncMock(return_value=False)) as exists,
            patch(
                "scheduler.jobs.holding_review_service.run_scheduled_daily_decision_report",
                new=AsyncMock(return_value={"review_id": "dr-1"}),
            ) as run_review,
        ):
            await jobs.daily_decision_report_job(now=datetime(2026, 6, 4, 15, 20))

        exists.assert_awaited_once_with(date_text="2026-06-04", account_id="default")
        run_review.assert_awaited_once_with(account_id="default", date_text="2026-06-04")

    async def test_daily_decision_report_job_skips_existing_review(self):
        settings = {
            "daily_decision_auto_enabled": "true",
            "daily_decision_auto_time": "15:20",
            "daily_decision_account_id": "default",
        }

        def fake_get_setting(key):
            return {"key": key, "value": settings.get(key, "")}

        with (
            patch("scheduler.jobs.settings_service.get_setting", side_effect=fake_get_setting),
            patch("scheduler.jobs.holding_review_service.review_exists_for_date", new=AsyncMock(return_value=True)) as exists,
            patch("scheduler.jobs.holding_review_service.run_scheduled_daily_decision_report", new=AsyncMock()) as run_review,
        ):
            await jobs.daily_decision_report_job(now=datetime(2026, 6, 4, 15, 20))

        exists.assert_awaited_once_with(date_text="2026-06-04", account_id="default")
        run_review.assert_not_awaited()

    async def test_self_evolution_job_runs_at_configured_time(self):
        settings = {
            "self_evolution_auto_enabled": "true",
            "self_evolution_auto_time": "15:45",
        }

        def fake_get_setting(key):
            return {"key": key, "value": settings.get(key, "")}

        with (
            patch("scheduler.jobs.settings_service.get_setting", side_effect=fake_get_setting),
            patch("scheduler.jobs.self_evolution_service.run_cycle", return_value={"snapshot_id": "sev3-1"}) as run_cycle,
        ):
            await jobs.self_evolution_job(now=datetime(2026, 6, 4, 15, 45))

        run_cycle.assert_called_once_with()

    async def test_self_evolution_job_skips_when_disabled(self):
        with (
            patch("scheduler.jobs.settings_service.get_setting", return_value={"key": "self_evolution_auto_enabled", "value": "false"}),
            patch("scheduler.jobs.self_evolution_service.run_cycle") as run_cycle,
        ):
            await jobs.self_evolution_job(now=datetime(2026, 6, 4, 15, 45))

        run_cycle.assert_not_called()

    def test_setup_scheduler_registers_daily_pnl_snapshot_job(self):
        with patch.object(jobs.scheduler, "start"):
            scheduler = jobs.setup_scheduler()

        self.assertIsNotNone(scheduler.get_job("daily_pnl_snapshot"))

    def test_setup_scheduler_registers_daily_decision_report_guard_job(self):
        with patch.object(jobs.scheduler, "start"):
            scheduler = jobs.setup_scheduler()

        self.assertIsNotNone(scheduler.get_job("daily_decision_report_guard"))

    def test_setup_scheduler_registers_self_evolution_guard_job(self):
        with patch.object(jobs.scheduler, "start"):
            scheduler = jobs.setup_scheduler()

        self.assertIsNotNone(scheduler.get_job("self_evolution_guard"))

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
