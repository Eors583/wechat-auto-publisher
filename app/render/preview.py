from __future__ import annotations

import re
from difflib import SequenceMatcher
from html import escape
from typing import Any

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
.rewrite-diff-region {
  scroll-margin-block: 96px;
  transition: background-color .18s ease, outline-color .18s ease;
}
body[data-rewrite-side="before"] .rewrite-diff-active {
  background: #fff1f0 !important;
  outline: 3px solid #ff7875;
  outline-offset: 4px;
}
body[data-rewrite-side="after"] .rewrite-diff-active {
  background: #e6f4ff !important;
  outline: 3px solid #1677ff;
  outline-offset: 4px;
}
""".strip()


def _lookup_text(value: str) -> str:
    return re.sub(r"[^\w]+", "", str(value or ""), flags=re.UNICODE)


def _diff_anchor(value: str, start: int, end: int) -> str:
    boundaries = "\n。！？!?；;"
    left = max(value.rfind(mark, 0, start) for mark in boundaries) + 1
    right_candidates = [
        position for mark in boundaries if (position := value.find(mark, end)) >= 0
    ]
    right = min(right_candidates) + 1 if right_candidates else len(value)
    context = value[max(left, start - 28) : min(right, end + 28)]
    return _lookup_text(context)[:80]


def _text_units(value: str) -> list[tuple[int, int, str]]:
    units: list[tuple[int, int, str]] = []
    for match in re.finditer(r".+?(?:\r?\n+|[。！？!?；;]+|$)", value, re.DOTALL):
        key = _lookup_text(match.group())
        if key:
            units.append((match.start(), match.end(), key))
    return units


def _unit_range(
    units: list[tuple[int, int, str]],
    start: int,
    end: int,
    text_length: int,
) -> tuple[int, int]:
    offset = units[start][0] if start < len(units) else text_length
    finish = units[end - 1][1] if end > start else offset
    return offset, finish


def build_rewrite_regions(source: str, candidate: str) -> list[dict[str, str]]:
    """Return nearby text edits as navigable before/after regions."""

    before = str(source or "")
    after = str(candidate or "")
    before_units = _text_units(before)
    after_units = _text_units(after)
    matcher = SequenceMatcher(
        None,
        [unit[2] for unit in before_units],
        [unit[2] for unit in after_units],
        autojunk=False,
    )
    regions: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before_start, before_end = _unit_range(before_units, i1, i2, len(before))
        after_start, after_end = _unit_range(after_units, j1, j2, len(after))
        before_text = before[before_start:before_end]
        after_text = after[after_start:after_end]
        if _lookup_text(before_text) == _lookup_text(after_text):
            continue
        regions.append(
            {
                "before": before_text,
                "after": after_text,
                "before_anchor": _diff_anchor(before, before_start, before_end),
                "after_anchor": _diff_anchor(after, after_start, after_end),
            }
        )
    return regions


def rewrite_region_navigation_script(index: int) -> str:
    """Highlight one diff region in both previews and scroll the shared canvas."""

    return (
        "(() => {"
        "const wrap=document.querySelector('.ops-inline-comparison');"
        "if(!wrap){return;}"
        "const targets=[];"
        "wrap.querySelectorAll('iframe').forEach((frame)=>{"
        "const doc=frame.contentDocument;"
        "if(!doc){return;}"
        "doc.querySelectorAll('.rewrite-diff-active').forEach((item)=>"
        "item.classList.remove('rewrite-diff-active'));"
        f"const target=doc.querySelector('[data-rewrite-regions~=\"{int(index)}\"]');"
        "if(target){target.classList.add('rewrite-diff-active');targets.push([frame,target]);}"
        "});"
        "if(!targets.length){return;}"
        "const [frame,target]=targets[0];"
        "const wrapRect=wrap.getBoundingClientRect();"
        "const frameRect=frame.getBoundingClientRect();"
        "const targetRect=target.getBoundingClientRect();"
        "const top=wrap.scrollTop+frameRect.top-wrapRect.top+targetRect.top"
        "-(wrap.clientHeight-targetRect.height)/2;"
        "wrap.scrollTo({top:Math.max(0,top),behavior:'smooth'});"
        "})()"
    )


def _mark_rewrite_regions(
    root: Any,
    regions: list[dict[str, str]],
    side: str,
) -> None:
    anchor_key = f"{side}_anchor"
    elements = [
        element
        for element in root.iterdescendants()
        if str(getattr(element, "tag", "")).lower() not in {"script", "style", "svg"}
    ]
    for index, region in enumerate(regions):
        anchor = str(region.get(anchor_key) or "")
        if not anchor:
            continue
        matches = [
            element
            for element in elements
            if anchor in _lookup_text(element.text_content())
        ]
        if not matches:
            continue
        target = min(
            matches, key=lambda element: len(_lookup_text(element.text_content()))
        )
        existing = str(target.get("data-rewrite-regions") or "").strip()
        target.set(
            "data-rewrite-regions",
            " ".join(part for part in (existing, str(index)) if part),
        )
        classes = str(target.get("class") or "").split()
        if "rewrite-diff-region" not in classes:
            target.set("class", " ".join([*classes, "rewrite-diff-region"]))


def prepare_preview_document(
    value: str,
    *,
    rewrite_regions: list[dict[str, str]] | None = None,
    rewrite_side: str = "",
) -> str:
    """Build an isolated WeChat-like preview document.

    The draft snapshot remains responsible for element order, alignment,
    spacing and explicit dimensions.  Preview adaptation is deliberately
    limited to loading WeChat lazy images and constraining oversized images to
    the content viewport, matching the WeChat editor's responsive boundary.
    """
    if not value:
        return ""

    root = lxml_html.fragment_fromstring(value, create_parent="div")
    if rewrite_regions and rewrite_side in {"before", "after"}:
        _mark_rewrite_regions(root, rewrite_regions, rewrite_side)
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
        f'</head><body data-rewrite-side="{escape(rewrite_side)}">'
        f"{content}</body></html>"
    )


def prepare_preview_html(
    value: str,
    *,
    rewrite_regions: list[dict[str, str]] | None = None,
    rewrite_side: str = "",
) -> str:
    """Return a sandboxed iframe whose layout cannot be changed by app CSS."""
    document = prepare_preview_document(
        value,
        rewrite_regions=rewrite_regions,
        rewrite_side=rewrite_side,
    )
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
