from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = (webhook_url or "").strip() or None

    def send(self, title: str, content: str, *, level: str = "info") -> None:
        message = f"[{level.upper()}] {title}\n{content}"
        if level == "error":
            logger.error(message)
        else:
            logger.info(message)
        if not self.webhook_url:
            return
        payload: dict[str, Any] = {
            "msgtype": "text",
            "text": {"content": message[:3500]},
        }
        try:
            httpx.post(self.webhook_url, json=payload, timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Webhook notify failed: %s", exc)
