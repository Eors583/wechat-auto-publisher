from __future__ import annotations

import base64
from unittest.mock import Mock, patch

import httpx

from app.db import Database
from app.wechat.factory import build_wechat_auth, build_wechat_client


RELAY = {
    "enabled": True,
    "gateway_url": "https://relay.example.com/wechat-relay",
    "username": "relay-user",
    "password": "relay-password",
}


def _transport_with_response(payload: dict) -> Mock:
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    transport.get.return_value = httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", "https://relay.example.com/test"),
    )
    transport.request.return_value = httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", "https://relay.example.com/test"),
    )
    return transport


def _authorization_header(auth: httpx.BasicAuth) -> str:
    request = httpx.Request("GET", "https://relay.example.com/test")
    authenticated = next(auth.auth_flow(request))
    return str(authenticated.headers["Authorization"])


def test_token_request_uses_gateway_prefix_and_basic_auth(tmp_path) -> None:
    db = Database(tmp_path / "relay.db")
    transport = _transport_with_response(
        {"access_token": "wechat-token", "expires_in": 7200}
    )
    auth = build_wechat_auth(
        {},
        db,
        "wx-app",
        "wx-secret",
        relay_settings=RELAY,
    )

    with patch("app.wechat.auth.httpx.Client", return_value=transport) as client_type:
        assert auth.get_access_token(force_refresh=True) == "wechat-token"

    transport.get.assert_called_once()
    assert transport.get.call_args.args[0] == (
        "https://relay.example.com/wechat-relay/cgi-bin/token"
    )
    assert transport.get.call_args.kwargs["params"]["appid"] == "wx-app"
    basic_auth = client_type.call_args.kwargs["auth"]
    expected = base64.b64encode(b"relay-user:relay-password").decode("ascii")
    assert _authorization_header(basic_auth) == f"Basic {expected}"


def test_api_request_uses_same_gateway_and_basic_auth(tmp_path) -> None:
    db = Database(tmp_path / "relay.db")
    cache_key = "relay-test:wx-app"
    db.set_token("cached-token", "2099-01-01T00:00:00+00:00", cache_key)
    transport = _transport_with_response({"media_id": "draft-id"})
    client = build_wechat_client(
        {},
        db,
        "wx-app",
        "wx-secret",
        relay_settings=RELAY,
        cache_key=cache_key,
    )

    with patch("app.wechat.client.httpx.Client", return_value=transport) as client_type:
        result = client.request(
            "POST",
            "/cgi-bin/draft/add",
            json_body={"articles": [{"title": "test"}]},
        )

    assert result["media_id"] == "draft-id"
    assert transport.request.call_args.args[:2] == (
        "POST",
        "https://relay.example.com/wechat-relay/cgi-bin/draft/add",
    )
    assert transport.request.call_args.kwargs["params"]["access_token"] == (
        "cached-token"
    )
    expected = base64.b64encode(b"relay-user:relay-password").decode("ascii")
    assert _authorization_header(client_type.call_args.kwargs["auth"]) == (
        f"Basic {expected}"
    )


def test_disabled_relay_keeps_direct_wechat_endpoint_without_basic_auth(
    tmp_path,
) -> None:
    db = Database(tmp_path / "relay.db")
    cache_key = "relay-test:wx-app"
    db.set_token("cached-token", "2099-01-01T00:00:00+00:00", cache_key)
    transport = _transport_with_response({"total_count": 0, "item": []})
    client = build_wechat_client(
        {},
        db,
        "wx-app",
        "wx-secret",
        relay_settings={"enabled": False},
        cache_key=cache_key,
    )

    with patch("app.wechat.client.httpx.Client", return_value=transport) as client_type:
        client.request(
            "POST",
            "/cgi-bin/draft/batchget",
            json_body={"offset": 0, "count": 1, "no_content": 1},
        )

    assert transport.request.call_args.args[1] == (
        "https://api.weixin.qq.com/cgi-bin/draft/batchget"
    )
    assert client_type.call_args.kwargs["auth"] is None

