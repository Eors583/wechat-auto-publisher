from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx
from lxml import html as lxml_html

from app.layout_profiles import normalize_layout, validate_layout


_WECHAT_HOST = "mp.weixin.qq.com"
_DIMENSION = re.compile(r"^(?:0|\d+(?:\.\d+)?(?:px|em|rem|%))$")
_LINE_HEIGHT = re.compile(r"^\d+(?:\.\d+)?(?:px|em|rem|%)?$")
_COLOR = re.compile(
    r"^(?:#[0-9a-f]{3,8}|rgba?\([^)]{3,80}\)|hsla?\([^)]{3,80}\)|transparent)$",
    re.I,
)
_NAMED_COLORS = {
    "black": "#000000",
    "white": "#ffffff",
    "gray": "#808080",
    "grey": "#808080",
    "red": "#ff0000",
    "blue": "#0000ff",
    "green": "#008000",
}
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    "Referer": "https://mp.weixin.qq.com/",
}


@dataclass(frozen=True)
class WeChatLayoutImport:
    source_url: str
    title: str
    account_name: str
    content_html: str
    preview_html: str
    layout: dict[str, Any]
    diagnostics: dict[str, int]
    captured_at: str


def fetch_wechat_article_layout(
    url: str,
    *,
    current_layout: dict[str, Any] | None = None,
    cookie: str = "",
    timeout: float = 20.0,
) -> WeChatLayoutImport:
    """Fetch one public WeChat article and derive reusable account layout rules."""

    clean_url = _validate_wechat_article_url(url)
    headers = dict(_BROWSER_HEADERS)
    if str(cookie or "").strip():
        headers["Cookie"] = str(cookie).strip()
    with httpx.Client(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        response = client.get(clean_url)
        response.raise_for_status()
        final_url = str(response.url)
        page = str(response.text or "")
    if len(page) < 500:
        raise ValueError("微信返回了空页面，请确认文章链接可以公开访问")
    _raise_for_wechat_access_page(page, final_url=final_url)
    return parse_wechat_article_layout(
        page,
        # 微信可能为短链接补充查询参数或 hash；结果仍归属用户输入的
        # 公开文章地址，避免把安全验证页误报成“链接格式错误”。
        source_url=clean_url,
        current_layout=current_layout,
    )


def parse_wechat_article_layout(
    page: str,
    *,
    source_url: str,
    current_layout: dict[str, Any] | None = None,
) -> WeChatLayoutImport:
    """Parse a downloaded WeChat page while preserving its inline layout."""

    _validate_wechat_article_url(source_url)
    try:
        document = lxml_html.fromstring(page or "")
    except (TypeError, ValueError) as exc:
        raise ValueError("微信文章 HTML 无法解析") from exc

    matches = document.xpath('//*[@id="js_content"]')
    if not matches:
        matches = document.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), '
            '" rich_media_content ")]'
        )
    if not matches:
        raise ValueError("没有读取到微信正文 #js_content，可能被登录态或访问限制拦截")

    content = deepcopy(matches[0])
    plain_text = _plain_text(content)
    if len(plain_text) < 20:
        raise ValueError("微信正文为空，请检查文章是否已删除、受限或需要重新登录")
    _sanitize_preserved_content(content)

    title = _metadata_content(document, "og:title") or _node_text(
        document.xpath('//*[@id="activity-name"]')
    )
    account_name = _node_text(document.xpath('//*[@id="js_name"]')) or _node_text(
        document.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), '
            '" profile_nickname ")]'
        )
    )
    layout, diagnostics = _extract_supported_layout(content, current_layout)
    content_html = lxml_html.tostring(content, encoding="unicode", method="html")
    preview_html = build_wechat_layout_preview(content_html)
    return WeChatLayoutImport(
        source_url=source_url,
        title=title or "未读取到文章标题",
        account_name=account_name or "未读取到公众号名称",
        content_html=content_html,
        preview_html=preview_html,
        layout=layout,
        diagnostics=diagnostics,
        captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def build_wechat_layout_preview(content_html: str) -> str:
    """Return an isolated, WeChat-sized preview that cannot affect app CSS."""

    document = (
        "<!doctype html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        "<style>"
        "html,body{margin:0;padding:0;background:#fff;}"
        "body{overflow-wrap:break-word;}"
        ".rich_media_content{max-width:677px;margin:0 auto;padding:0 16px;"
        "font-size:17px;line-height:1.75;color:#333;word-wrap:break-word;"
        "box-sizing:border-box;visibility:visible!important;opacity:1!important;}"
        "img{max-width:100%!important;height:auto!important;}"
        "</style></head><body>"
        f"{content_html}</body></html>"
    )
    return (
        '<iframe class="wechat-layout-import-preview" '
        'title="微信公众号原文排版预览" scrolling="yes" '
        'sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" '
        f'srcdoc="{escape(document, quote=True)}"></iframe>'
    )


def _validate_wechat_article_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlsplit(value)
    host = str(parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or host != _WECHAT_HOST:
        raise ValueError("请输入 mp.weixin.qq.com 的微信公众号文章链接")
    if not (parsed.path == "/s" or parsed.path.startswith("/s/")):
        raise ValueError("该链接不是微信公众号公开文章地址")
    return value


def _raise_for_wechat_access_page(page: str, *, final_url: str) -> None:
    """Turn WeChat's HTTP-200 guard pages into actionable product errors."""

    parsed = urlsplit(str(final_url or ""))
    path = str(parsed.path or "")
    lowered = str(page or "").casefold()
    has_content = 'id="js_content"' in lowered or "rich_media_content" in lowered
    if has_content:
        return

    if "appmsgcaptcha" in path or any(
        marker in lowered
        for marker in (
            "wappoc_appmsgcaptcha",
            "captcha",
            "环境异常",
            "访问过于频繁",
            "操作频繁",
        )
    ):
        raise ValueError(
            "微信将请求转到了安全验证页。请更新公众号后台登录态后重试，"
            "也可以换一篇能够在无痕窗口直接打开的公开文章"
        )

    if any(
        marker in lowered
        for marker in (
            "此内容因违规无法查看",
            "已停止访问该网页",
            "内容已被发布者删除",
            "该内容已被发布者删除",
        )
    ):
        raise ValueError("这篇微信文章已删除、停用或不可公开访问，无法提取排版")

    raise ValueError(
        "微信返回了不含正文的页面。请确认链接可在无痕窗口直接打开；"
        "如果文章受限，请更新公众号后台登录态后重试"
    )


def _sanitize_preserved_content(content: Any) -> None:
    for node in list(content.xpath(".//script|.//iframe|.//object|.//embed|.//form")):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    for node in content.iter():
        for name in list(node.attrib):
            lowered = str(name).casefold()
            value = str(node.attrib.get(name) or "").strip()
            if lowered.startswith("on"):
                del node.attrib[name]
                continue
            if lowered in {"href", "src", "data-src"} and value.casefold().startswith(
                ("javascript:", "vbscript:")
            ):
                del node.attrib[name]
                continue
            if lowered == "style" and re.search(
                r"(?:expression\s*\(|javascript\s*:|vbscript\s*:)",
                value,
                flags=re.I,
            ):
                del node.attrib[name]

    for image in content.iter("img"):
        source = str(image.get("data-src") or image.get("src") or "").strip()
        if source.startswith("http://mmbiz.qpic.cn/"):
            source = "https://" + source[len("http://") :]
        if source:
            image.set("src", source)
        if image.get("data-w") and not image.get("width"):
            image.set("width", str(image.get("data-w")))
        image.set("loading", "eager")
        image.set("decoding", "async")
        image.set("referrerpolicy", "no-referrer")


def _extract_supported_layout(
    content: Any,
    current_layout: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, int]]:
    layout = normalize_layout(current_layout or {})
    paragraphs = [
        node
        for node in content.xpath(".//p")
        if len(_plain_text(node)) >= 8 and not node.xpath("ancestor::blockquote")
    ]
    if not paragraphs:
        paragraphs = [
            node
            for node in content.xpath(".//section|.//div")
            if 12 <= len(_plain_text(node)) <= 500
        ]
    body_styles = [
        (_effective_text_style(node, content), len(_plain_text(node)))
        for node in paragraphs
    ]
    _apply_typography(layout["body"], body_styles)
    _apply_spacing(layout["body"], body_styles, "spacing_after")
    _apply_first_indent(layout["body"], body_styles)
    _apply_horizontal_padding(layout["body"], content, body_styles)

    heading_nodes = _heading_candidates(content, body_styles)
    title_nodes = heading_nodes[:1]
    argument_nodes = heading_nodes[1:] or heading_nodes
    if title_nodes:
        title_styles = [
            (_effective_text_style(node, content), max(1, len(_plain_text(node))))
            for node in title_nodes
        ]
        _apply_typography(layout["title"], title_styles)
        _apply_heading_spacing(layout["title"], title_styles)
        layout["title"]["bold"] = _is_bold(title_styles[0][0])
    if argument_nodes:
        argument_styles = [
            (_effective_text_style(node, content), max(1, len(_plain_text(node))))
            for node in argument_nodes[:12]
        ]
        _apply_typography(layout["argument"], argument_styles)
        _apply_heading_spacing(layout["argument"], argument_styles)
        layout["argument"]["bold"] = _weighted_bool(argument_styles, _is_bold)
        _apply_background_and_border(layout["argument"], argument_styles)

    quote_nodes = content.xpath(".//blockquote") or [
        node
        for node in content.xpath(".//section|.//div")
        if _looks_like_quote(_effective_style(node, content))
        and 4 <= len(_plain_text(node)) <= 500
    ]
    if quote_nodes:
        quote_styles = [
            (_effective_text_style(node, content), max(1, len(_plain_text(node))))
            for node in quote_nodes[:10]
        ]
        _apply_typography(layout["quote"], quote_styles)
        _apply_heading_spacing(layout["quote"], quote_styles)
        _apply_background_and_border(layout["quote"], quote_styles)

    list_nodes = content.xpath(".//li")
    if list_nodes:
        list_styles = [
            (_effective_text_style(node, content), max(1, len(_plain_text(node))))
            for node in list_nodes[:20]
        ]
        _apply_typography(layout["list"], list_styles)
        _apply_spacing(layout["list"], list_styles, "spacing_after")
        marker = _common_non_body_color(list_styles, str(layout["body"]["color"]))
        if marker:
            layout["list"]["marker_color"] = marker

    section_depth = max(
        (
            1 + len(node.xpath("ancestor::section"))
            for node in content.xpath(".//section")
        ),
        default=0,
    )
    diagnostics = {
        "element_count": sum(1 for _ in content.iter()),
        "inline_style_count": len(content.xpath('.//*[@style]')),
        "image_count": len(content.xpath(".//img")),
        "section_depth": section_depth,
        "body_sample_count": len(body_styles),
        "heading_sample_count": len(heading_nodes),
    }
    return validate_layout(layout), diagnostics


def _apply_typography(
    target: dict[str, Any],
    samples: list[tuple[dict[str, str], int]],
) -> None:
    mappings = {
        "font-size": ("font_size", _valid_dimension),
        "color": ("color", _valid_color),
        "line-height": ("line_height", _valid_line_height),
        "text-align": ("alignment", lambda value: value if value in {"left", "center", "right", "justify"} else ""),
    }
    for css_key, (layout_key, normalizer) in mappings.items():
        value = _weighted_style(samples, css_key, normalizer)
        if value:
            target[layout_key] = value


def _apply_spacing(
    target: dict[str, Any],
    samples: list[tuple[dict[str, str], int]],
    layout_key: str,
) -> None:
    value = _weighted_style(samples, "margin-bottom", _valid_dimension)
    if not value:
        value = _weighted_style(samples, "margin", _margin_bottom)
    if value:
        target[layout_key] = value


def _apply_heading_spacing(
    target: dict[str, Any],
    samples: list[tuple[dict[str, str], int]],
) -> None:
    before = _weighted_style(samples, "margin-top", _valid_dimension)
    after = _weighted_style(samples, "margin-bottom", _valid_dimension)
    margin_before = _weighted_style(samples, "margin", _margin_top)
    margin_after = _weighted_style(samples, "margin", _margin_bottom)
    if before or margin_before:
        target["spacing_before"] = before or margin_before
    if after or margin_after:
        target["spacing_after"] = after or margin_after


def _apply_first_indent(
    target: dict[str, Any], samples: list[tuple[dict[str, str], int]]
) -> None:
    value = _weighted_style(samples, "text-indent", _valid_dimension)
    if value:
        target["first_line_indent"] = value


def _apply_horizontal_padding(
    target: dict[str, Any],
    content: Any,
    samples: list[tuple[dict[str, str], int]],
) -> None:
    content_style = _parse_style(str(content.get("style") or ""))
    value = _valid_dimension(content_style.get("padding-left", ""))
    if not value:
        value = _padding_left(content_style.get("padding", ""))
    if not value:
        value = _weighted_style(samples, "padding-left", _valid_dimension)
    if value:
        target["horizontal_padding"] = value


def _apply_background_and_border(
    target: dict[str, Any], samples: list[tuple[dict[str, str], int]]
) -> None:
    background = _weighted_style(samples, "background-color", _valid_color)
    if not background:
        background = _weighted_style(samples, "background", _background_color)
    border = _weighted_style(samples, "border-left-color", _valid_color)
    if not border:
        border = _weighted_style(samples, "border-left", _border_color)
    if background:
        target["background"] = background
    if border:
        target["border_color"] = border


def _heading_candidates(
    content: Any, body_styles: list[tuple[dict[str, str], int]]
) -> list[Any]:
    explicit = content.xpath(".//h1|.//h2|.//h3|.//h4")
    candidates = list(explicit)
    body_size = _numeric_px(_weighted_style(body_styles, "font-size", _valid_dimension))
    for node in content.xpath(".//p|.//section"):
        text = _plain_text(node)
        if not 2 <= len(text) <= 80 or node in candidates:
            continue
        style = _effective_text_style(node, content)
        size = _numeric_px(style.get("font-size", ""))
        if _is_bold(style) and (not body_size or not size or size >= body_size):
            candidates.append(node)
    candidates.sort(
        key=lambda node: (
            -_numeric_px(
                _effective_text_style(node, content).get("font-size", "")
            ),
            len(_plain_text(node)),
        )
    )
    return candidates[:16]


def _effective_style(node: Any, boundary: Any) -> dict[str, str]:
    chain: list[Any] = []
    current = node
    while current is not None:
        chain.append(current)
        if current is boundary:
            break
        current = current.getparent()
    result: dict[str, str] = {}
    for item in reversed(chain):
        result.update(_parse_style(str(item.get("style") or "")))
        legacy_color = _valid_color(str(item.get("color") or ""))
        if legacy_color:
            result["color"] = legacy_color
        if str(item.tag or "").casefold() in {"b", "strong"}:
            result["font-weight"] = "bold"
        text_fill = _valid_color(result.get("-webkit-text-fill-color", ""))
        if text_fill and text_fill != "transparent":
            result["color"] = text_fill
    return result


def _effective_text_style(node: Any, boundary: Any) -> dict[str, str]:
    """Resolve typography that a WeChat editor stores on inner text spans."""

    result = _effective_style(node, boundary)
    weights: dict[str, Counter[str]] = {
        key: Counter()
        for key in (
            "font-size",
            "color",
            "line-height",
            "font-weight",
            "text-align",
        )
    }
    total_text_weight = 0
    for item in node.iter():
        direct_text = re.sub(r"\s+", " ", str(item.text or "")).strip()
        if direct_text:
            total_text_weight += len(direct_text)
            style = _effective_style(item, boundary)
            for key, counter in weights.items():
                value = str(style.get(key) or "").strip()
                if value:
                    counter[value] += len(direct_text)
        tail_text = re.sub(r"\s+", " ", str(item.tail or "")).strip()
        parent = item.getparent()
        if tail_text and parent is not None and parent is not node.getparent():
            total_text_weight += len(tail_text)
            style = _effective_style(parent, boundary)
            for key, counter in weights.items():
                value = str(style.get(key) or "").strip()
                if value:
                    counter[value] += len(tail_text)
    for key, counter in weights.items():
        # Do not classify a large section as bold or colored merely because it
        # contains one small accented child.  A typographic override must
        # describe at least half of the container's actual text.
        if counter and sum(counter.values()) * 2 >= max(1, total_text_weight):
            result[key] = counter.most_common(1)[0][0]
    return result


def _parse_style(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for declaration in _split_declarations(value):
        if ":" not in declaration:
            continue
        key, raw = declaration.split(":", 1)
        clean_key = key.strip().casefold()
        clean_value = re.sub(r"\s*!important\s*$", "", raw.strip(), flags=re.I)
        if clean_key and clean_value:
            result[clean_key] = clean_value
    return result


def _split_declarations(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    for char in value or "":
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth = max(0, depth - 1)
            current.append(char)
        elif char == ";" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _weighted_style(
    samples: Iterable[tuple[dict[str, str], int]],
    key: str,
    normalizer: Any,
) -> str:
    weights: Counter[str] = Counter()
    for style, weight in samples:
        value = normalizer(str(style.get(key) or "").strip())
        if value:
            weights[value] += max(1, int(weight))
    return weights.most_common(1)[0][0] if weights else ""


def _weighted_bool(
    samples: Iterable[tuple[dict[str, str], int]], predicate: Any
) -> bool:
    yes = no = 0
    for style, weight in samples:
        if predicate(style):
            yes += max(1, int(weight))
        else:
            no += max(1, int(weight))
    return yes >= no


def _valid_dimension(value: str) -> str:
    clean = str(value or "").strip().casefold()
    return clean if _DIMENSION.fullmatch(clean) else ""


def _valid_line_height(value: str) -> str:
    clean = str(value or "").strip().casefold()
    return clean if _LINE_HEIGHT.fullmatch(clean) else ""


def _valid_color(value: str) -> str:
    clean = re.sub(r"\s+", "", str(value or "").strip().casefold())
    clean = _NAMED_COLORS.get(clean, clean)
    return clean if _COLOR.fullmatch(clean) else ""


def _margin_values(value: str) -> tuple[str, str, str, str]:
    parts = [part for part in str(value or "").strip().split() if _valid_dimension(part)]
    if len(parts) == 1:
        return (parts[0],) * 4
    if len(parts) == 2:
        return parts[0], parts[1], parts[0], parts[1]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2], parts[1]
    if len(parts) >= 4:
        return parts[0], parts[1], parts[2], parts[3]
    return "", "", "", ""


def _margin_top(value: str) -> str:
    return _margin_values(value)[0]


def _margin_bottom(value: str) -> str:
    return _margin_values(value)[2]


def _padding_left(value: str) -> str:
    return _margin_values(value)[3]


def _background_color(value: str) -> str:
    for token in reversed(str(value or "").split()):
        color = _valid_color(token)
        if color:
            return color
    return ""


def _border_color(value: str) -> str:
    for token in reversed(str(value or "").split()):
        color = _valid_color(token)
        if color:
            return color
    return ""


def _is_bold(style: dict[str, str]) -> bool:
    value = str(style.get("font-weight") or "").strip().casefold()
    if value in {"bold", "bolder"}:
        return True
    try:
        return int(value) >= 600
    except ValueError:
        return False


def _looks_like_quote(style: dict[str, str]) -> bool:
    return bool(
        _valid_color(style.get("background-color", ""))
        or _border_color(style.get("border-left", ""))
        or _valid_color(style.get("border-left-color", ""))
    )


def _common_non_body_color(
    samples: list[tuple[dict[str, str], int]], body_color: str
) -> str:
    weights: Counter[str] = Counter()
    for style, weight in samples:
        color = _valid_color(style.get("color", ""))
        if color and color != body_color:
            weights[color] += max(1, weight)
    return weights.most_common(1)[0][0] if weights else ""


def _numeric_px(value: str) -> float:
    match = re.match(r"^(\d+(?:\.\d+)?)px$", str(value or "").strip(), flags=re.I)
    return float(match.group(1)) if match else 0.0


def _plain_text(node: Any) -> str:
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _node_text(nodes: list[Any]) -> str:
    return _plain_text(nodes[0]) if nodes else ""


def _metadata_content(document: Any, name: str) -> str:
    values = document.xpath(
        "//meta[translate(@property, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz')=$name]/@content",
        name=name.casefold(),
    )
    return str(values[0]).strip() if values else ""


__all__ = [
    "WeChatLayoutImport",
    "build_wechat_layout_preview",
    "fetch_wechat_article_layout",
    "parse_wechat_article_layout",
]
