from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)


@dataclass
class IngestedContent:
    title: str = ""
    content: str = ""
    source_url: str | None = None
    images: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def ingest_text(text: str, title: str = "", source_url: str | None = None) -> IngestedContent:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Empty text content")
    return IngestedContent(title=title.strip(), content=cleaned, source_url=source_url)


def ingest_url(url: str, timeout: float = 30.0) -> IngestedContent:
    url = url.strip()
    if not url:
        raise ValueError("Empty URL")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; WeChatAutoPublisher/0.1; +https://localhost)"
        )
    }
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        html = resp.text

    title, content, images = _extract_with_trafilatura(html, url)
    if not content or len(content.strip()) < 80:
        title2, content2, images2 = _extract_with_readability(html, url)
        title = title or title2
        content = content2 or content
        images = images or images2

    if not content or len(content.strip()) < 40:
        raise ValueError(
            f"Failed to extract article body from URL: {url}. Please paste text manually."
        )

    return IngestedContent(
        title=title or "",
        content=content.strip(),
        source_url=url,
        images=images,
        meta={"extractor": "trafilatura/readability"},
    )


def ingest_urls(urls: list[str], timeout: float = 30.0) -> IngestedContent:
    """Combine multiple reference articles while preserving source boundaries."""
    items = [ingest_url(url, timeout=timeout) for url in urls]
    if not items:
        raise ValueError("At least one reference URL is required")
    sections = [
        f"【参考资料 {index}：{item.title or item.source_url or '未命名'}】\n{item.content}"
        for index, item in enumerate(items, 1)
    ]
    return IngestedContent(
        title=items[0].title,
        content="\n\n".join(sections),
        source_url=items[0].source_url,
        images=list(dict.fromkeys(image for item in items for image in item.images)),
        meta={"reference_urls": [item.source_url for item in items]},
    )


def _extract_with_trafilatura(html: str, url: str) -> tuple[str, str, list[str]]:
    try:
        import trafilatura
        from trafilatura import extract, extract_metadata
    except ImportError:
        logger.warning("trafilatura not installed")
        return "", "", []

    text = extract(
        html,
        url=url,
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    ) or ""
    meta = extract_metadata(html, default_url=url)
    title = (meta.title if meta else "") or ""
    images: list[str] = []
    if meta and getattr(meta, "image", None):
        images.append(urljoin(url, meta.image))
    return title, text, images


def _extract_with_readability(html: str, url: str) -> tuple[str, str, list[str]]:
    try:
        from readability import Document
    except ImportError:
        logger.warning("readability-lxml not installed")
        return "", "", []

    doc = Document(html)
    title = doc.short_title() or doc.title() or ""
    summary_html = doc.summary() or ""
    text = _html_to_text(summary_html)
    images = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', summary_html, flags=re.I)
    images = [urljoin(url, src) for src in images]
    return title, text, images


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?i)</div>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
