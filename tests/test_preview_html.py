from __future__ import annotations

from html import unescape

from app.render.preview import prepare_preview_document, prepare_preview_html


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


def test_preview_removes_scripts_without_relaxing_the_iframe_sandbox() -> None:
    preview = prepare_preview_html(
        '<p>正文</p><script>window.top.alert("unsafe")</script><p>结尾</p>'
    )
    document = unescape(preview)

    assert "<script" not in document
    assert "正文" in document
    assert "结尾" in document
    assert "allow-scripts" not in preview


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
