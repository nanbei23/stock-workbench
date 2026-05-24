"""新闻管理"""
from models.database import get_db


def save_news(code6, news_list):
    """
    批量保存新闻。
    news_list: [{title, content, url, source, sentiment, published_at}, ...]
    """
    db = get_db()
    try:
        for n in news_list:
            # 去重：同 URL 不重复插入
            existing = db.execute(
                'SELECT id FROM news_cache WHERE url = ? AND url != ""',
                (n.get('url', ''),)
            ).fetchone()
            if existing:
                continue
            db.execute(
                '''INSERT INTO news_cache
                   (code6, source, title, content, url, sentiment, published_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (code6, n.get('source', ''), n.get('title', ''),
                 n.get('content', ''), n.get('url', ''),
                 n.get('sentiment', 'neutral'), n.get('published_at', ''))
            )
        db.commit()
    finally:
        db.close()


def get_news(code6, limit=50):
    """获取指定股票的新闻列表"""
    db = get_db()
    try:
        rows = db.execute(
            'SELECT * FROM news_cache WHERE code6 = ? ORDER BY cached_at DESC LIMIT ?',
            (code6, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_sentiment_summary(code6):
    """获取情绪统计：positive, negative, neutral, overall"""
    db = get_db()
    try:
        rows = db.execute(
            'SELECT sentiment, COUNT(*) as cnt FROM news_cache WHERE code6 = ? GROUP BY sentiment',
            (code6,)
        ).fetchall()

        counts = {'positive': 0, 'negative': 0, 'neutral': 0}
        for r in rows:
            s = r['sentiment']
            if s in counts:
                counts[s] = r['cnt']

        total = sum(counts.values())
        if total == 0:
            overall = 'neutral'
        elif counts['positive'] > counts['negative'] * 1.5:
            overall = 'positive'
        elif counts['negative'] > counts['positive'] * 1.5:
            overall = 'negative'
        else:
            overall = 'neutral'

        return {
            'positive': counts['positive'],
            'negative': counts['negative'],
            'neutral': counts['neutral'],
            'overall': overall,
        }
    finally:
        db.close()
