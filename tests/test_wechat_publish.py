from __future__ import annotations

from app.wechat.publish import build_article_from_job


def test_build_article_does_not_expose_ingestion_source_url() -> None:
    article = build_article_from_job(
        {
            "selected_title": "测试标题",
            "html_content": "<p>测试正文</p>",
            "thumb_media_id": "thumb-1",
            "digest": "摘要",
            "source_url": "https://example.com/original-article",
        }
    )
    assert article["content_source_url"] == ""
