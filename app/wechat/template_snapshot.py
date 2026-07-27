from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import html as lxml_html

from .client import WeChatClient
from .draft import batchget_drafts


@dataclass
class TemplateSnapshot:
    content: str
    path: Path
    source_media_id: str | None = None
    source_index: int | None = None
    source_title: str | None = None


@dataclass
class TemplateDraftCandidate:
    media_id: str
    article_index: int
    title: str
    content: str
    has_placeholder: bool

    @property
    def key(self) -> str:
        return f"{self.media_id}:{self.article_index}"


def load_template_snapshot(config: dict[str, Any]) -> TemplateSnapshot | None:
    path = _snapshot_path(config)
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    placeholder = _placeholder(config)
    if placeholder not in _plain_text(content):
        return None
    return TemplateSnapshot(content=content, path=path)


def capture_template_snapshot(
    client: WeChatClient,
    config: dict[str, Any],
) -> TemplateSnapshot:
    """从一篇已插入后台模板的临时文章中提取 HTML，并缓存为本地快照。"""
    placeholder = _placeholder(config)
    preferred_title = str(config.get("capture_title") or "").strip()
    selected_media_id = str(config.get("selected_media_id") or "").strip()
    selected_index = int(config.get("selected_article_index") or 0)
    scan_limit = max(1, int(config.get("scan_limit") or 80))
    offset = 0
    scanned = 0
    fallback: tuple[str, int, str, str] | None = None

    while scanned < scan_limit:
        data = batchget_drafts(client, offset=offset, count=20, no_content=0)
        rows = data.get("item") or []
        total = int(data.get("total_count") or 0)
        for row in rows:
            scanned += 1
            media_id = str(row.get("media_id") or "")
            items = ((row.get("content") or {}).get("news_item")) or []
            for index, item in enumerate(items):
                title = str(item.get("title") or "").strip()
                content = str(item.get("content") or "")
                if selected_media_id and media_id == selected_media_id and index == selected_index:
                    if placeholder not in _plain_text(content):
                        raise RuntimeError(
                            f"所选模板草稿“{title}”不包含正文占位符“{placeholder}”"
                        )
                    return _save_snapshot(config, media_id, index, title, content)
                if placeholder not in _plain_text(content):
                    continue
                candidate = (media_id, index, title, content)
                if preferred_title and title == preferred_title:
                    return _save_snapshot(config, *candidate)
                if fallback is None:
                    fallback = candidate
            if scanned >= scan_limit:
                break
        offset += len(rows)
        if not rows or offset >= total:
            break

    if selected_media_id:
        raise RuntimeError("所选模板草稿已不存在，请重新选择")
    if fallback:
        return _save_snapshot(config, *fallback)
    raise RuntimeError(
        "未找到已插入‘蓝血经营管理系统模版’且仍保留"
        "‘蓝血经营管理系统正文’占位文字的文章。"
    )


def list_template_draft_candidates(
    client: WeChatClient,
    config: dict[str, Any],
    *,
    keyword: str = "模板",
) -> list[TemplateDraftCandidate]:
    """List template drafts from one authenticated official account only."""
    placeholder = _placeholder(config)
    scan_limit = max(1, int(config.get("scan_limit") or 80))
    offset = 0
    scanned = 0
    candidates: list[TemplateDraftCandidate] = []
    while scanned < scan_limit:
        data = batchget_drafts(client, offset=offset, count=20, no_content=0)
        rows = data.get("item") or []
        total = int(data.get("total_count") or 0)
        for row in rows:
            scanned += 1
            media_id = str(row.get("media_id") or "")
            items = ((row.get("content") or {}).get("news_item")) or []
            for index, item in enumerate(items):
                title = str(item.get("title") or "").strip()
                if keyword not in title:
                    continue
                content = str(item.get("content") or "")
                candidates.append(
                    TemplateDraftCandidate(
                        media_id=media_id,
                        article_index=index,
                        title=title,
                        content=content,
                        has_placeholder=placeholder in _plain_text(content),
                    )
                )
            if scanned >= scan_limit:
                break
        offset += len(rows)
        if not rows or offset >= total:
            break
    return candidates


def save_template_draft_candidate(
    config: dict[str, Any], candidate: TemplateDraftCandidate
) -> TemplateSnapshot:
    placeholder = _placeholder(config)
    if placeholder not in _plain_text(candidate.content):
        raise ValueError(
            f"模板草稿“{candidate.title}”缺少正文占位符“{placeholder}”，无法套用"
        )
    return _save_snapshot(
        config,
        candidate.media_id,
        candidate.article_index,
        candidate.title,
        candidate.content,
    )


def merge_template_html(
    template_html: str,
    generated_html: str,
    placeholder: str = "蓝血经营管理系统正文",
) -> str:
    """替换模板占位块，同时保留模板固定图片、页眉、页尾及样式。"""
    root = lxml_html.fragment_fromstring(template_html, create_parent="div")
    target = _find_placeholder_target(root, placeholder)
    if target is None or target.getparent() is None:
        raise ValueError(f"模板快照中未找到独立的正文占位符：{placeholder}")

    parent = target.getparent()
    position = parent.index(target)
    for fragment in lxml_html.fragments_fromstring(generated_html):
        if isinstance(fragment, str):
            wrapper = lxml_html.Element("p")
            wrapper.text = fragment
            fragment = wrapper
        parent.insert(position, fragment)
        position += 1
    parent.remove(target)
    return "".join(
        lxml_html.tostring(child, encoding="unicode", method="html")
        for child in root
    )


def _find_placeholder_target(root: Any, placeholder: str) -> Any | None:
    """Find the smallest replaceable block without swallowing media-only siblings."""
    matches = [
        element
        for element in root.iterdescendants()
        if _is_placeholder_text("".join(element.itertext()), placeholder)
    ]
    if not matches:
        return None

    # Ancestors appear before descendants. Starting from the deepest exact match
    # avoids treating a section that also owns images/cards as the text slot.
    target = matches[-1]
    current = target
    while current.getparent() is not None and current.getparent() is not root:
        parent = current.getparent()
        if parent.tag in {"p", "li", "blockquote", "h1", "h2", "h3"} and (
            _is_placeholder_text("".join(parent.itertext()), placeholder)
        ):
            target = parent
            current = parent
            continue
        break
    return target


def _is_placeholder_text(value: str, placeholder: str) -> bool:
    """Accept decorative arrows around a placeholder, but no meaningful copy."""
    normalized = _normalize_text(value)
    if normalized == placeholder:
        return True
    decoration = re.sub(r"[▼▽▾▿▲△▴▵↓↑→←·•—\-]+", "", normalized)
    return decoration == placeholder


def _remove_incompatible_embeds(root: Any) -> None:
    """Drop unsupported embeds and their otherwise-empty wrappers from a snapshot."""
    for node in list(root.xpath(".//script|.//iframe|.//object|.//embed")):
        target = node
        parent = target.getparent()
        while (
            parent is not None
            and parent is not root
            and len(parent) == 1
            and not _normalize_text("".join(parent.itertext()))
        ):
            target = parent
            parent = target.getparent()
        if parent is not None:
            parent.remove(target)


def _save_snapshot(
    config: dict[str, Any],
    media_id: str,
    index: int,
    title: str,
    content: str,
) -> TemplateSnapshot:
    path = _snapshot_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = sanitize_template_html(content)
    path.write_text(content, encoding="utf-8")
    return TemplateSnapshot(content, path, media_id, index, title)


def sanitize_template_html(content: str) -> str:
    """Keep the official-account draft template intact; only its body slot is replaced."""
    return content or ""


def _snapshot_path(config: dict[str, Any]) -> Path:
    path = Path(str(config.get("snapshot_path") or "data/editor_template.html"))
    if path.is_absolute():
        return path
    root = Path(str(config.get("_root") or Path.cwd()))
    return root / path


def _placeholder(config: dict[str, Any]) -> str:
    return str(config.get("placeholder") or "蓝血经营管理系统正文").strip()


def _plain_text(value: str) -> str:
    try:
        root = lxml_html.fragment_fromstring(value, create_parent="div")
        return _normalize_text("".join(root.itertext()))
    except (ValueError, TypeError):
        return _normalize_text(re.sub(r"<[^>]+>", " ", value))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "")
