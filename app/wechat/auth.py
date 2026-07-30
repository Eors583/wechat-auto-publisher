from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import time
from typing import TYPE_CHECKING

import httpx

from .errors import WeChatHTTPError


logger = logging.getLogger(__name__)

WECHAT_API_BASE_URL = "https://api.weixin.qq.com"

if TYPE_CHECKING:
    from app.db import Database


class WeChatAuth:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        db: Database,
        *,
        cache_key: str | None = None,
        base_url: str = WECHAT_API_BASE_URL,
        basic_auth: httpx.BasicAuth | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.db = db
        self.base_url = str(base_url or WECHAT_API_BASE_URL).rstrip("/")
        self.basic_auth = basic_auth
        # 一个项目会同时访问发布账号和对标账号，令牌绝不能共用同一个缓存键。
        self.cache_key = cache_key or f"access_token:{app_id}"

    def get_access_token(self, force_refresh: bool = False) -> str:
        if not self.app_id or not self.app_secret:
            raise RuntimeError("WECHAT_APP_ID / WECHAT_APP_SECRET is empty")
        if not force_refresh:
            cached = self.db.get_token(self.cache_key)
            if cached:
                token, expires_at = cached
                if _still_valid(expires_at):
                    return token

        url = f"{self.base_url}/cgi-bin/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.app_id,
            "secret": self.app_secret,
        }
        for attempt in range(1, 4):
            try:
                with httpx.Client(timeout=20.0, auth=self.basic_auth) as client:
                    resp = client.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                break
            except httpx.HTTPStatusError as exc:
                status_code = int(exc.response.status_code)
                if status_code in {502, 503, 504} and attempt < 3:
                    delay = 0.5 * (2 ** (attempt - 1))
                    logger.warning(
                        "WeChat access token gateway HTTP %s, retrying "
                        "(%s/3) in %.1fs",
                        status_code,
                        attempt + 1,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise WeChatHTTPError(status_code) from None
            except (httpx.TransportError, ConnectionError, OSError) as exc:
                if attempt >= 3:
                    raise
                delay = 0.5 * (2 ** (attempt - 1))
                logger.warning(
                    "WeChat access token transport error, retrying (%s/3) in %.1fs: %s",
                    attempt + 1,
                    delay,
                    exc,
                )
                time.sleep(delay)
        if "access_token" not in data:
            raise RuntimeError(f"Failed to get access_token: {data}")
        token = data["access_token"]
        expires_in = int(data.get("expires_in", 7200))
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=max(expires_in - 300, 60))
        ).replace(microsecond=0).isoformat()
        self.db.set_token(token, expires_at, self.cache_key)
        return token


def _still_valid(expires_at: str) -> bool:
    try:
        exp = datetime.fromisoformat(expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp > datetime.now(timezone.utc)
    except ValueError:
        return False
