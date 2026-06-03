import unittest

from scripts.diagnose_kline_blank import validate_rows


class DiagnoseKlineBlankTests(unittest.TestCase):
    def test_validate_rows_accepts_ordered_daily_kline(self):
        rows = [
            {"date": "2026-06-01", "open": 10, "high": 11, "low": 9.8, "close": 10.5, "volume": 100, "amount": 1000},
            {"date": "2026-06-02", "open": 10.5, "high": 11.2, "low": 10.1, "close": 11, "volume": 120, "amount": 1300},
        ]

        result = validate_rows(rows, code="000001", period="day", count=120, source="test")

        self.assertTrue(result.ok)
        self.assertEqual(result.row_count, 2)
        self.assertEqual(result.issues, [])

    def test_validate_rows_flags_duplicate_frontend_time_values(self):
        rows = [
            {"date": "2026-06-01 00:00:00", "open": 10, "high": 11, "low": 9.8, "close": 10.5, "volume": 100, "amount": 1000},
            {"date": "2026-06-01 15:00:00", "open": 10.5, "high": 11.2, "low": 10.1, "close": 11, "volume": 120, "amount": 1300},
        ]

        result = validate_rows(rows, code="000001", period="day", count=120, source="test")

        self.assertFalse(result.ok)
        self.assertIn("duplicate_frontend_time_values: 1", result.issues)
        self.assertIn("non_increasing_frontend_time_values: 1", result.issues)

    def test_validate_rows_flags_invalid_ohlc(self):
        rows = [
            {"date": "2026-06-01", "open": 10, "high": 9, "low": 9.8, "close": 10.5, "volume": 100, "amount": 1000},
        ]

        result = validate_rows(rows, code="000001", period="day", count=120, source="test")

        self.assertFalse(result.ok)
        self.assertTrue(any("invalid_ohlc" in issue for issue in result.issues))


if __name__ == "__main__":
    unittest.main()
