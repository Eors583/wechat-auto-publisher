from __future__ import annotations

import inspect

from app.ui.panels import tasks
from app.ui.styles import APP_CSS
from app.workflows import delivery


def test_secondary_preview_reuses_delivery_resolution(monkeypatch) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setattr(
        delivery,
        "article_from_news_item",
        lambda payload: dict(payload),
    )

    config = {
        "layout": {"enabled": True},
        "benchmark": {"enabled": True, "follow_source_order": True},
    }
    record = __import__("app.benchmark", fromlist=["BenchmarkRecord"])
    calls["record"] = record.BenchmarkRecord(
        published_at=300,
        source="official_freepublish",
        articles=[
            record.BenchmarkArticle("最新头条", payload={"title": "最新头条"}),
            record.BenchmarkArticle(
                "广告1：最新广告标题",
                payload={
                    "title": "广告1：最新广告标题",
                    "thumb_media_id": "cover-a",
                    "content": "广告正文",
                },
            ),
        ],
    )
    monkeypatch.setattr(
        delivery,
        "fetch_latest_benchmark_record",
        lambda config, db: calls["record"],
    )

    result = delivery.resolve_secondary_articles(
        "client", config, "db", {"selected_title": "主文章"}
    )

    assert [item["title"] for item in result] == ["最新广告标题"]
    assert result[0]["content"] == "广告正文"
    assert result[0]["_benchmark_source"] == "official_freepublish"


def test_delivery_and_review_use_the_same_secondary_resolver() -> None:
    delivery_source = inspect.getsource(delivery.DeliverySteps._secondary_articles)
    service_source = inspect.getsource(
        __import__("app.services.batches", fromlist=["BatchService"]).BatchService._preview_secondary_articles
    )

    assert "resolve_secondary_articles(" in delivery_source
    assert "resolve_secondary_articles(" in service_source


def test_review_page_renders_responsive_secondary_article_preview() -> None:
    source = inspect.getsource(tasks.build_review_page)

    assert 'secondary_preview_tab = ui.tab("广告栏预览")' in source
    assert "with ui.tab_panel(secondary_preview_tab)" in source
    preview_panel = source.index("with ui.tab_panel(preview_tab)")
    secondary_panel = source.index("with ui.tab_panel(secondary_preview_tab)")
    edit_panel = source.index("with ui.tab_panel(edit_tab)")
    assert preview_panel < secondary_panel < edit_panel
    assert 'ui.label("广告栏预览")' in source
    assert "service._preview_secondary_articles(batch_id, job_id)" in source
    assert "wechat_image_proxy_url(thumb_url)" in source
    assert "ops-secondary-preview-title" in source
    assert ".ops-secondary-preview-row" in APP_CSS
    assert "minmax(0, 1fr)" in APP_CSS
    assert "overflow-wrap: anywhere" in APP_CSS
    assert "grid-template-columns: repeat(5, minmax(0, 1fr));" in APP_CSS
    assert (
        ".ops-review-document-panels .ops-review-mode-panel { "
        "padding: 0 !important; overflow: auto;"
    ) in APP_CSS
