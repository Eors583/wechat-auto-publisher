from __future__ import annotations

import logging
import random
from typing import Any, Callable

logger = logging.getLogger(__name__)


def resolve_cover(
    *,
    topic: str,
    config: dict[str, Any],
    override_media_id: str | None = None,
    pick_from_library: Callable[[], str] | None = None,
    fallback_media_id: str | None = None,
) -> str:
    """选择封面 media_id。

    优先级：
    1. CLI / 任务指定的 override
    2. 话题关键词映射
    3. config.cover.default_media_id（固定图）
    4. 素材库随机（cover.from_material_library=true 时）
    """
    if override_media_id:
        return override_media_id.strip()

    cover_cfg = config.get("cover", {}) or {}
    keyword_map = cover_cfg.get("keyword_map") or {}
    topic_l = (topic or "").lower()

    keys = sorted(keyword_map.keys(), key=lambda k: len(str(k)), reverse=True)
    for key in keys:
        needle = str(key).lower()
        if needle and needle in topic_l:
            media_id = str(keyword_map[key] or "").strip()
            if media_id:
                return media_id

    default_id = str(cover_cfg.get("default_media_id") or "").strip()
    use_library = bool(cover_cfg.get("from_material_library", True))

    # 未开素材库随机时，必须有默认图
    if default_id and not use_library:
        return default_id

    # 开了素材库：优先随机；失败再退回默认图
    if use_library and pick_from_library is not None:
        try:
            media_id = pick_from_library()
            if media_id:
                logger.info("Picked cover from material library: %s", media_id[:24])
                return media_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("Material library cover pick failed: %s", exc)
            if default_id:
                return default_id
            if fallback_media_id:
                logger.warning("Using the account's last successful cover as fallback")
                return fallback_media_id.strip()
            raise

    if default_id:
        return default_id

    if fallback_media_id:
        return fallback_media_id.strip()

    raise ValueError(
        "No cover media_id found. Upload images to WeChat material library, "
        "or set cover.default_media_id / pass --cover-media-id."
    )


def pick_random_image_media_id(client: Any, *, page_size: int = 20, max_pages: int = 5) -> str:
    """从公众号永久图片素材中随机取一张。"""
    from app.wechat.material import batch_get_material

    ids: list[str] = []
    offset = 0
    total = None
    for _ in range(max_pages):
        data = batch_get_material(client, material_type="image", offset=offset, count=page_size)
        if total is None:
            total = int(data.get("total_count") or 0)
        items = data.get("item") or []
        for item in items:
            mid = (item or {}).get("media_id")
            if mid:
                ids.append(str(mid))
        offset += len(items)
        if not items or offset >= (total or 0):
            break

    if not ids:
        raise ValueError(
            "素材库没有永久图片。请到公众号后台「素材管理 → 图片」上传至少一张封面图。"
        )
    return random.choice(ids)
