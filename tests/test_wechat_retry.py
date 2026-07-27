from __future__ import annotations

from unittest.mock import Mock, patch

import httpx
import pytest

from app.wechat.client import WeChatClient


def _response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", "https://api.weixin.qq.com/test"),
    )


def test_safe_material_read_retries_after_connection_reset() -> None:
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    transport.request.side_effect = [
        httpx.ReadError("[WinError 10054] connection reset"),
        _response({"total_count": 1, "item_count": 1, "item": []}),
    ]
    client = WeChatClient(
        lambda: "token",
        lambda: "token",
        retry_backoff_seconds=0,
    )

    with patch("app.wechat.client.httpx.Client", return_value=transport):
        result = client.request(
            "POST",
            "/cgi-bin/material/batchget_material",
            json_body={"type": "image", "offset": 0, "count": 1},
        )

    assert result["total_count"] == 1
    assert transport.request.call_count == 2


def test_draft_creation_is_not_retried_to_avoid_duplicate_drafts() -> None:
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    transport.request.side_effect = httpx.ReadError("connection reset")
    client = WeChatClient(
        lambda: "token",
        lambda: "token",
        retry_backoff_seconds=0,
    )

    with patch("app.wechat.client.httpx.Client", return_value=transport):
        with pytest.raises(httpx.ReadError):
            client.request(
                "POST",
                "/cgi-bin/draft/add",
                json_body={"articles": []},
            )

    assert transport.request.call_count == 1
