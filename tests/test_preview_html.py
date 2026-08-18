from __future__ import annotations

from html import unescape

from app.render.preview import (
    build_rewrite_regions,
    prepare_preview_document,
    prepare_preview_html,
    rewrite_region_navigation_script,
)


def test_wechat_data_src_is_loaded_only_for_browser_preview() -> None:
    original = (
        '<section><img data-src="https://mmbiz.qpic.cn/a/640?wx_fmt=webp" '
        'alt="图片" style="width:578px !important"></section>'
    )
    document = prepare_preview_document(original)
    assert 'src="https://mmbiz.qpic.cn/a/640?wx_fmt=webp"' in document
    assert 'data-src="https://mmbiz.qpic.cn/a/640?wx_fmt=webp"' in document
    assert 'style="width:578px !important"' in document
    assert "margin-left:auto" not in document
    assert "<img src=" not in original


def test_preview_upgrades_wechat_image_to_https() -> None:
    document = prepare_preview_document(
        '<img src="http://mmbiz.qpic.cn/example/0?from=appmsg">'
    )
    assert 'src="https://mmbiz.qpic.cn/example/0?from=appmsg"' in document


def test_template_alignment_is_preserved_without_manual_image_positioning() -> None:
    document = prepare_preview_document(
        '<p style="text-align: center"><span><img '
        'data-src="https://mmbiz.qpic.cn/a" style="width: 578px"></span></p>'
    )
    assert 'style="text-align: center"' in document
    assert 'style="width: 578px"' in document
    assert "display:block" not in document
    assert "margin-left:auto" not in document
    assert "margin-right:auto" not in document


def test_preview_is_isolated_from_application_styles() -> None:
    preview = prepare_preview_html("<p>正文</p>")
    assert preview.startswith('<iframe class="wechat-preview-iframe"')
    assert 'sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"' in preview
    assert 'scrolling="no"' in preview
    assert "contentDocument.documentElement.scrollHeight" in preview
    assert "<style>" in unescape(preview)
    assert "img {" in unescape(preview)
    assert "max-width: 100%;" in unescape(preview)


def test_rewrite_regions_mark_both_styled_previews() -> None:
    source = "第一段保持原文。\n第二段保持原文。\n第三段保持原文。"
    candidate = "第一段保持原文。\n第二段改写为经营韧性建议。\n第三段保持原文。"
    source_html = "".join(f"<p>{line}</p>" for line in source.splitlines())
    candidate_html = "".join(f"<p>{line}</p>" for line in candidate.splitlines())

    regions = build_rewrite_regions(source, candidate)
    before = prepare_preview_document(
        source_html,
        rewrite_regions=regions,
        rewrite_side="before",
    )
    after = prepare_preview_document(
        candidate_html,
        rewrite_regions=regions,
        rewrite_side="after",
    )

    assert len(regions) == 1
    assert regions[0]["before"] == "保持原文"
    assert regions[0]["after"] == "改写为经营韧性建议"
    assert 'data-rewrite-regions="0"' in before
    assert 'data-rewrite-regions="0"' in after
    assert 'data-rewrite-side="before"' in before
    assert 'data-rewrite-side="after"' in after
    assert ".rewrite-diff-active" in before
    assert (
        '<p data-rewrite-regions="0" class="rewrite-diff-region">第二段保持原文。</p>'
        in before
    )

    script = rewrite_region_navigation_script(0)
    assert "data-rewrite-regions" in script
    assert "rewrite-diff-active" in script
    assert "wrap.scrollTo" in script


def test_inserted_text_is_highlighted_only_in_rewritten_preview() -> None:
    source = "第一段保持原文。\n第三段保持原文。"
    candidate = "第一段保持原文。\n新增经营建议。\n第三段保持原文。"
    source_html = "<p>第一段保持原文。</p><p>第三段保持原文。</p>"
    candidate_html = (
        "<p>第一段保持原文。</p><p>新增经营建议。</p><p>第三段保持原文。</p>"
    )

    regions = build_rewrite_regions(source, candidate)
    before = prepare_preview_document(
        source_html, rewrite_regions=regions, rewrite_side="before"
    )
    after = prepare_preview_document(
        candidate_html, rewrite_regions=regions, rewrite_side="after"
    )

    assert regions == [
        {
            "before": "",
            "after": "\n新增经营建议。",
            "before_anchor": "",
            "after_anchor": "新增经营建议",
        }
    ]
    assert 'data-rewrite-regions="0"' not in before
    assert (
        '<p data-rewrite-regions="0" class="rewrite-diff-region">新增经营建议。</p>'
        in after
    )


def test_deleted_text_is_highlighted_only_in_original_preview() -> None:
    source = "第一段保持原文。\n应删除的旧结论。\n第三段保持原文。"
    candidate = "第一段保持原文。\n第三段保持原文。"
    source_html = (
        "<p>第一段保持原文。</p><p>应删除的旧结论。</p><p>第三段保持原文。</p>"
    )
    candidate_html = "<p>第一段保持原文。</p><p>第三段保持原文。</p>"

    regions = build_rewrite_regions(source, candidate)
    before = prepare_preview_document(
        source_html, rewrite_regions=regions, rewrite_side="before"
    )
    after = prepare_preview_document(
        candidate_html, rewrite_regions=regions, rewrite_side="after"
    )

    assert regions[0]["before"] == "\n应删除的旧结论。"
    assert regions[0]["after"] == ""
    assert 'data-rewrite-regions="0"' in before
    assert 'data-rewrite-regions="0"' not in after
