import unittest

from fastapi.testclient import TestClient

from app import app


class PositionPlanDetailPageTests(unittest.TestCase):
    def test_position_plan_detail_page_renders_shell(self):
        client = TestClient(app)

        resp = client.get("/position-plans/pp-test")

        self.assertEqual(resp.status_code, 200)
        self.assertIn('data-plan-id="pp-test"', resp.text)
        self.assertIn("建仓计划详情", resp.text)
        self.assertIn("/static/js/position-plan-detail.js", resp.text)


if __name__ == "__main__":
    unittest.main()
