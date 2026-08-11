from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.db import Database
from app.providers import jizhile_api as provider
from app.providers.jizhile_api import (
    JizhileApiError,
    fetch_jizhile_account_articles,
)
from app.providers.jizhile_api import (
    test_jizhile_api as verify_jizhile_api,
)
from app.services.auth import AuthService
from app.services.jizhile_settings import (
    clear_jizhile_settings,
    effective_jizhile_settings,
    public_jizhile_settings,
    save_jizhile_settings,
)


class _FakeClient:
    def __init__(self, handler):
        self.handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, path: str, *, json: dict[str, Any]):
        payload = self.handler(path, json)
        request = httpx.Request("POST", f"https://www.dajiala.com{path}")
        return httpx.Response(200, json=payload, request=request)


def test_history_fetch_uses_account_nickname_and_follows_offset(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def handler(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((path, dict(payload)))
        if not payload["offset"]:
            return {
                "code": 0,
                "mp_nickname": "蓝血研究",
                "mp_wxid": "lanxueyanjiu",
                "offset": "next-page",
                "is_end": 0,
                "data": [
                    {
                        "title": "文章一",
                        "url": "https://mp.weixin.qq.com/s/article-1",
                        "digest": "摘要一",
                        "cover_url": "https://mmbiz.qpic.cn/cover-1.jpg",
                        "post_time": 1786429678,
                        "sn": "sn-1",
                    },
                    {
                        "title": "文章二",
                        "url": "https://mp.weixin.qq.com/s/article-2",
                        "post_time": 1786420000,
                        "appmsgid": 22,
                        "position": 2,
                    },
                ],
            }
        return {
            "code": 0,
            "nickname": "蓝血研究",
            "offset": "",
            "is_end": 1,
            "data": [
                {
                    "title": "文章三",
                    "url": "https://mp.weixin.qq.com/s/article-3",
                    "post_time": 1786410000,
                    "sn": "sn-3",
                }
            ],
        }

    monkeypatch.setattr(provider, "_client", lambda **_kwargs: _FakeClient(handler))
    rows = fetch_jizhile_account_articles(
        "蓝血研究",
        key="secret-key",
        verifycode="extra-code",
        limit=3,
    )

    assert len(rows) == 3
    assert rows[0]["title"] == "文章一"
    assert rows[0]["account_name"] == "蓝血研究"
    assert rows[0]["external_key"] == "sn-1"
    assert rows[1]["external_key"] == "22:2"
    assert calls[0][0] == provider.POST_HISTORY_PATH
    assert calls[0][1] == {
        "nickname": "蓝血研究",
        "offset": "",
        "key": "secret-key",
        "verifycode": "extra-code",
    }
    assert calls[1][1]["offset"] == "next-page"


def test_history_fetch_prefers_sample_article_url(monkeypatch) -> None:
    seen: list[dict[str, Any]] = []

    def handler(_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(payload))
        return {"code": 0, "data": [], "is_end": 1}

    monkeypatch.setattr(provider, "_client", lambda **_kwargs: _FakeClient(handler))
    fetch_jizhile_account_articles(
        "蓝血研究",
        wechat_id="lanxueyanjiu",
        sample_url="https://mp.weixin.qq.com/s/sample",
        key="secret-key",
    )
    assert seen[0]["url"] == "https://mp.weixin.qq.com/s/sample"
    assert "nickname" not in seen[0]


def test_balance_endpoint_validates_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        provider,
        "_client",
        lambda **_kwargs: _FakeClient(
            lambda path, payload: {
                "code": 0,
                "remain_money": 12.34,
                "yesterday_money": 15.67,
                "request_time": "2026-08-11 16:30:00",
            }
        ),
    )
    result = verify_jizhile_api(key="secret-key", verifycode="extra-code")
    assert result == {
        "ok": True,
        "remain_money": 12.34,
        "yesterday_money": 15.67,
        "request_time": "2026-08-11 16:30:00",
    }


def test_invalid_key_is_reported_without_exposing_key(monkeypatch) -> None:
    monkeypatch.setattr(
        provider,
        "_client",
        lambda **_kwargs: _FakeClient(
            lambda _path, _payload: {"code": 10002, "msg": "invalid"}
        ),
    )
    with pytest.raises(JizhileApiError, match="API Key 或附加码不正确") as exc:
        verify_jizhile_api(key="private-value")
    assert "private-value" not in str(exc.value)


def test_jizhile_settings_encrypt_secrets_and_are_customer_scoped(
    tmp_path: Path,
) -> None:
    root = Database(tmp_path / "jizhile-settings.db")
    auth = AuthService(root)
    user_a = auth.register("jizhile-user-a", "secret1")
    user_b = auth.register("jizhile-user-b", "secret2")
    db = root.for_user(str(user_a["id"]))
    save_jizhile_settings(
        db,
        enabled=True,
        key="private-key",
        verifycode="private-code",
        session_label="运营账户",
        remain_money=9.5,
        checked_at="2026-08-11 16:30:00",
    )

    raw = db.get_setting("jizhile_api") or ""
    assert "private-key" not in raw
    assert "private-code" not in raw
    assert json.loads(raw)["key_encrypted"]
    assert effective_jizhile_settings(db)["key"] == "private-key"
    assert effective_jizhile_settings(db)["verifycode"] == "private-code"
    assert public_jizhile_settings(db) == {
        "enabled": True,
        "has_key": True,
        "has_verifycode": True,
        "session_label": "运营账户",
        "remain_money": 9.5,
        "checked_at": "2026-08-11 16:30:00",
    }
    assert public_jizhile_settings(root.for_user(str(user_b["id"])))["has_key"] is False

    clear_jizhile_settings(db)
    assert public_jizhile_settings(db)["has_key"] is False
