from __future__ import annotations

import inspect

from app.ui.panels import tasks
from app.ui.styles import APP_CSS
from app.workflows import delivery


def test_secondary_preview_reuses_delivery_resolution(monkeypatch) -> None:
    selected = [{"title": "原广告标题", "thumb_url": "https://mmbiz.qpic.cn/a"}]
    synced = [{"title": "最新广告标题", "thumb_url": "https://mmbiz.qpic.cn/a"}]
    calls: dict[str, object] = {}

    def fake_select(client, layout, *, exclude_titles):
        calls["select"] = (client, layout, exclude_titles)
        return selected

    def fake_fetch(config, db):
        calls["fetch"] = (config, db)
        return object()

    def fake_sync(rows, record, **options):
        calls["sync"] = (rows, record, options)
        return synced

    monkeypatch.setattr(delivery, "select_secondary_articles", fake_select)
    monkeypatch.setattr(delivery, "fetch_latest_benchmark_record", fake_fetch)
    monkeypatch.setattr(delivery, "sync_secondary_titles", fake_sync)

    config = {
        "layout": {"enabled": True},
        "benchmark": {"enabled": True, "follow_source_order": True},
    }
    result = delivery.resolve_secondary_articles(
        "client", config, "db", {"selected_title": "主文章"}
    )

    assert result == synced
    assert calls["select"] == ("client", config["layout"], ["主文章"])
    assert calls["fetch"] == (config, "db")
    assert calls["sync"][0] == selected
    assert calls["sync"][2]["follow_source_order"] is True


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
