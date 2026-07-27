from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.providers.topics_catalog import (
    _fetch_recent_news_rss,
    _is_focus_topic,
    _is_recent_item,
    load_peer_topics,
)


class _Response:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def get(self, _url: str) -> _Response:
        return _Response(self.content)


class TopicsCatalogTests(unittest.TestCase):
    def test_focus_and_recent_week_filter(self) -> None:
        config = {"topics": {"focus_keywords": ["企业", "管理", "项目", "组织"], "recent_days": 7}}
        self.assertTrue(_is_focus_topic("企业组织管理升级", config))
        self.assertFalse(_is_focus_topic("明星娱乐新闻", config))

        recent = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        self.assertTrue(_is_recent_item({"published_at": recent}, config))
        self.assertFalse(_is_recent_item({"published_at": old}, config))

    def test_rss_only_keeps_recent_focus_topics(self) -> None:
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        xml = f"""<?xml version="1.0" encoding="utf-8"?>
        <rss><channel>
          <item><title>企业项目管理新趋势</title><link>https://example.com/new</link><pubDate>{recent}</pubDate></item>
          <item><title>企业管理旧闻</title><link>https://example.com/old</link><pubDate>{old}</pubDate></item>
          <item><title>娱乐热点</title><link>https://example.com/fun</link><pubDate>{recent}</pubDate></item>
        </channel></rss>""".encode("utf-8")
        config = {"topics": {"news_queries": ["企业管理"], "recent_days": 7}}

        result = _fetch_recent_news_rss(_Client(xml), config)  # type: ignore[arg-type]

        self.assertEqual([x["title"] for x in result], ["企业项目管理新趋势"])

    def test_peer_topics_are_limited_to_focus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "_root": tmp,
                "_data_dir": tmp,
                "topics": {
                    "focus_keywords": ["企业", "管理", "项目", "组织"],
                    "peers": [
                        {"name": "同行", "topics": ["企业组织管理", "娱乐八卦"]}
                    ],
                },
            }
            Path(tmp, "peer_topics.json").write_text("[]", encoding="utf-8")

            result = load_peer_topics(config)

        self.assertEqual([x["topic"] for x in result], ["企业组织管理"])


if __name__ == "__main__":
    unittest.main()
