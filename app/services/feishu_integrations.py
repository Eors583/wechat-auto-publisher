from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from app.ai.model_registry import decrypt_api_key, encrypt_api_key
from app.ai.image_providers import is_image_provider
from app.db import Database
from app.services.configuration import ConfigurationService
from app.services.failures import sanitize_failure_text


FEISHU_TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)
PAIRING_HASH_ITERATIONS = 120_000
PAIRING_MAX_FAILURES = 5


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _hash_pairing_code(code: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        str(code).encode("utf-8"),
        bytes.fromhex(str(salt)),
        max(1, int(iterations)),
    ).hex()


class FeishuIntegrationService:
    """Security boundary for one authenticated user's own Feishu app.

    The callback lookup is intentionally unscoped because it happens before a
    Feishu request can be authenticated. Every business operation after that
    lookup uses a fresh ``Database.for_user(owner_user_id)``.
    """

    def __init__(self, db: Database, config: dict[str, Any] | None = None) -> None:
        self.db = db
        self.config = dict(config or {})
        self.configuration = ConfigurationService(db, self.config)

    def public(self, *, callback_base_url: str = "") -> dict[str, Any]:
        row = self.db.get_feishu_integration()
        if not row:
            return {
                "configured": False,
                "enabled": False,
                "status": "unconfigured",
                "account_ids": [],
                "default_account_id": "",
                "runtime": {"status": "stopped"},
            }
        accounts = self.db.list_feishu_integration_accounts(str(row["id"]))
        callback_path = f'/api/feishu/events/{row["callback_key"]}'
        try:
            runtime = json.loads(str(row.get("runtime_json") or "{}"))
        except json.JSONDecodeError:
            runtime = {}
        bound_open_id = str(row.get("bound_open_id") or "")
        return {
            "configured": True,
            "id": str(row["id"]),
            "enabled": bool(row.get("enabled")),
            "status": str(row.get("status") or "waiting_pairing"),
            "app_id": str(row.get("app_id") or ""),
            "has_app_secret": bool(row.get("app_secret_encrypted")),
            "has_verification_token": bool(row.get("verification_token_encrypted")),
            "has_encrypt_key": bool(row.get("encrypt_key_encrypted")),
            "callback_path": callback_path,
            "callback_url": f'{callback_base_url.rstrip("/")}{callback_path}'
            if callback_base_url
            else callback_path,
            "agent_model_id": str(row.get("agent_model_id") or ""),
            "account_ids": [str(item["account_id"]) for item in accounts],
            "default_account_id": next(
                (
                    str(item["account_id"])
                    for item in accounts
                    if bool(item.get("is_default"))
                ),
                "",
            ),
            "bound": bool(bound_open_id),
            "bound_open_id_masked": self._mask_identifier(bound_open_id),
            "pairing": self.pairing_status(row=row),
            "runtime": runtime if isinstance(runtime, dict) else {},
        }

    def migrate_legacy_global(self, default_admin_user_id: str) -> bool:
        """Move one historical global config only to the original admin.

        The migrated robot is deliberately disabled and unbound. Legacy
        allow-all/chat allowlists are never copied.
        """

        marker = "migration.feishu_integrations_owner.v1"
        if self.db.get_setting(marker):
            return False
        raw = self.db.get_setting("feishu_integration")
        owner_db = self.db.for_user(default_admin_user_id)
        migrated = False
        if raw and not owner_db.get_feishu_integration():
            try:
                legacy = json.loads(raw)
            except json.JSONDecodeError:
                legacy = {}
            if isinstance(legacy, dict):
                account_ids = [
                    str(item).strip()
                    for item in legacy.get("default_account_ids") or []
                    if str(item).strip()
                ]
                if (
                    legacy.get("app_id")
                    and legacy.get("app_secret_encrypted")
                    and legacy.get("agent_model_id")
                    and account_ids
                ):
                    try:
                        owner_db.save_feishu_integration(
                            app_id=str(legacy["app_id"]),
                            app_secret_encrypted=str(
                                legacy["app_secret_encrypted"]
                            ),
                            verification_token_encrypted=str(
                                legacy.get("verification_token_encrypted") or ""
                            ),
                            encrypt_key_encrypted=str(
                                legacy.get("encrypt_key_encrypted") or ""
                            ),
                            callback_key=secrets.token_urlsafe(32),
                            agent_model_id=str(legacy["agent_model_id"]),
                            account_ids=account_ids,
                            default_account_id=account_ids[0],
                            enabled=False,
                        )
                        owner_db.set_feishu_integration_enabled(False)
                        migrated = True
                    except (ValueError, KeyError):
                        # Preserve the dormant legacy JSON for manual recovery;
                        # never attach invalid accounts to another customer.
                        migrated = False
        self.db.set_setting(marker, "migrated" if migrated else "not_applicable")
        return migrated

    def save(
        self,
        *,
        app_id: str,
        app_secret: str | None,
        verification_token: str | None,
        encrypt_key: str | None,
        agent_model_id: str,
        account_ids: list[str],
        default_account_id: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        current = self.db.get_feishu_integration() or {}
        clean_app_id = str(app_id or "").strip()
        if not clean_app_id:
            raise ValueError("请填写飞书 App ID")
        model_id = str(agent_model_id or "").strip()
        if not model_id:
            raise ValueError("请选择机器人使用的文本模型")
        model = self.configuration.get_model(model_id)
        if is_image_provider(str(model.get("provider_type") or "")):
            raise ValueError("飞书机器人必须使用文本模型")

        app_changed = bool(current.get("app_id")) and str(
            current.get("app_id")
        ) != clean_app_id
        if app_changed and not all(
            str(value or "").strip()
            for value in (app_secret, verification_token, encrypt_key)
        ):
            raise ValueError("更换 App ID 时必须重新填写全部飞书密钥")

        encrypted: dict[str, str] = {}
        for field, supplied in {
            "app_secret": app_secret,
            "verification_token": verification_token,
            "encrypt_key": encrypt_key,
        }.items():
            clean = str(supplied or "").strip()
            stored = str(current.get(f"{field}_encrypted") or "")
            encrypted[field] = encrypt_api_key(clean) if clean else stored
            if enabled and not encrypted[field]:
                raise ValueError(f"启用机器人前请填写 {self._field_label(field)}")

        callback_key = str(current.get("callback_key") or secrets.token_urlsafe(32))
        try:
            self.db.save_feishu_integration(
                app_id=clean_app_id,
                app_secret_encrypted=encrypted["app_secret"],
                verification_token_encrypted=encrypted["verification_token"],
                encrypt_key_encrypted=encrypted["encrypt_key"],
                callback_key=callback_key,
                agent_model_id=model_id,
                account_ids=[str(item) for item in account_ids],
                default_account_id=str(default_account_id or "").strip(),
                enabled=bool(enabled),
            )
            if app_changed:
                self.db.unbind_feishu_integration()
        except Exception as exc:
            if "app_id" in str(exc).casefold() or "unique" in str(exc).casefold():
                raise ValueError("该飞书 App ID 已被其他系统用户配置") from exc
            raise
        return self.public()

    def test_credentials(
        self,
        *,
        app_id: str = "",
        app_secret: str | None = None,
        post: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        current = self.effective_for_owner()
        clean_app_id = str(app_id or current.get("app_id") or "").strip()
        clean_secret = str(app_secret or current.get("app_secret") or "").strip()
        if not clean_app_id or not clean_secret:
            raise ValueError("请填写飞书 App ID 和 App Secret")
        sender = post or httpx.post
        try:
            response = sender(
                FEISHU_TOKEN_URL,
                json={"app_id": clean_app_id, "app_secret": clean_secret},
                timeout=15,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RuntimeError("无法连接飞书开放平台，请检查网络后重试") from exc
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"飞书返回了无法识别的结果（HTTP {response.status_code}）"
            ) from exc
        if response.status_code >= 400 or int(payload.get("code") or 0) != 0:
            code = payload.get("code", response.status_code)
            message = sanitize_failure_text(
                payload.get("msg") or "App ID 或 App Secret 无效"
            )
            raise ValueError(f"飞书凭证验证失败（{code}）：{message}")
        return {"ok": True, "app_id": clean_app_id, "message": "飞书凭证验证成功"}

    def create_pairing_code(self, *, ttl_minutes: int = 10) -> dict[str, Any]:
        row = self.db.get_feishu_integration()
        if not row or not bool(row.get("enabled")):
            raise ValueError("请先保存并启用自己的飞书机器人")
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_hex(16)
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=max(5, min(10, int(ttl_minutes)))
        )
        self.db.set_feishu_pairing(
            str(row["id"]),
            salt=salt,
            code_hash=_hash_pairing_code(code, salt, PAIRING_HASH_ITERATIONS),
            iterations=PAIRING_HASH_ITERATIONS,
            expires_at=expires_at.isoformat(timespec="microseconds"),
        )
        return {
            "code": code,
            "message": f"绑定 {code}",
            "expires_at": expires_at.isoformat(timespec="seconds"),
        }

    def consume_pairing_code(
        self,
        integration_id: str,
        *,
        text: str,
        open_id: str,
        chat_id: str,
    ) -> bool:
        row = self.db.get_feishu_integration()
        if not row or str(row.get("id")) != str(integration_id):
            return False
        supplied = self._pairing_code(text)
        if not supplied or int(row.get("pairing_failed_attempts") or 0) >= PAIRING_MAX_FAILURES:
            return False
        salt = str(row.get("pairing_salt") or "")
        expected = str(row.get("pairing_code_hash") or "")
        iterations = int(row.get("pairing_iterations") or PAIRING_HASH_ITERATIONS)
        if not salt or not expected:
            return False
        actual = _hash_pairing_code(supplied, salt, iterations)
        if not hmac.compare_digest(actual, expected):
            self.db.fail_feishu_pairing(integration_id)
            return False
        clean_open_id = str(open_id or "").strip()
        clean_chat_id = str(chat_id or "").strip()
        if not clean_open_id or not clean_chat_id:
            return False
        return self.db.consume_feishu_pairing(
            integration_id,
            expected_code_hash=expected,
            open_id=clean_open_id,
            chat_id=clean_chat_id,
        )

    def pairing_status(self, *, row: dict[str, Any] | None = None) -> dict[str, Any]:
        current = row if row is not None else self.db.get_feishu_integration()
        if not current:
            return {"status": "none"}
        if current.get("bound_open_id"):
            return {
                "status": "used",
                "bound_open_id_masked": self._mask_identifier(
                    str(current.get("bound_open_id") or "")
                ),
                "used_at": str(current.get("pairing_used_at") or ""),
            }
        if not current.get("pairing_code_hash"):
            return {"status": "none"}
        if int(current.get("pairing_failed_attempts") or 0) >= PAIRING_MAX_FAILURES:
            return {"status": "locked"}
        try:
            expires_at = datetime.fromisoformat(
                str(current.get("pairing_expires_at") or "")
            )
        except ValueError:
            return {"status": "invalid"}
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return {
            "status": "expired"
            if expires_at <= datetime.now(timezone.utc)
            else "waiting",
            "expires_at": expires_at.isoformat(timespec="seconds"),
            "failed_attempts": int(current.get("pairing_failed_attempts") or 0),
        }

    def unbind(self) -> dict[str, Any]:
        self.db.unbind_feishu_integration()
        return self.public()

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if enabled:
            row = self.effective_for_owner()
            if not row:
                raise ValueError("请先保存并验证自己的飞书机器人")
            for field, label in (
                ("app_id", "App ID"),
                ("app_secret", "App Secret"),
                ("verification_token", "Verification Token"),
                ("encrypt_key", "Encrypt Key"),
                ("agent_model_id", "文本模型"),
            ):
                if not str(row.get(field) or "").strip():
                    raise ValueError(f"启用机器人前请补全 {label}")
            accounts = self.db.list_feishu_integration_accounts(
                str(row.get("id") or "")
            )
            if not accounts or any(not bool(item.get("enabled")) for item in accounts):
                raise ValueError("启用机器人前请重新选择当前可用的公众号")
            if sum(bool(item.get("is_default")) for item in accounts) != 1:
                raise ValueError("启用机器人前请设置唯一默认公众号")
            model = self.configuration.get_model(
                str(row.get("agent_model_id") or "")
            )
            if is_image_provider(str(model.get("provider_type") or "")):
                raise ValueError("飞书机器人必须使用文本模型")
        self.db.set_feishu_integration_enabled(enabled)
        return self.public()

    def effective_for_owner(self) -> dict[str, Any]:
        row = self.db.get_feishu_integration()
        if not row:
            return {}
        return self._effective(row)

    def effective_for_callback(self, callback_key: str) -> dict[str, Any]:
        row = self.db.get_feishu_integration_by_callback_key(callback_key)
        if not row:
            return {}
        return self._effective(row)

    def _effective(self, row: dict[str, Any]) -> dict[str, Any]:
        owner_db = self.db.for_user(str(row.get("owner_user_id") or ""))
        accounts = owner_db.list_feishu_integration_accounts(str(row["id"]))
        return {
            **dict(row),
            "app_secret": decrypt_api_key(str(row.get("app_secret_encrypted") or "")),
            "verification_token": decrypt_api_key(
                str(row.get("verification_token_encrypted") or "")
            ),
            "encrypt_key": decrypt_api_key(str(row.get("encrypt_key_encrypted") or "")),
            "default_account_ids": [
                str(item["account_id"])
                for item in accounts
                if bool(item.get("is_default"))
            ],
            "allowed_account_ids": [str(item["account_id"]) for item in accounts],
        }

    @staticmethod
    def _pairing_code(text: str) -> str:
        clean = "".join(str(text or "").strip().split())
        if clean.startswith("绑定"):
            clean = clean[2:]
        return clean if len(clean) == 6 and clean.isdigit() else ""

    @staticmethod
    def _mask_identifier(value: str) -> str:
        clean = str(value or "")
        if len(clean) <= 8:
            return "已绑定" if clean else ""
        return f"{clean[:4]}…{clean[-4:]}"

    @staticmethod
    def _field_label(field: str) -> str:
        return {
            "app_secret": "App Secret",
            "verification_token": "Verification Token",
            "encrypt_key": "Encrypt Key",
        }[field]


__all__ = [
    "FEISHU_TOKEN_URL",
    "PAIRING_HASH_ITERATIONS",
    "PAIRING_MAX_FAILURES",
    "FeishuIntegrationService",
]
