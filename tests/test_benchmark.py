from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.benchmark import (
    BenchmarkArticle,
    BenchmarkRecord,
    benchmark_record_is_fresh,
    fetch_latest_benchmark_record,
    parse_admin_publish_response,
    sync_secondary_titles,
)
from app.layout.composer import is_ad_titled, parse_ad_number, strip_ad_title_prefix


class BenchmarkTests(unittest.TestCase):
    def test_live_official_record_wins_over_fresh_cache(self) -> None:
        now = datetime.now(timezone.utc)
        live = BenchmarkRecord(
            published_at=int((now - timedelta(minutes=5)).timestamp()),
            articles=[BenchmarkArticle("刚刚发布")],
            source="official_freepublish",
        )
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "benchmark.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "published_at": int(
                            (now - timedelta(hours=2)).timestamp()
                        ),
                        "source": "cache",
                        "articles": [{"title": "两小时前缓存"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            config = {
                "benchmark": {
                    "enabled": True,
                    "cache_path": str(cache_path),
                    "official_fallback_enabled": True,
                    "official_max_age_hours": 36,
                    "app_id": "wx-live",
                    "app_secret": "secret",
                }
            }
            with (
                patch("app.benchmark.build_wechat_client", return_value=object()),
                patch("app.benchmark.fetch_official_publish_record", return_value=live),
            ):
                result = fetch_latest_benchmark_record(config, object())

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.articles[0].title, "刚刚发布")

    def test_stale_publish_record_is_not_current_advertising(self) -> None:
        now = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
        recent = BenchmarkRecord(
            published_at=int((now - timedelta(hours=2)).timestamp()),
            articles=[BenchmarkArticle("最新文章")],
            source="test",
        )
        stale = BenchmarkRecord(
            published_at=int((now - timedelta(days=12)).timestamp()),
            articles=[BenchmarkArticle("十二天前文章")],
            source="test",
        )

        self.assertTrue(
            benchmark_record_is_fresh(recent, max_age_hours=36, now=now)
        )
        self.assertFalse(
            benchmark_record_is_fresh(stale, max_age_hours=36, now=now)
        )

    def test_ad_marker_can_appear_inside_title(self) -> None:
        title = "课程广告4：华为如何洞察客户需求？"
        self.assertTrue(is_ad_titled(title))
        self.assertEqual(parse_ad_number(title), 4)
        self.assertEqual(strip_ad_title_prefix(title), "课程华为如何洞察客户需求？")

    def test_parse_nested_admin_publish_response(self) -> None:
        info = {
            "send_time": 1783998000,
            "appmsg_info": json.dumps(
                {
                    "item_list": [
                        {"title": "主稿", "cover": "main.jpg"},
                        {"title": "广告甲", "cover": "a.jpg"},
                    ]
                },
                ensure_ascii=False,
            ),
        }
        payload = {
            "publish_page": json.dumps(
                {"publish_list": [{"publish_info": json.dumps(info, ensure_ascii=False)}]},
                ensure_ascii=False,
            )
        }

        record = parse_admin_publish_response(payload)

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.published_at, 1783998000)
        self.assertEqual([x.title for x in record.articles], ["主稿", "广告甲"])
        self.assertEqual(record.articles[1].cover_url, "a.jpg")

    def test_titles_follow_images_not_order(self) -> None:
        record = BenchmarkRecord(
            published_at=1783998000,
            source="test",
            articles=[
                BenchmarkArticle("主稿", "main"),
                BenchmarkArticle("甲标题", "source-a"),
                BenchmarkArticle("乙标题", "source-b"),
            ],
        )
        secondaries = [
            {"title": "旧标题1", "thumb_url": "target-b"},
            {"title": "旧标题2", "thumb_url": "target-a"},
            {"title": "无匹配", "thumb_url": "target-x"},
        ]
        # source-a=0xAA, source-b=0x55；目标顺序故意相反。
        with patch(
            "app.benchmark._download_hashes",
            side_effect=[[0xAA, 0x55], [0x55, 0xAA, 0xFFFF]],
        ):
            matched = sync_secondary_titles(
                secondaries, record, threshold=0.95, matched_only=True
            )

        self.assertEqual([x["title"] for x in matched], ["甲标题", "乙标题"])
        self.assertTrue(all(x["_benchmark_image_score"] == 1.0 for x in matched))

    def test_unmatched_ad_keeps_original_title(self) -> None:
        record = BenchmarkRecord(
            published_at=1783998000,
            source="test",
            articles=[BenchmarkArticle("主稿"), BenchmarkArticle("新标题", "source")],
        )
        secondaries = [
            {"title": "原标题", "thumb_url": "matched"},
            {"title": "没有匹配时沿用", "thumb_url": "unmatched"},
        ]
        with patch(
            "app.benchmark._download_hashes",
            side_effect=[[0xAA], [0xAA, 0xFFFF]],
        ):
            result = sync_secondary_titles(secondaries, record, threshold=0.95)

        self.assertEqual([x["title"] for x in result], ["新标题", "没有匹配时沿用"])

    def test_duplicate_target_images_are_removed(self) -> None:
        record = BenchmarkRecord(
            published_at=1783998000,
            source="test",
            articles=[BenchmarkArticle("主稿"), BenchmarkArticle("新标题", "source")],
        )
        secondaries = [
            {"title": "广告1", "thumb_url": "same", "_ad_number": 1},
            {"title": "广告2", "thumb_url": "same-copy", "_ad_number": 2},
        ]
        with patch(
            "app.benchmark._download_hashes",
            side_effect=[[0xAA], [0xAA, 0xAA]],
        ):
            result = sync_secondary_titles(secondaries, record, threshold=0.95)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "新标题")


if __name__ == "__main__":
    unittest.main()
