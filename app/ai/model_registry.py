from __future__ import annotations

import base64
import ctypes
import hashlib
import ipaddress
import os
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

from app.ai.image_providers import (
    IMAGE_CUSTOM,
    IMAGE_PROVIDER_TYPES,
    is_image_provider,
    resolved_image_endpoint,
)
from app.db import Database
from app.services.url_validation import validate_external_url

OPENAI_COMPATIBLE = "openai_compatible"
LOCAL_OPENAI_COMPATIBLE = "local_openai_compatible"
GEMINI = "gemini"
MANUS = "manus"
OPENAI_IMAGE = IMAGE_CUSTOM
PROVIDER_TYPES = {
    OPENAI_COMPATIBLE,
    LOCAL_OPENAI_COMPATIBLE,
    GEMINI,
    MANUS,
    *IMAGE_PROVIDER_TYPES,
}
CONFIG_MODEL_PREFIX = "config:"

_CONFIG_MODEL_LABELS = {
    "manus": "Manus",
    "deepseek": "DeepSeek",
    "qwen": "通义千问",
    "moonshot": "Kimi / Moonshot",
    "zhipu": "智谱 GLM",
    "gemini": "Google Gemini",
    "openai": "OpenAI",
}


def configured_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose usable config.yaml/.env providers as read-only model records."""
    ai = config.get("ai") or {}
    primary = str(ai.get("primary") or "")
    fallback = str(ai.get("fallback") or "")
    ordered = list(dict.fromkeys([primary, fallback, *_CONFIG_MODEL_LABELS.keys()]))
    result: list[dict[str, Any]] = []
    for provider in ordered:
        cfg = ai.get(provider) or {}
        if not provider or not isinstance(cfg, dict) or not str(cfg.get("api_key") or "").strip():
            continue
        result.append(
            {
                "id": CONFIG_MODEL_PREFIX + provider,
                "provider": provider,
                "name": _CONFIG_MODEL_LABELS.get(provider, provider),
                "provider_type": (
                    GEMINI
                    if provider == "gemini"
                    else (MANUS if provider == "manus" else OPENAI_COMPATIBLE)
                ),
                "api_base": str(cfg.get("api_base") or ""),
                "model": str(cfg.get("model") or provider),
                "enabled": True,
                "has_api_key": True,
                "is_config_model": True,
                "is_default": provider == primary,
            }
        )
    return result


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _credential_fernet() -> Fernet | None:
    secret = str(os.getenv("CREDENTIAL_ENCRYPTION_KEY") or "").strip()
    if not secret:
        return None
    derived = hashlib.sha256(
        ("wechat-auto-publisher:credentials:v1:" + secret).encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_api_key(api_key: str) -> str:
    """Encrypt a credential with the server key or legacy Windows DPAPI.

    PostgreSQL-backed services require ``CREDENTIAL_ENCRYPTION_KEY`` so every
    container instance can decrypt the same record. DPAPI is retained only for
    one-time Windows migration compatibility; Linux never falls back to Base64.
    """
    raw = api_key.encode("utf-8")
    fernet = _credential_fernet()
    if fernet is not None:
        return "fernet:" + fernet.encrypt(raw).decode("ascii")
    if os.name != "nt":
        raise RuntimeError(
            "服务器未配置 CREDENTIAL_ENCRYPTION_KEY，不能保存敏感凭证"
        )
    in_blob, in_buffer = _blob(raw)
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob), "wechat-auto-publisher", None, None, None, 0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        del in_buffer


def decrypt_api_key(value: str) -> str:
    if value.startswith("fernet:"):
        fernet = _credential_fernet()
        if fernet is None:
            raise RuntimeError(
                "服务器未配置 CREDENTIAL_ENCRYPTION_KEY，无法读取敏感凭证"
            )
        try:
            return fernet.decrypt(value[7:].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError(
                "敏感凭证无法解密，请检查服务器加密密钥"
            ) from exc
    if value.startswith("base64:"):
        return base64.b64decode(value[7:]).decode("utf-8")
    if not value.startswith("dpapi:"):
        # Migration support for an early local record; new writes are encrypted.
        return value
    encrypted = base64.b64decode(value[6:])
    in_blob, in_buffer = _blob(encrypted)
    out_blob = _DataBlob()
    if os.name != "nt" or not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise RuntimeError("无法解密 API Key；它只能由保存它的 Windows 用户使用")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        del in_buffer


def save_model(
    db: Database,
    *,
    name: str,
    provider_type: str,
    api_base: str,
    model: str,
    api_key: str | None,
    enabled: bool = True,
    model_id: str | None = None,
) -> str:
    name = name.strip()
    model = model.strip()
    provider_type = provider_type.strip()
    if not name or not model:
        raise ValueError("显示名称和模型名称不能为空")
    if provider_type not in PROVIDER_TYPES:
        raise ValueError("不支持的接口类型")
    if provider_type == LOCAL_OPENAI_COMPATIBLE:
        if not db.owner_user_id:
            raise ValueError("本地模型必须由登录用户在公众号配置中添加")
        parsed = urlparse(api_base.strip())
        hostname = str(parsed.hostname or "").casefold()
        try:
            port = parsed.port
        except ValueError:
            port = None
        try:
            is_loopback = bool(
                hostname and ipaddress.ip_address(hostname).is_loopback
            )
        except ValueError:
            is_loopback = False
        if (
            parsed.scheme not in {"http", "https"}
            or not port
            or (
                hostname not in {"localhost", "localhost.localdomain"}
                and not is_loopback
            )
        ):
            raise ValueError(
                "本地模型地址必须是带端口的 localhost/127.0.0.1/::1 地址"
            )
        if str(api_key or "").strip().startswith("agt_codex_") and port == 11434:
            raise ValueError(
                "Cockpit Tools 密钥不能配合 Ollama 的 11434 地址使用；"
                "请填写 Cockpit Tools“服务配置”中显示的 API 地址，"
                "通常为 http://localhost:11797/v1"
            )
    if provider_type == MANUS:
        api_base = api_base.strip() or "https://api.manus.ai"
    if provider_type == OPENAI_COMPATIBLE and not api_base.strip():
        raise ValueError("OpenAI 兼容接口必须填写 API Base URL")
    if (
        db.owner_user_id
        and provider_type != LOCAL_OPENAI_COMPATIBLE
        and api_base.strip()
    ):
        if urlparse(api_base.strip()).scheme != "https":
            raise ValueError("个人模型的 API Base URL 必须使用 HTTPS")
        validate_external_url(api_base.strip())
    if is_image_provider(provider_type):
        api_base = resolved_image_endpoint(provider_type, api_base)
        if provider_type == OPENAI_IMAGE and not api_base:
            raise ValueError("自定义生图接口必须填写 API Base URL")
    existing = db.get_ai_model(model_id) if model_id else None
    encrypted = encrypt_api_key(api_key.strip()) if api_key and api_key.strip() else ""
    if (
        not encrypted
        and existing
        and str(existing.get("provider_type") or "") == provider_type
    ):
        encrypted = str(existing["api_key_encrypted"])
    if not encrypted and provider_type != LOCAL_OPENAI_COMPATIBLE:
        raise ValueError("API Key 不能为空")
    model_id = model_id or f"custom_{uuid.uuid4().hex[:12]}"
    db.upsert_ai_model(
        {
            "id": model_id,
            "name": name,
            "provider_type": provider_type,
            "api_base": api_base.strip(),
            "model": model,
            "api_key_encrypted": encrypted,
            "enabled": enabled,
            "created_at": existing.get("created_at") if existing else None,
        }
    )
    return model_id


def public_models(
    db: Database,
    enabled_only: bool = False,
    *,
    purpose: str | None = None,
) -> list[dict[str, Any]]:
    models = db.list_ai_models(enabled_only=enabled_only)
    if purpose == "text":
        models = [item for item in models if not is_image_provider(item.get("provider_type"))]
    elif purpose == "image":
        models = [item for item in models if is_image_provider(item.get("provider_type"))]
    for item in models:
        record_owner = str(item.pop("owner_user_id", "") or "")
        has_api_key = bool(str(item.get("api_key_encrypted") or ""))
        item.pop("api_key_encrypted", None)
        item["enabled"] = bool(item.get("enabled"))
        item["has_api_key"] = has_api_key
        item["scope"] = "private" if record_owner else "platform"
        item["editable"] = record_owner == db.owner_user_id
        item["connection_type"] = (
            "local"
            if item.get("provider_type") == LOCAL_OPENAI_COMPATIBLE
            else "api"
        )
    return models


def apply_model_selection(
    config: dict[str, Any],
    db: Database,
    primary_id: str | None,
    fallback_id: str | None = None,
) -> dict[str, Any]:
    """Return a config copy containing only the selected custom credentials."""
    if not primary_id:
        return config
    selected_ids = list(dict.fromkeys(x for x in (primary_id, fallback_id) if x))
    custom: dict[str, dict[str, Any]] = {}
    config_records = {item["id"]: item for item in configured_models(config)}
    for model_id in selected_ids:
        if model_id.startswith(CONFIG_MODEL_PREFIX):
            if model_id not in config_records:
                raise ValueError(f"配置模型未设置 API Key：{model_id}")
            continue
        record = db.get_ai_model(model_id)
        if not record or not bool(record.get("enabled")):
            raise ValueError(f"所选模型不可用或已停用：{model_id}")
        custom[model_id] = {
            "id": model_id,
            "name": record["name"],
            "provider_type": record["provider_type"],
            "api_base": record.get("api_base") or "",
            "model": record["model"],
            "api_key": decrypt_api_key(record["api_key_encrypted"]),
        }
    result = dict(config)
    ai = dict(result.get("ai") or {})
    if custom:
        ai["custom_models"] = custom

    def provider_key(model_id: str) -> str:
        if model_id.startswith(CONFIG_MODEL_PREFIX):
            return model_id[len(CONFIG_MODEL_PREFIX) :]
        return model_id

    ai["primary"] = provider_key(primary_id)
    ai["fallback"] = (
        provider_key(fallback_id)
        if fallback_id and fallback_id != primary_id
        else ""
    )
    result["ai"] = ai
    return result


def test_model_connection(db: Database, model_id: str) -> str:
    from app.ai.gemini import GeminiClient
    from app.ai.manus import ManusClient
    from app.ai.openai_compat import OpenAICompatClient

    record = db.get_ai_model(model_id)
    if not record:
        raise ValueError("模型不存在")
    key = decrypt_api_key(record["api_key_encrypted"])
    if record["provider_type"] == LOCAL_OPENAI_COMPATIBLE:
        client = build_text_client(db, {}, model_id)
        client.complete("只回复 OK", max_tokens=8, temperature=0, max_attempts=1)
    elif is_image_provider(record["provider_type"]):
        from app.ai.image_generator import test_image_endpoint

        return test_image_endpoint(
            api_key=key,
            api_base=record.get("api_base") or "",
            model=record["model"],
            provider_type=str(record.get("provider_type") or ""),
        )
    if record["provider_type"] == MANUS:
        client = ManusClient(
            api_key=key,
            api_base=record.get("api_base") or "https://api.manus.ai",
            model=record["model"] or "manus-1.6",
            # Manus is an asynchronous task API. Even a minimal probe can take
            # more than one minute after task creation, so a generic 60-second
            # connection timeout produces false negatives.
            timeout=180,
        )
        client.complete("只回复 OK")
    elif record["provider_type"] == GEMINI:
        client = GeminiClient(api_key=key, model=record["model"], timeout=30)
        client.complete("只回复 OK")
    else:
        client = OpenAICompatClient(
            api_key=key,
            api_base=record.get("api_base") or "",
            model=record["model"],
            provider_name=record["name"],
            timeout=30,
        )
        client.complete("只回复 OK", max_tokens=8, temperature=0, max_attempts=1)
    return "连接成功"


def generate_model_test_image(
    db: Database,
    model_id: str,
    output_dir: str | Path,
) -> Path:
    """Call the real image endpoint and return a locally normalized test image."""
    from app.ai.image_generator import generate_image

    record = db.get_ai_model(model_id)
    if not record or not is_image_provider(record.get("provider_type")):
        raise ValueError("所选配置不是生图智能体")
    if not bool(record.get("enabled")):
        raise ValueError("生图智能体已停用")
    target = Path(output_dir) / f"{model_id}_test.jpg"
    return generate_image(
        api_key=decrypt_api_key(str(record["api_key_encrypted"])),
        api_base=str(record.get("api_base") or ""),
        model=str(record.get("model") or ""),
        provider_type=str(record.get("provider_type") or ""),
        prompt=(
            "为微信公众号文章生成一张横版测试配图：现代企业管理团队在明亮会议室分析数据，"
            "纯写实商业新闻纪实摄影，构图克制清晰，不要文字、水印、二维码或品牌标识。"
        ),
        output_path=target,
    )


def build_text_client(
    db: Database,
    config: dict[str, Any],
    model_id: str,
) -> Any:
    """Build one configured text client for short assistant completions."""
    from app.ai.gemini import GeminiClient
    from app.ai.local_browser import LocalBrowserCompatClient
    from app.ai.manus import ManusClient
    from app.ai.openai_compat import OpenAICompatClient

    model_id = str(model_id or "").strip()
    if not model_id:
        raise ValueError("请选择飞书智能体模型")

    if model_id.startswith(CONFIG_MODEL_PREFIX):
        provider = model_id[len(CONFIG_MODEL_PREFIX) :]
        record = dict((config.get("ai") or {}).get(provider) or {})
        if not str(record.get("api_key") or "").strip():
            raise ValueError(f"智能体模型未配置 API Key：{model_id}")
        provider_type = (
            GEMINI
            if provider == "gemini"
            else (MANUS if provider == "manus" else OPENAI_COMPATIBLE)
        )
        name = _CONFIG_MODEL_LABELS.get(provider, provider)
        key = str(record.get("api_key") or "")
    else:
        stored = db.get_ai_model(model_id)
        if not stored or not bool(stored.get("enabled")):
            raise ValueError(f"智能体模型不可用或已停用：{model_id}")
        record = dict(stored)
        provider = model_id
        provider_type = str(record.get("provider_type") or OPENAI_COMPATIBLE)
        name = str(record.get("name") or model_id)
        key = decrypt_api_key(str(record.get("api_key_encrypted") or ""))

    if provider_type == LOCAL_OPENAI_COMPATIBLE:
        return LocalBrowserCompatClient(
            db=db,
            model_id=model_id,
            model=str(record.get("model") or ""),
            provider_name=name,
        )
    if provider == "manus" or provider_type == MANUS:
        # Manus runs a remote asynchronous task. Editorial reviews and long
        # generations regularly need more than the generic HTTP-style 120s
        # window, while the remote task is still legitimately ``running``.
        manus_timeout = max(
            600.0,
            float(record.get("timeout_seconds") or 600),
        )
        return ManusClient(
            api_key=key,
            api_base=str(record.get("api_base") or "https://api.manus.ai"),
            model=str(record.get("model") or "manus-1.6"),
            timeout=manus_timeout,
        )
    if provider_type == GEMINI:
        return GeminiClient(
            api_key=key,
            model=str(record.get("model") or "gemini-2.0-flash"),
            timeout=60,
        )
    return OpenAICompatClient(
        api_key=key,
        api_base=str(record.get("api_base") or ""),
        model=str(record.get("model") or provider),
        provider_name=name,
        timeout=60,
    )
