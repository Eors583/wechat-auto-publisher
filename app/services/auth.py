from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import Database

DEFAULT_ADMIN_USERNAME = "lanxue"
DEFAULT_ADMIN_PASSWORD = "lanxue"
SESSION_DAYS = 30
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_PBKDF2_ITERATIONS = 310_000


def _signup_credit_points() -> int:
    raw = str(os.getenv("WECHAT_PUBLISHER_SIGNUP_CREDITS") or "0").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERATIONS}$"
        f"{_b64encode(salt)}${_b64encode(digest)}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = str(encoded).split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(_b64encode(digest), expected)
    except (TypeError, ValueError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(user.get("id") or ""),
        "username": str(user.get("username") or ""),
        "role": str(user.get("role") or "user"),
        "enabled": bool(user.get("enabled")),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
    }


class AuthService:
    """Minimal local/hosted account service with opaque persisted sessions."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure_default_admin(self) -> dict[str, Any]:
        existing = self.db.get_user_by_username(DEFAULT_ADMIN_USERNAME)
        if existing:
            self.db.claim_legacy_customer_data(str(existing["id"]))
            return public_user(existing)
        try:
            created = self.db.create_user(
                username=DEFAULT_ADMIN_USERNAME,
                password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
                role="admin",
                enabled=True,
            )
        except Exception:
            # A second process may seed the same account concurrently.
            existing = self.db.get_user_by_username(DEFAULT_ADMIN_USERNAME)
            if not existing:
                raise
            created = existing
        self.db.claim_legacy_customer_data(str(created["id"]))
        return public_user(created)

    def register(self, username: str, password: str) -> dict[str, Any]:
        clean_username = self._validate_username(username)
        clean_password = self._validate_password(password)
        if self.db.get_user_by_username(clean_username):
            raise ValueError("该用户名已注册，请直接登录")
        try:
            user = self.db.create_user(
                username=clean_username,
                password_hash=hash_password(clean_password),
                role="user",
                enabled=True,
            )
        except Exception as exc:
            if self.db.get_user_by_username(clean_username):
                raise ValueError("该用户名已注册，请直接登录") from exc
            raise
        signup_points = _signup_credit_points()
        if signup_points:
            self.db.for_user(str(user["id"])).grant_credit_points(
                points=signup_points,
                source_type="signup",
                source_id="welcome",
                actor_user_id="system",
                reason="新用户体验积分",
            )
        return public_user(user)

    def login(self, username: str, password: str) -> dict[str, Any]:
        clean_username = str(username or "").strip()
        user = self.db.get_user_by_username(clean_username)
        if (
            not user
            or not bool(user.get("enabled"))
            or not verify_password(str(password or ""), str(user.get("password_hash") or ""))
        ):
            raise ValueError("用户名或密码不正确")
        token = secrets.token_urlsafe(40)
        expires_at = (_utc_now() + timedelta(days=SESSION_DAYS)).isoformat(
            timespec="microseconds"
        )
        self.db.create_user_session(
            token_hash=token_hash(token),
            user_id=str(user["id"]),
            expires_at=expires_at,
        )
        return {
            "token": token,
            "token_type": "bearer",
            "expires_at": expires_at,
            "user": public_user(user),
        }

    def authenticate(self, token: str | None) -> dict[str, Any] | None:
        clean_token = str(token or "").strip()
        if not clean_token:
            return None
        user = self.db.get_user_session(token_hash(clean_token))
        return public_user(user) if user else None

    def logout(self, token: str | None) -> None:
        clean_token = str(token or "").strip()
        if clean_token:
            self.db.delete_user_session(token_hash(clean_token))

    def list_users(self) -> list[dict[str, Any]]:
        return [public_user(item) for item in self.db.list_users()]

    def set_user_enabled(
        self,
        user_id: str,
        enabled: bool,
        *,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        user = self.db.get_user(str(user_id))
        if not user:
            raise ValueError("用户不存在")
        if (
            not enabled
            and actor_user_id
            and str(actor_user_id) == str(user_id)
        ):
            raise ValueError("不能停用当前登录账号")
        self.db.set_user_enabled(str(user_id), bool(enabled))
        updated = self.db.get_user(str(user_id))
        if not updated:
            raise ValueError("用户不存在")
        return public_user(updated)

    @staticmethod
    def _validate_username(username: str) -> str:
        clean = str(username or "").strip()
        if not _USERNAME_PATTERN.fullmatch(clean):
            raise ValueError("用户名需为 3～32 位字母、数字、点、横线或下划线")
        return clean

    @staticmethod
    def _validate_password(password: str) -> str:
        clean = str(password or "")
        if len(clean) < 6:
            raise ValueError("密码至少需要 6 位")
        if len(clean) > 128:
            raise ValueError("密码不能超过 128 位")
        return clean


__all__ = [
    "AuthService",
    "DEFAULT_ADMIN_PASSWORD",
    "DEFAULT_ADMIN_USERNAME",
    "hash_password",
    "public_user",
    "token_hash",
    "verify_password",
]
