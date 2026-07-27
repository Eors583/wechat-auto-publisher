from __future__ import annotations

from pathlib import Path
from typing import Any

from .client import WeChatClient


def upload_article_image(client: WeChatClient, image_path: str | Path) -> str:
    """Upload image for article body. Returns WeChat CDN URL."""
    path = Path(image_path)
    with path.open("rb") as f:
        files = {"media": (path.name, f, "application/octet-stream")}
        data = client.request("POST", "/cgi-bin/media/uploadimg", files=files)
    url = data.get("url")
    if not url:
        raise RuntimeError(f"uploadimg missing url: {data}")
    return str(url)


def upload_thumb(client: WeChatClient, image_path: str | Path) -> str:
    """Upload permanent thumb material. Returns media_id."""
    return str(upload_permanent_image(client, image_path)["media_id"])


def upload_permanent_image(
    client: WeChatClient, image_path: str | Path
) -> dict[str, str]:
    """Upload a permanent image and retain both media_id and preview URL."""
    path = Path(image_path)
    with path.open("rb") as f:
        files = {"media": (path.name, f, "application/octet-stream")}
        data = client.request(
            "POST",
            "/cgi-bin/material/add_material",
            params={"type": "image"},
            files=files,
        )
    media_id = data.get("media_id")
    if not media_id:
        raise RuntimeError(f"add_material missing media_id: {data}")
    return {
        "media_id": str(media_id),
        "url": str(data.get("url") or ""),
    }


def batch_get_material(
    client: WeChatClient,
    material_type: str = "image",
    offset: int = 0,
    count: int = 20,
) -> dict[str, Any]:
    payload = {"type": material_type, "offset": offset, "count": count}
    return client.request("POST", "/cgi-bin/material/batchget_material", json_body=payload)
