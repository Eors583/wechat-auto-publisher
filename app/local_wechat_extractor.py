from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener

MAX_ARTICLE_BYTES = 8 * 1024 * 1024
MIN_ARTICLE_CHARS = 80
MAX_ARTICLE_IMAGES = 24
WECHAT_HOST = "mp.weixin.qq.com"
BLOCK_PAGE_MARKERS = (
    "环境异常",
    "访问过于频繁",
    "请完成验证",
    "当前环境存在异常",
)


class WeChatExtractionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_wechat_article_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != WECHAT_HOST
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.path not in {"/s", "/s/"}
        and not parsed.path.startswith("/s/")
    ):
        raise WeChatExtractionError(
            "invalid_wechat_url",
            "只支持 https://mp.weixin.qq.com/s 开头的公众号文章链接。",
        )
    return value


class _ArticleParser(HTMLParser):
    _BLOCK_TAGS = {
        "article",
        "blockquote",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "li",
        "p",
        "section",
        "tr",
    }
    _IGNORED_TAGS = {"script", "style", "noscript", "svg"}
    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.document_title: list[str] = []
        self.parts: list[str] = []
        self.images: list[str] = []
        self._content_depth = 0
        self._ignored_depth = 0
        self._in_title = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        lowered = tag.casefold()
        if lowered == "meta":
            property_name = (
                attributes.get("property") or attributes.get("name") or ""
            ).casefold()
            if property_name in {"og:title", "twitter:title"}:
                self.title = attributes.get("content", "").strip() or self.title
        if lowered == "title":
            self._in_title = True
        if attributes.get("id") == "js_content":
            self._content_depth = 1
        elif self._content_depth and lowered not in self._VOID_TAGS:
            self._content_depth += 1
        if self._content_depth and lowered in self._IGNORED_TAGS:
            self._ignored_depth += 1
        if self._content_depth and not self._ignored_depth:
            if lowered in self._BLOCK_TAGS:
                self.parts.append("\n")
            if lowered == "img":
                source = (
                    attributes.get("data-src") or attributes.get("src") or ""
                ).strip()
                if source and source not in self.images:
                    self.images.append(source)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "title":
            self._in_title = False
        if self._content_depth:
            if lowered in self._IGNORED_TAGS and self._ignored_depth:
                self._ignored_depth -= 1
            if not self._ignored_depth and lowered in self._BLOCK_TAGS:
                self.parts.append("\n")
            self._content_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.document_title.append(data)
        if self._content_depth and not self._ignored_depth:
            self.parts.append(data)


def extract_wechat_article_html(page_html: str, source_url: str) -> dict[str, Any]:
    parser = _ArticleParser()
    parser.feed(page_html)
    title = html.unescape(parser.title or "".join(parser.document_title)).strip()
    title = re.sub(r"\s+", " ", title)
    content = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    content = re.sub(r"[ \t\f\v]+", " ", content)
    content = re.sub(r" *\n *", "\n", content)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    combined = f"{title}\n{content}"
    marker = next((item for item in BLOCK_PAGE_MARKERS if item in combined), "")
    if marker:
        raise WeChatExtractionError(
            "wechat_blocked",
            f"微信返回了“{marker}”验证/拦截页，没有获取到真实正文。",
        )
    if len(re.sub(r"\s+", "", content)) < MIN_ARTICLE_CHARS:
        raise WeChatExtractionError(
            "article_body_missing",
            "没有识别到完整公众号正文；请在浏览器打开原文确认后重试，或粘贴正文。",
        )
    images = [
        value for value in parser.images if value.startswith(("https://", "http://"))
    ][:MAX_ARTICLE_IMAGES]
    return {
        "title": title,
        "content": content,
        "source_url": source_url,
        "images": images,
    }


def fetch_wechat_article(url: str, *, timeout: float = 20.0) -> dict[str, Any]:
    source_url = validate_wechat_article_url(url)
    request = Request(
        source_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        method="GET",
    )
    try:
        with build_opener().open(request, timeout=timeout) as response:  # noqa: S310
            final_url = validate_wechat_article_url(response.geturl())
            body = response.read(MAX_ARTICLE_BYTES + 1)
            if len(body) > MAX_ARTICLE_BYTES:
                raise WeChatExtractionError("article_too_large", "公众号文章页面过大。")
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        raise WeChatExtractionError(
            "wechat_http_error",
            f"微信文章访问失败（HTTP {exc.code}）。",
        ) from None
    except (URLError, OSError, TimeoutError) as exc:
        raise WeChatExtractionError(
            "wechat_unavailable",
            "用户电脑无法访问该微信文章，请检查网络、代理或稍后重试。",
        ) from exc
    page_html = body.decode(charset, errors="replace")
    return extract_wechat_article_html(page_html, final_url)


__all__ = [
    "WeChatExtractionError",
    "extract_wechat_article_html",
    "fetch_wechat_article",
    "validate_wechat_article_url",
]
