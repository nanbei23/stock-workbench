import unittest

from fastapi.testclient import TestClient

from app import app


class ReportDetailPageTests(unittest.TestCase):
    def test_report_detail_page_renders_full_detail_shell(self):
        client = TestClient(app)

        resp = client.get("/reports/123")

        self.assertEqual(resp.status_code, 200)
        self.assertIn('data-report-id="123"', resp.text)
        self.assertIn("AI报告详情", resp.text)
        self.assertIn("七层分析明细", resp.text)
        self.assertIn("投资风格画像", resp.text)
        self.assertIn("/static/js/report-detail.js", resp.text)


if __name__ == "__main__":
    unittest.main()
