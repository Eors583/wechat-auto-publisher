from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lxml import html as lxml_html

from app.wechat.template_snapshot import (
    TemplateSnapshot,
    load_template_snapshot,
    merge_template_html,
)


@dataclass
class HtmlQualityReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    paragraph_count: int = 0
    image_count: int = 0
    long_paragraph_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.errors:
            return "；".join(self.errors)
        if self.warnings:
            return "；".join(self.warnings)
        return f"排版检查通过：{self.paragraph_count} 个段落，{self.image_count} 张图片"


@dataclass
class FinalizedArticle:
    html: str
    snapshot: TemplateSnapshot | None
    report: HtmlQualityReport


def finalize_article_html(
    generated_html: str,
    editor_template_config: dict[str, Any] | None = None,
    *,
    snapshot: TemplateSnapshot | None = None,
    load_local_snapshot: bool = True,
) -> FinalizedArticle:
    """Use the exact same merge, compatibility and quality path for preview and drafts."""
    editor = dict(editor_template_config or {})
    if snapshot is None and load_local_snapshot and editor.get("enabled", False):
        snapshot = load_template_snapshot(editor)
    # Normalize only generated article markup. The selected WeChat draft template
    # must remain intact, including its native video/card components and styling.
    html = normalize_wechat_html(generated_html)
    if snapshot is not None:
        html = merge_template_html(
            snapshot.content,
            html,
            str(editor.get("placeholder") or "蓝血经营管理系统正文"),
        )
    report = inspect_wechat_html(
        html,
        placeholder=str(editor.get("placeholder") or "蓝血经营管理系统正文"),
        require_responsive_images=snapshot is None,
    )
    return FinalizedArticle(html=html, snapshot=snapshot, report=report)


def normalize_wechat_html(value: str) -> str:
    """Make images and tables responsive without flattening WeChat template components."""
    root = lxml_html.fragment_fromstring(value or "", create_parent="div")
    for image in root.iter("img"):
        style = str(image.get("style") or "").rstrip("; ")
        responsive = (
            "max-width:100% !important;height:auto !important;"
            "box-sizing:border-box"
        )
        image.set("style", f"{style};{responsive}" if style else responsive)
    for table in root.iter("table"):
        style = str(table.get("style") or "").rstrip("; ")
        responsive = "width:100% !important;max-width:100% !important;table-layout:fixed"
        table.set("style", f"{style};{responsive}" if style else responsive)
    return "".join(
        lxml_html.tostring(child, encoding="unicode", method="html") for child in root
    )


def inspect_wechat_html(
    value: str,
    *,
    placeholder: str = "",
    require_responsive_images: bool = True,
) -> HtmlQualityReport:
    report = HtmlQualityReport()
    try:
        root = lxml_html.fragment_fromstring(value or "", create_parent="div")
    except (ValueError, TypeError) as exc:
        report.errors.append(f"HTML 结构无法解析：{exc}")
        return report

    report.paragraph_count = len(root.xpath(".//p"))
    images = list(root.iter("img"))
    report.image_count = len(images)
    plain_text = "\n".join("".join(node.itertext()) for node in root.xpath(".//p|.//blockquote|.//td|.//th"))

    if placeholder:
        normalized_placeholder = re.sub(r"\s+", "", placeholder)
        for element in root.iterdescendants():
            text = re.sub(r"\s+", "", "".join(element.itertext()))
            decoration_free = re.sub(r"[▼▽▾▿▲△▴▵↓↑→←·•—\-]+", "", text)
            if text == normalized_placeholder or decoration_free == normalized_placeholder:
                report.errors.append("最终 HTML 仍残留正文占位符")
                break
    if root.xpath(".//script"):
        report.errors.append("HTML 含有不安全的脚本标签")
    for element in root.iterdescendants():
        style = str(element.get("style") or "").lower().replace(" ", "")
        if "position:fixed" in style:
            report.errors.append("HTML 含有移动端不兼容的 position:fixed 样式")
            break
        if any(str(name).lower().startswith("on") for name in element.attrib):
            report.errors.append("HTML 含有不安全的事件属性")
            break

    for index, image in enumerate(images, 1):
        source = str(image.get("src") or image.get("data-src") or "").strip()
        if not source:
            report.errors.append(f"第 {index} 张图片缺少 src/data-src")
        if source.lower().startswith("javascript:"):
            report.errors.append(f"第 {index} 张图片地址不安全")
        style = str(image.get("style") or "").lower().replace(" ", "")
        if require_responsive_images and (
            "max-width:100%!important" not in style
            or "height:auto!important" not in style
        ):
            report.errors.append(f"第 {index} 张图片未设置移动端自适应")

    markdown_patterns = (
        r"\*\*[^*\n]+\*\*",
        r"(?m)^\s*#{1,6}\s+",
        r"\[[^\]\n]+\]\([^)\n]+\)",
        r"(?m)^\s*\|?\s*:?-{3,}:?\s*\|",
    )
    if any(re.search(pattern, plain_text) for pattern in markdown_patterns):
        report.errors.append("正文仍残留未渲染的 Markdown 符号")

    for paragraph in root.xpath(".//p"):
        length = len(re.sub(r"\s+", "", "".join(paragraph.itertext())))
        if length > 600:
            report.long_paragraph_count += 1
    if report.long_paragraph_count:
        report.warnings.append(
            f"发现 {report.long_paragraph_count} 个超过 600 字的长段落，建议人工检查换行"
        )
    if not report.paragraph_count:
        report.errors.append("最终 HTML 没有正文段落")
    return report
