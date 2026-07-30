from __future__ import annotations

from typing import Any

import httpx

from app.db import Database

from .auth import WECHAT_API_BASE_URL, WeChatAuth
from .client import WeChatClient


def build_wechat_auth(
    config: dict[str, Any],
    db: Database,
    app_id: str,
    app_secret: str,
    relay_settings: dict[str, Any] | None = None,
    cache_key: str | None = None,
) -> WeChatAuth:
    """Build token authentication with the effective dedicated relay."""

    base_url, basic_auth = _connection_options(config, db, relay_settings)
    return WeChatAuth(
        app_id=str(app_id or "").strip(),
        app_secret=str(app_secret or "").strip(),
        db=db,
        cache_key=cache_key,
        base_url=base_url,
        basic_auth=basic_auth,
    )


def build_wechat_client(
    config: dict[str, Any],
    db: Database,
    app_id: str,
    app_secret: str,
    relay_settings: dict[str, Any] | None = None,
    cache_key: str | None = None,
) -> WeChatClient:
    """Build one WeChat client whose token and API calls share one gateway."""

    base_url, basic_auth = _connection_options(config, db, relay_settings)
    auth = WeChatAuth(
        app_id=str(app_id or "").strip(),
        app_secret=str(app_secret or "").strip(),
        db=db,
        cache_key=cache_key,
        base_url=base_url,
        basic_auth=basic_auth,
    )
    return WeChatClient(
        get_token=auth.get_access_token,
        refresh_token=lambda: auth.get_access_token(force_refresh=True),
        base_url=base_url,
        basic_auth=basic_auth,
    )


def _connection_options(
    config: dict[str, Any],
    db: Database,
    relay_settings: dict[str, Any] | None,
) -> tuple[str, httpx.BasicAuth | None]:
    # app.services keeps compatibility exports that import the pipeline.  Delay
    # this import until client construction so importing app.wechat never forms
    # a services -> pipeline -> workflows -> wechat cycle.
    from app.services.wechat_relay_settings import (
        effective_wechat_relay_settings,
        validate_wechat_relay_settings,
    )

    if relay_settings is None:
        fallback = config.get("wechat_relay")
        if not isinstance(fallback, dict):
            fallback = config.get("wechat_proxy")
        effective = effective_wechat_relay_settings(
            db,
            fallback if isinstance(fallback, dict) else None,
        )
    else:
        effective = validate_wechat_relay_settings(relay_settings)
    if not bool(effective.get("enabled", False)):
        return WECHAT_API_BASE_URL, None
    return (
        str(effective["gateway_url"]).rstrip("/"),
        httpx.BasicAuth(
            str(effective.get("username") or ""),
            str(effective.get("password") or ""),
        ),
    )
