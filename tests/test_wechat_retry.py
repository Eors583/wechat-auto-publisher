from __future__ import annotations

from unittest.mock import Mock, patch

import httpx
import pytest

from app.db import Database
from app.wechat.auth import WeChatAuth
from app.wechat.client import WeChatClient
from app.wechat.errors import WeChatHTTPError


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


@pytest.mark.parametrize(
    "path",
    [
        "/cgi-bin/media/uploadimg",
        "/cgi-bin/material/add_material",
        "/cgi-bin/freepublish/submit",
    ],
)
def test_mutating_upload_and_publish_requests_are_never_replayed(path: str) -> None:
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
            client.request("POST", path, data={"type": "image"})

    assert transport.request.call_count == 1


def test_safe_read_retries_temporary_gateway_http_error() -> None:
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    gateway_request = httpx.Request(
        "POST",
        "https://relay.example.com/wechat-relay/cgi-bin/draft/batchget",
    )
    transport.request.side_effect = [
        httpx.Response(502, request=gateway_request),
        httpx.Response(
            200,
            json={"total_count": 0, "item_count": 0, "item": []},
            request=gateway_request,
        ),
    ]
    client = WeChatClient(
        lambda: "token",
        lambda: "token",
        retry_backoff_seconds=0,
        base_url="https://relay.example.com/wechat-relay",
    )

    with patch("app.wechat.client.httpx.Client", return_value=transport):
        result = client.request(
            "POST",
            "/cgi-bin/draft/batchget",
            json_body={"offset": 0, "count": 1, "no_content": 1},
        )

    assert result["total_count"] == 0
    assert transport.request.call_count == 2


def test_token_http_error_never_exposes_appsecret_in_message(tmp_path) -> None:
    app_secret = "must-not-appear-in-error"
    response = httpx.Response(
        401,
        request=httpx.Request(
            "GET",
            "https://relay.example.com/wechat-relay/cgi-bin/token"
            f"?grant_type=client_credential&appid=wx-test&secret={app_secret}",
        ),
    )
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    transport.get.return_value = response
    auth = WeChatAuth(
        "wx-test",
        app_secret,
        Database(tmp_path / "relay-http-error.db"),
        base_url="https://relay.example.com/wechat-relay",
    )

    with patch("app.wechat.auth.httpx.Client", return_value=transport):
        with pytest.raises(WeChatHTTPError) as raised:
            auth.get_access_token(force_refresh=True)

    assert raised.value.status_code == 401
    assert app_secret not in str(raised.value)
