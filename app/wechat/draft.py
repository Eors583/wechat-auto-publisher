from __future__ import annotations

from typing import Any

from .client import WeChatClient


def add_draft(client: WeChatClient, articles: list[dict[str, Any]]) -> str:
    """Create draft. Returns media_id."""
    payload = {"articles": articles}
    data = client.request("POST", "/cgi-bin/draft/add", json_body=payload)
    media_id = data.get("media_id")
    if not media_id:
        raise RuntimeError(f"draft/add missing media_id: {data}")
    return str(media_id)


def update_draft(
    client: WeChatClient,
    media_id: str,
    index: int,
    article: dict[str, Any],
) -> None:
    payload = {"media_id": media_id, "index": index, "articles": article}
    client.request("POST", "/cgi-bin/draft/update", json_body=payload)


def batchget_drafts(
    client: WeChatClient,
    *,
    offset: int = 0,
    count: int = 20,
    no_content: int = 0,
) -> dict[str, Any]:
    return client.request(
        "POST",
        "/cgi-bin/draft/batchget",
        json_body={"offset": offset, "count": count, "no_content": no_content},
    )


def get_draft(client: WeChatClient, media_id: str) -> dict[str, Any]:
    return client.request(
        "POST",
        "/cgi-bin/draft/get",
        json_body={"media_id": media_id},
    )


def list_draft_summaries(
    client: WeChatClient,
    *,
    max_items: int = 60,
) -> list[dict[str, Any]]:
    """列出草稿摘要：media_id / title / thumb_media_id / first article。"""
    out: list[dict[str, Any]] = []
    offset = 0
    page = 20
    while len(out) < max_items:
        data = batchget_drafts(client, offset=offset, count=page, no_content=0)
        items = data.get("item") or []
        total = int(data.get("total_count") or 0)
        for item in items:
            media_id = str(item.get("media_id") or "")
            news_item = ((item.get("content") or {}).get("news_item")) or []
            first = news_item[0] if news_item else {}
            out.append(
                {
                    "media_id": media_id,
                    "title": str(first.get("title") or ""),
                    "digest": str(first.get("digest") or ""),
                    "thumb_media_id": str(first.get("thumb_media_id") or ""),
                    "author": str(first.get("author") or ""),
                    "update_time": item.get("update_time"),
                    "article": article_from_news_item(first) if first else None,
                    "article_count": len(news_item),
                }
            )
            if len(out) >= max_items:
                break
        offset += len(items)
        if not items or offset >= total:
            break
    return out


def article_from_news_item(item: dict[str, Any]) -> dict[str, Any]:
    """把草稿 news_item 转成 draft/add 可用的 article 字段。"""
    return {
        "title": str(item.get("title") or "")[:64],
        "author": str(item.get("author") or "")[:16],
        "digest": str(item.get("digest") or "")[:120],
        "content": str(item.get("content") or ""),
        "content_source_url": str(item.get("content_source_url") or ""),
        "thumb_media_id": str(item.get("thumb_media_id") or ""),
        # 仅用于本地图片指纹匹配；draft/add 时不会提交该字段。
        "thumb_url": str(item.get("thumb_url") or ""),
        "need_open_comment": int(item.get("need_open_comment") or 0),
        "only_fans_can_comment": int(item.get("only_fans_can_comment") or 0),
    }


def build_article(
    *,
    title: str,
    content: str,
    thumb_media_id: str,
    author: str = "",
    digest: str = "",
    content_source_url: str = "",
    need_open_comment: int = 0,
    only_fans_can_comment: int = 0,
) -> dict[str, Any]:
    if not thumb_media_id:
        raise ValueError("thumb_media_id is required for WeChat draft")
    return {
        "title": title[:64],
        "author": author[:16],
        "digest": digest[:120],
        "content": content,
        "content_source_url": content_source_url,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": need_open_comment,
        "only_fans_can_comment": only_fans_can_comment,
    }
