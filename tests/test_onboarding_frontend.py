import re
import unittest
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "static" / "js" / "app.js"


class OnboardingFrontendTests(unittest.TestCase):
    def test_onboarding_dialog_is_scrollable_on_desktop(self):
        source = APP_JS.read_text(encoding="utf-8")

        card_rule = re.search(r"\.onboarding-card\{([^}]+)\}", source)

        self.assertIsNotNone(card_rule)
        rule = card_rule.group(1)
        self.assertIn("max-height:calc(100vh - 40px)", rule)
        self.assertIn("overflow:auto", rule)

    def test_watchlist_markdown_import_supports_pasted_text_fallback(self):
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn('id="onboardingWatchlistMdText"', source)
        self.assertIn("overlay.querySelector('#onboardingWatchlistMdText')", source)
        self.assertIn("textInput?.value?.trim()", source)


if __name__ == "__main__":
    unittest.main()
