import unittest

from scheduler.ta_bridge import friendly_analysis_error


class TradingAgentsBridgeErrorTests(unittest.TestCase):
    def test_friendly_analysis_error_handles_auth_failure(self):
        err = RuntimeError(
            "Error code: 401 - {'error': {'message': 'Authentication Fails, "
            "Your api key: ****bqz9 is invalid'}}"
        )

        message = friendly_analysis_error(err)

        self.assertIn("AI 引擎鉴权失败", message)
        self.assertIn("测试连接", message)
        self.assertNotIn("bqz9", message)


if __name__ == "__main__":
    unittest.main()
