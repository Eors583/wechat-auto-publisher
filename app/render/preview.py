from __future__ import annotations

from html import escape

from lxml import html as lxml_html


_PREVIEW_DOCUMENT_STYLE = """
html, body {
  margin: 0;
  padding: 0;
  width: 100%;
  background: #fff;
  overflow: hidden;
}
body {
  overflow-wrap: break-word;
}
img {
  max-width: 100%;
}
""".strip()


def prepare_preview_document(value: str) -> str:
    """Build an isolated WeChat-like preview document.

    The draft snapshot remains responsible for element order, alignment,
    spacing and explicit dimensions.  Preview adaptation is deliberately
    limited to loading WeChat lazy images and constraining oversized images to
    the content viewport, matching the WeChat editor's responsive boundary.
    """
    if not value:
        return ""

    root = lxml_html.fragment_fromstring(value, create_parent="div")
    for image in root.iter("img"):
        source = str(image.get("src") or "").strip()
        lazy_source = str(image.get("data-src") or "").strip()
        if lazy_source and (
            not source or source.lower().startswith("data:image/gif")
        ):
            source = lazy_source
        if source.startswith("http://mmbiz.qpic.cn/"):
            source = "https://" + source[len("http://") :]
        if source:
            image.set("src", source)
        image.set("loading", "eager")
        image.set("decoding", "async")
        image.set("referrerpolicy", "no-referrer")

    content = "".join(
        lxml_html.tostring(child, encoding="unicode", method="html")
        for child in root
    )
    return (
        "<!doctype html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        f"<style>{_PREVIEW_DOCUMENT_STYLE}</style>"
        f"</head><body>{content}</body></html>"
    )


def prepare_preview_html(value: str) -> str:
    """Return a sandboxed iframe whose layout cannot be changed by app CSS."""
    document = prepare_preview_document(value)
    if not document:
        return ""
    return (
        '<iframe class="wechat-preview-iframe" '
        'title="微信公众号草稿模板预览" '
        'scrolling="no" '
        'sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" '
        'onload="this.style.height=Math.max(520,'
        "this.contentDocument.documentElement.scrollHeight)+'px'\" "
        f'srcdoc="{escape(document, quote=True)}"></iframe>'
    )
