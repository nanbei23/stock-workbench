import unittest
from unittest.mock import AsyncMock, patch

from services import news_service


class NewsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sentiment_counts_news_items(self):
        with patch(
            "services.news_service.get_stock_news",
            new=AsyncMock(return_value=[
                {"sentiment": "positive"},
                {"sentiment": "negative"},
                {"sentiment": "neutral"},
                {"sentiment": "positive"},
            ]),
        ):
            result = await news_service.get_sentiment("000001")

        self.assertEqual(result["total"], 4)
        self.assertEqual(result["positive"], 2)
        self.assertEqual(result["negative"], 1)
        self.assertEqual(result["neutral"], 1)

    async def test_wechat_news_uses_quote_name_when_keyword_missing(self):
        with (
            patch(
                "services.news_service.tencent_quote_batch",
                new=AsyncMock(return_value={"000001": {"name": "平安银行"}}),
            ),
            patch(
                "services.news_service.search_wechat_articles",
                new=AsyncMock(return_value=[{"title": "文章"}]),
            ) as search,
        ):
            result = await news_service.get_wechat_news("000001", "", 5)

        self.assertEqual(result["keyword"], "平安银行")
        self.assertEqual(result["news"], [{"title": "文章"}])
        search.assert_awaited_once_with("平安银行", 5)


if __name__ == "__main__":
    unittest.main()
