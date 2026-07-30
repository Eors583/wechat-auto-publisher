from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.ai.model_registry import decrypt_api_key, encrypt_api_key
from app.db import Database

SETTING_KEY = "wechat_api_relay"
TEST_ACCOUNT_SETTING_KEY = "wechat_api_relay_test_account"
WECHAT_RELAY_ACCESS_CODE_VERSION = "wr1"
DEFAULT_WECHAT_RELAY_GATEWAY_URL = "https://bluebloodlab.cn/wechat-relay"
DEFAULT_WECHAT_RELAY_FIXED_EGRESS_IP = "47.99.126.8"

_ACCESS_CODE_CHECKSUM_LENGTH = 16
_ACCESS_CODE_MAX_LENGTH = 512
_BASE64URL_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_CHECKSUM_PATTERN = re.compile(rf"^[0-9a-f]{{{_ACCESS_CODE_CHECKSUM_LENGTH}}}$")
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ACCESS_CODE_ERROR = "中转接入码无效或已损坏，请重新复制完整接入码"


class WechatRelayAccessCodeError(ValueError):
    """Raised when a relay access code is malformed or fails its checksum."""


def public_wechat_relay_test_account(db: Database) -> dict[str, Any]:
    """Return the non-secret account used only for relay health checks."""

    stored = _load_test_account(db)
    return {
        "name": str(stored.get("name") or ""),
        "app_id": str(stored.get("app_id") or ""),
        "has_app_secret": bool(stored.get("app_secret_encrypted")),
    }


def save_wechat_relay_test_account(
    db: Database,
    *,
    name: str,
    app_id: str,
    app_secret: str | None = None,
    clear_app_secret: bool = False,
) -> dict[str, Any]:
    """Save a merchant-only WeChat account for read-only relay testing.

    This record is deliberately separate from ``official_accounts`` so it does
    not become a publishing target or require an article-model binding.
    """

    current = _load_test_account(db)
    clean_name = str(name or "").strip()
    clean_app_id = str(app_id or "").strip()
    supplied_secret = str(app_secret or "").strip()
    if clear_app_secret:
        current.pop("app_secret_encrypted", None)
    if supplied_secret:
        current["app_secret_encrypted"] = encrypt_api_key(supplied_secret)
    if not clean_name:
        raise ValueError("请填写测试公众号名称")
    if not clean_app_id:
        raise ValueError("请填写测试公众号 AppID")
    if not current.get("app_secret_encrypted"):
        raise ValueError("请填写测试公众号 AppSecret")
    current.update({"name": clean_name, "app_id": clean_app_id})
    db.set_setting(
        TEST_ACCOUNT_SETTING_KEY,
        json.dumps(current, ensure_ascii=False),
    )
    return public_wechat_relay_test_account(db)


def effective_wechat_relay_test_account(db: Database) -> dict[str, str]:
    """Return relay-test credentials for internal use without logging them."""

    stored = _load_test_account(db)
    encrypted = str(stored.get("app_secret_encrypted") or "")
    if not stored.get("app_id") or not encrypted:
        raise ValueError("请先配置中转测试公众号的 AppID 和 AppSecret")
    return {
        "name": str(stored.get("name") or "中转测试公众号"),
        "app_id": str(stored.get("app_id") or ""),
        "app_secret": decrypt_api_key(encrypted),
    }


def public_wechat_relay_connection_info() -> dict[str, str]:
    """Return the non-secret connection details shown in beginner flows."""

    return {
        "gateway_url": DEFAULT_WECHAT_RELAY_GATEWAY_URL,
        "fixed_egress_ip": DEFAULT_WECHAT_RELAY_FIXED_EGRESS_IP,
    }


def encode_wechat_relay_access_code(username: str, password: str) -> str:
    """Encode one customer's Basic Auth credentials as a versioned access code.

    The checksum protects against truncated or mistyped codes. It is deliberately
    not an authentication signature: possession of the code grants the same
    access as possession of the underlying Basic Auth credentials.
    """

    clean_username, clean_password = _validate_access_code_credentials(
        username,
        password,
    )
    username_segment = _base64url_encode(clean_username)
    password_segment = _base64url_encode(clean_password)
    payload = (
        f"{WECHAT_RELAY_ACCESS_CODE_VERSION}.{username_segment}.{password_segment}"
    )
    return f"{payload}.{_access_code_checksum(payload)}"


def decode_wechat_relay_access_code(access_code: str) -> dict[str, str]:
    """Decode and validate a relay access code without persisting or logging it."""

    try:
        code = str(access_code or "").strip()
        if not code or len(code) > _ACCESS_CODE_MAX_LENGTH:
            raise ValueError
        version, username_segment, password_segment, checksum = code.split(".")
        if version != WECHAT_RELAY_ACCESS_CODE_VERSION:
            raise ValueError
        if not _BASE64URL_SEGMENT_PATTERN.fullmatch(username_segment):
            raise ValueError
        if not _BASE64URL_SEGMENT_PATTERN.fullmatch(password_segment):
            raise ValueError
        if not _CHECKSUM_PATTERN.fullmatch(checksum):
            raise ValueError

        payload = f"{version}.{username_segment}.{password_segment}"
        expected = _access_code_checksum(payload)
        if not hmac.compare_digest(checksum, expected):
            raise ValueError

        username = _base64url_decode(username_segment)
        password = _base64url_decode(password_segment)
        clean_username, clean_password = _validate_access_code_credentials(
            username,
            password,
        )
        # Reject alternative/non-canonical encodings so a code has one stable
        # representation for checksum calculation and support diagnostics.
        if _base64url_encode(clean_username) != username_segment:
            raise ValueError
        if _base64url_encode(clean_password) != password_segment:
            raise ValueError
    except (TypeError, ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise WechatRelayAccessCodeError(_ACCESS_CODE_ERROR) from exc

    return {
        "username": clean_username,
        "password": clean_password,
    }


def save_wechat_relay_access_code(
    db: Database,
    access_code: str,
    *,
    enabled: bool = True,
    gateway_url: str = DEFAULT_WECHAT_RELAY_GATEWAY_URL,
) -> dict[str, Any]:
    """Apply an access code through the existing encrypted settings writer.

    Only the safe public settings view is returned. The original code and the
    decoded password are never written to the settings record or returned to
    callers.
    """

    credentials = decode_wechat_relay_access_code(access_code)
    save_wechat_relay_settings(
        db,
        enabled=enabled,
        gateway_url=gateway_url,
        username=credentials["username"],
        password=credentials["password"],
    )
    return public_wechat_relay_settings(db)


def public_wechat_relay_settings(db: Database) -> dict[str, Any]:
    """Return relay settings that are safe to render in a user interface."""

    stored = _load(db)
    return {
        "enabled": bool(stored.get("enabled", False)),
        "gateway_url": str(stored.get("gateway_url") or stored.get("base_url") or ""),
        "username": str(stored.get("username") or ""),
        "has_password": bool(stored.get("password_encrypted")),
    }


def save_wechat_relay_settings(
    db: Database,
    *,
    enabled: bool,
    gateway_url: str,
    username: str,
    password: str | None = None,
    clear_password: bool = False,
) -> None:
    """Persist the global WeChat API relay without exposing its password.

    A blank password preserves an already-saved password, matching the account,
    model and Feishu credential editors.
    """

    current = _load(db)
    normalized_url = _normalize_gateway_url(gateway_url)
    clean_username = str(username or "").strip()
    supplied_password = str(password or "").strip()
    if clear_password:
        current.pop("password_encrypted", None)
    if supplied_password:
        current["password_encrypted"] = encrypt_api_key(supplied_password)

    if enabled:
        if not normalized_url:
            raise ValueError("启用微信云中转时必须填写 HTTPS 中转地址")
        if not clean_username:
            raise ValueError("启用微信云中转时必须填写中转用户名")
        if not current.get("password_encrypted"):
            raise ValueError("启用微信云中转时必须填写中转密码")

    current.update(
        {
            "enabled": bool(enabled),
            "gateway_url": normalized_url,
            "username": clean_username,
        }
    )
    current.pop("base_url", None)
    db.set_setting(SETTING_KEY, json.dumps(current, ensure_ascii=False))
    invalidate = getattr(db, "invalidate_all_wechat_connection_health", None)
    if callable(invalidate):
        invalidate()


def effective_wechat_relay_settings(
    db: Database,
    config_fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return runtime relay credentials, preferring saved desktop settings."""

    stored = _load(db)
    if stored:
        password_encrypted = str(stored.get("password_encrypted") or "")
        result = {
            "enabled": bool(stored.get("enabled", False)),
            "gateway_url": _normalize_gateway_url(
                str(stored.get("gateway_url") or stored.get("base_url") or "")
            ),
            "username": str(stored.get("username") or "").strip(),
            "password": (
                decrypt_api_key(password_encrypted) if password_encrypted else ""
            ),
        }
    else:
        fallback = dict(config_fallback or {})
        result = {
            "enabled": bool(fallback.get("enabled", False)),
            "gateway_url": _normalize_gateway_url(
                str(fallback.get("gateway_url") or fallback.get("base_url") or "")
            ),
            "username": str(fallback.get("username") or "").strip(),
            "password": str(fallback.get("password") or "").strip(),
        }
    return validate_wechat_relay_settings(result)


def validate_wechat_relay_settings(
    settings: dict[str, Any] | None,
) -> dict[str, Any]:
    """Normalize a saved or temporary relay override for client construction."""

    value = dict(settings or {})
    result = {
        "enabled": bool(value.get("enabled", False)),
        "gateway_url": _normalize_gateway_url(
            str(value.get("gateway_url") or value.get("base_url") or "")
        ),
        "username": str(value.get("username") or "").strip(),
        "password": str(value.get("password") or "").strip(),
    }
    if result["enabled"]:
        if not result["gateway_url"]:
            raise ValueError("启用微信云中转时必须填写 HTTPS 中转地址")
        if not result["username"]:
            raise ValueError("启用微信云中转时必须填写中转用户名")
        if not result["password"]:
            raise ValueError("启用微信云中转时必须填写中转密码")
    return result


def _normalize_gateway_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("微信云中转地址格式不正确") from exc
    if parsed.scheme.casefold() != "https":
        raise ValueError("微信云中转地址必须使用 HTTPS")
    if not parsed.hostname:
        raise ValueError("微信云中转地址缺少有效域名")
    if parsed.hostname.casefold() == "api.weixin.qq.com":
        raise ValueError("微信云中转地址不能直接填写微信官方 API 域名")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("中转用户名和密码必须单独填写，不能写在地址中")
    if parsed.query or parsed.fragment:
        raise ValueError("微信云中转地址不能包含查询参数或片段")

    hostname = parsed.hostname.casefold()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = "/" + parsed.path.strip("/") if parsed.path.strip("/") else ""
    return urlunsplit(("https", netloc, path, "", ""))


def _validate_access_code_credentials(
    username: str,
    password: str,
) -> tuple[str, str]:
    clean_username = str(username or "").strip()
    clean_password = str(password or "")
    if not _USERNAME_PATTERN.fullmatch(clean_username):
        raise ValueError("中转用户名只能包含字母、数字、点、下划线和连字符")
    try:
        password_bytes = clean_password.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("中转密码必须使用可打印 ASCII 字符") from exc
    if not 16 <= len(password_bytes) <= 72:
        raise ValueError("中转密码长度必须为 16 至 72 个字符")
    if any(byte < 0x21 or byte > 0x7E for byte in password_bytes):
        raise ValueError("中转密码必须使用无空格的可打印 ASCII 字符")
    return clean_username, clean_password


def _base64url_encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _base64url_decode(segment: str) -> str:
    padding = "=" * (-len(segment) % 4)
    return base64.b64decode(
        segment + padding,
        altchars=b"-_",
        validate=True,
    ).decode("utf-8")


def _access_code_checksum(payload: str) -> str:
    return hashlib.sha256(payload.encode("ascii")).hexdigest()[
        :_ACCESS_CODE_CHECKSUM_LENGTH
    ]


def _load(db: Database) -> dict[str, Any]:
    raw = db.get_setting(SETTING_KEY)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _load_test_account(db: Database) -> dict[str, Any]:
    raw = db.get_setting(TEST_ACCOUNT_SETTING_KEY)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
