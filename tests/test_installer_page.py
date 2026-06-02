import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import app


INSTALLER_HTML = Path(__file__).resolve().parents[1] / "installer" / "macos_x86" / "index.html"


class InstallerPageTests(unittest.TestCase):
    def test_installer_page_contains_database_initialization_controls(self):
        source = INSTALLER_HTML.read_text(encoding="utf-8")

        self.assertIn("数据库初始化", source)
        self.assertIn("资产初始化", source)
        self.assertIn("初始持仓股", source)
        self.assertIn('id="installerApiBase"', source)
        self.assertIn('id="installerWatchlistMd"', source)
        self.assertIn('id="installerWatchlistMdText"', source)
        self.assertIn('id="installerCash"', source)
        self.assertIn('class="installer-pos-code"', source)
        self.assertIn('class="installer-pos-cost"', source)
        self.assertIn('class="installer-pos-shares"', source)
        self.assertIn('id="installerPositionRows"', source)
        self.assertIn("function importInstallerWatchlist", source)
        self.assertIn("function saveInstallerAssets", source)
        self.assertIn("/api/watchlist/import-md", source)
        self.assertIn("/api/portfolio/cash-balance", source)
        self.assertIn("/api/trades", source)

    def test_file_installer_can_call_local_initialization_api(self):
        client = TestClient(app)

        for path in ["/api/watchlist/import-md", "/api/portfolio/cash-balance", "/api/trades"]:
            with self.subTest(path=path):
                resp = client.options(
                    path,
                    headers={
                        "Origin": "null",
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "content-type",
                    },
                )

                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.headers.get("access-control-allow-origin"), "null")


if __name__ == "__main__":
    unittest.main()
