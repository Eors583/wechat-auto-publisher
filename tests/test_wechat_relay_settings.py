from __future__ import annotations

import json

import pytest

from app.db import Database
from app.services.wechat_relay_settings import (
    DEFAULT_WECHAT_RELAY_FIXED_EGRESS_IP,
    DEFAULT_WECHAT_RELAY_GATEWAY_URL,
    SETTING_KEY,
    WechatRelayAccessCodeError,
    decode_wechat_relay_access_code,
    effective_wechat_relay_test_account,
    effective_wechat_relay_settings,
    encode_wechat_relay_access_code,
    public_wechat_relay_connection_info,
    public_wechat_relay_test_account,
    public_wechat_relay_settings,
    save_wechat_relay_access_code,
    save_wechat_relay_test_account,
    save_wechat_relay_settings,
    validate_wechat_relay_settings,
)


def test_relay_settings_encrypt_password_and_hide_it_from_public_view(tmp_path) -> None:
    db = Database(tmp_path / "relay.db")

    save_wechat_relay_settings(
        db,
        enabled=True,
        gateway_url="https://Relay.Example.com/wechat-relay/",
        username=" operator ",
        password="relay-secret",
    )

    raw = db.get_setting(SETTING_KEY) or ""
    assert "relay-secret" not in raw
    stored = json.loads(raw)
    assert stored["password_encrypted"]
    assert public_wechat_relay_settings(db) == {
        "enabled": True,
        "gateway_url": "https://relay.example.com/wechat-relay",
        "username": "operator",
        "has_password": True,
    }
    assert effective_wechat_relay_settings(db) == {
        "enabled": True,
        "gateway_url": "https://relay.example.com/wechat-relay",
        "username": "operator",
        "password": "relay-secret",
    }


def test_blank_password_preserves_saved_secret_and_clear_is_explicit(tmp_path) -> None:
    db = Database(tmp_path / "relay.db")
    save_wechat_relay_settings(
        db,
        enabled=True,
        gateway_url="https://relay.example.com",
        username="operator",
        password="first-secret",
    )

    save_wechat_relay_settings(
        db,
        enabled=True,
        gateway_url="https://relay.example.com/v2",
        username="operator",
        password="",
    )
    assert effective_wechat_relay_settings(db)["password"] == "first-secret"

    save_wechat_relay_settings(
        db,
        enabled=False,
        gateway_url="https://relay.example.com/v2",
        username="operator",
        clear_password=True,
    )
    assert public_wechat_relay_settings(db)["has_password"] is False
    with pytest.raises(ValueError, match="中转密码"):
        save_wechat_relay_settings(
            db,
            enabled=True,
            gateway_url="https://relay.example.com/v2",
            username="operator",
        )


def test_database_settings_override_config_fallback(tmp_path) -> None:
    db = Database(tmp_path / "relay.db")
    fallback = {
        "enabled": True,
        "base_url": "https://fallback.example.com/api",
        "username": "fallback-user",
        "password": "fallback-password",
    }
    assert effective_wechat_relay_settings(db, fallback)["gateway_url"] == (
        "https://fallback.example.com/api"
    )

    save_wechat_relay_settings(
        db,
        enabled=False,
        gateway_url="",
        username="",
    )
    assert effective_wechat_relay_settings(db, fallback) == {
        "enabled": False,
        "gateway_url": "",
        "username": "",
        "password": "",
    }


@pytest.mark.parametrize(
    "gateway_url",
    [
        "http://relay.example.com",
        "ftp://relay.example.com",
        "https://user:secret@relay.example.com",
        "https://relay.example.com/path?token=secret",
        "https://relay.example.com/path#fragment",
        "https:///missing-host",
        "https://api.weixin.qq.com",
    ],
)
def test_relay_rejects_unsafe_gateway_urls(tmp_path, gateway_url: str) -> None:
    db = Database(tmp_path / "relay.db")
    with pytest.raises(ValueError):
        save_wechat_relay_settings(
            db,
            enabled=True,
            gateway_url=gateway_url,
            username="operator",
            password="secret",
        )


def test_temporary_override_accepts_gateway_url_or_legacy_base_url() -> None:
    current = validate_wechat_relay_settings(
        {
            "enabled": True,
            "gateway_url": "https://relay.example.com/wechat",
            "username": "user",
            "password": "password",
        }
    )
    legacy = validate_wechat_relay_settings(
        {
            "enabled": True,
            "base_url": "https://relay.example.com/wechat",
            "username": "user",
            "password": "password",
        }
    )
    assert current == legacy


def test_relay_access_code_round_trip_uses_stable_wr1_format() -> None:
    access_code = encode_wechat_relay_access_code(
        "wechat-client-001",
        "relay-test-password-2026",
    )

    assert access_code == (
        "wr1.d2VjaGF0LWNsaWVudC0wMDE.cmVsYXktdGVzdC1wYXNzd29yZC0yMDI2.79313d2c56827d24"
    )
    assert decode_wechat_relay_access_code(access_code) == {
        "username": "wechat-client-001",
        "password": "relay-test-password-2026",
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda code: code.replace("wr1.", "wr2.", 1),
        lambda code: code[:-1] + ("0" if code[-1] != "0" else "1"),
        lambda code: code.rsplit(".", 1)[0],
        lambda code: code.replace(".", "+", 1),
        lambda _code: "wr1.not-base64!.still-invalid.0000000000000000",
    ],
)
def test_relay_access_code_rejects_wrong_version_tampering_and_damage(
    mutate,
) -> None:
    password = "relay-test-password-2026"
    access_code = encode_wechat_relay_access_code("wechat-client-001", password)

    with pytest.raises(WechatRelayAccessCodeError) as caught:
        decode_wechat_relay_access_code(mutate(access_code))

    assert str(caught.value) == "中转接入码无效或已损坏，请重新复制完整接入码"
    assert password not in str(caught.value)
    assert access_code not in str(caught.value)


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("name:with-colon", "relay-test-password-2026"),
        ("name with spaces", "relay-test-password-2026"),
        ("wechat-client-001", "too-short"),
        ("wechat-client-001", "contains whitespace"),
        ("wechat-client-001", "密码不能进入BasicAuth"),
        ("wechat-client-001", "x" * 73),
    ],
)
def test_relay_access_code_encoder_rejects_unsafe_basic_auth_credentials(
    username: str,
    password: str,
) -> None:
    with pytest.raises(ValueError):
        encode_wechat_relay_access_code(username, password)


def test_saving_relay_access_code_reuses_encrypted_storage_without_echo(
    tmp_path,
) -> None:
    db = Database(tmp_path / "relay-access-code.db")
    password = "relay-test-password-2026"
    access_code = encode_wechat_relay_access_code("wechat-client-001", password)

    result = save_wechat_relay_access_code(db, access_code)

    assert result == {
        "enabled": True,
        "gateway_url": DEFAULT_WECHAT_RELAY_GATEWAY_URL,
        "username": "wechat-client-001",
        "has_password": True,
    }
    assert "password" not in result
    assert "access_code" not in result
    raw = db.get_setting(SETTING_KEY) or ""
    assert access_code not in raw
    assert password not in raw
    assert effective_wechat_relay_settings(db) == {
        "enabled": True,
        "gateway_url": DEFAULT_WECHAT_RELAY_GATEWAY_URL,
        "username": "wechat-client-001",
        "password": password,
    }


def test_invalid_relay_access_code_does_not_change_saved_settings(tmp_path) -> None:
    db = Database(tmp_path / "relay-access-code-invalid.db")
    save_wechat_relay_settings(
        db,
        enabled=True,
        gateway_url="https://relay.example.com/wechat",
        username="existing-client",
        password="existing-password-2026",
    )
    before = db.get_setting(SETTING_KEY)

    with pytest.raises(WechatRelayAccessCodeError):
        save_wechat_relay_access_code(db, "wr1.damaged")

    assert db.get_setting(SETTING_KEY) == before
    assert effective_wechat_relay_settings(db)["password"] == ("existing-password-2026")


def test_public_relay_connection_info_contains_no_credentials() -> None:
    public = public_wechat_relay_connection_info()

    assert public == {
        "gateway_url": DEFAULT_WECHAT_RELAY_GATEWAY_URL,
        "fixed_egress_ip": DEFAULT_WECHAT_RELAY_FIXED_EGRESS_IP,
    }
    assert set(public) == {"gateway_url", "fixed_egress_ip"}


def test_relay_test_account_is_encrypted_and_not_a_publish_account(tmp_path) -> None:
    db = Database(tmp_path / "relay-test-account.db")

    public = save_wechat_relay_test_account(
        db,
        name="蓝血测试号",
        app_id="wx-test-app-id",
        app_secret="wechat-private-secret",
    )

    assert public == {
        "name": "蓝血测试号",
        "app_id": "wx-test-app-id",
        "has_app_secret": True,
    }
    raw = db.get_setting("wechat_api_relay_test_account") or ""
    assert "wechat-private-secret" not in raw
    assert effective_wechat_relay_test_account(db) == {
        "name": "蓝血测试号",
        "app_id": "wx-test-app-id",
        "app_secret": "wechat-private-secret",
    }
    assert public_wechat_relay_test_account(db) == public
    assert db.list_official_accounts() == []


def test_relay_test_account_blank_secret_preserves_saved_value(tmp_path) -> None:
    db = Database(tmp_path / "relay-test-account-preserve.db")
    save_wechat_relay_test_account(
        db,
        name="测试号",
        app_id="wx-first",
        app_secret="first-private-secret",
    )

    save_wechat_relay_test_account(
        db,
        name="更新后的测试号",
        app_id="wx-second",
    )

    effective = effective_wechat_relay_test_account(db)
    assert effective["name"] == "更新后的测试号"
    assert effective["app_id"] == "wx-second"
    assert effective["app_secret"] == "first-private-secret"
