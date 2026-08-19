from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_DESCRIPTION = "BlueBloodLab Cockpit Bridge"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
DEFAULT_COCKPIT_API_BASE = "http://127.0.0.1:11797"
_ALLOWED_COCKPIT_HOSTS = {"127.0.0.1", "localhost", "::1"}


def normalize_cockpit_api_base(value: str) -> str:
    """Validate and normalize a user-configured loopback Cockpit API base."""

    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Cockpit URL 或端口格式无效") from exc
    hostname = str(parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "http":
        raise ValueError("Cockpit URL 必须使用 http://")
    if hostname not in _ALLOWED_COCKPIT_HOSTS:
        raise ValueError("Cockpit URL 只能使用 localhost、127.0.0.1 或 ::1")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Cockpit URL 不能包含账号或密码")
    if port is None:
        raise ValueError("Cockpit URL 必须填写端口")
    if parsed.query or parsed.fragment:
        raise ValueError("Cockpit URL 不能包含查询参数或片段")
    path = parsed.path.rstrip("/")
    if path not in {"", "/v1"}:
        raise ValueError("Cockpit URL 只能填写服务地址，可选以 /v1 结尾")
    display_host = f"[{hostname}]" if hostname == "::1" else hostname
    return f"http://{display_host}:{port}"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        ),
        buffer,
    )


def protect_current_user(value: str) -> bytes:
    """Encrypt text for the current Windows user with DPAPI."""

    if os.name != "nt":
        raise RuntimeError("本机 Cockpit 密钥存储目前只支持 Windows")
    raw = str(value or "").encode("utf-8")
    if not raw:
        raise ValueError("Cockpit API Key 不能为空")
    in_blob, in_buffer = _blob(raw)
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        _DESCRIPTION,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        del in_buffer


def unprotect_current_user(value: bytes) -> str:
    """Decrypt a DPAPI blob created by :func:`protect_current_user`."""

    if os.name != "nt":
        raise RuntimeError("本机 Cockpit 密钥存储目前只支持 Windows")
    if not value:
        return ""
    in_blob, in_buffer = _blob(bytes(value))
    out_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    ):
        raise RuntimeError("本机 Cockpit API Key 无法由当前 Windows 用户解密")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        del in_buffer


def default_local_credential_dir() -> Path:
    local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "BlueBloodLab" / "CockpitBridge"


class LocalCredentialStore:
    """Keep the Cockpit key on this Windows account, never on production."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory else default_local_credential_dir()
        self.path = self.directory / "cockpit-key.dpapi"
        self.api_base_path = self.directory / "cockpit-api-base.txt"

    def configured(self) -> bool:
        return self.path.is_file() and self.path.stat().st_size > 0

    def save_api_key(self, api_key: str) -> None:
        encrypted = protect_current_user(str(api_key or "").strip())
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    def load_api_key(self) -> str:
        if not self.configured():
            return ""
        return unprotect_current_user(self.path.read_bytes()).strip()

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def load_cockpit_api_base(self) -> str:
        if not self.api_base_path.is_file():
            return DEFAULT_COCKPIT_API_BASE
        try:
            return normalize_cockpit_api_base(
                self.api_base_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValueError):
            return DEFAULT_COCKPIT_API_BASE

    def save_cockpit_api_base(self, api_base: str) -> None:
        normalized = normalize_cockpit_api_base(api_base)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.api_base_path.with_suffix(".tmp")
        temporary.write_text(normalized, encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.api_base_path)


class LocalSecureStateStore:
    """Persist companion state as one CurrentUser-DPAPI protected JSON blob."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory else default_local_credential_dir()
        self.path = self.directory / "agent-state.dpapi"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = unprotect_current_user(self.path.read_bytes())
            value = json.loads(raw)
        except Exception as exc:
            raise RuntimeError("本机 Companion 状态无法解密或已经损坏") from exc
        if not isinstance(value, dict):
            raise RuntimeError("本机 Companion 状态格式无效")
        return dict(value)

    def save(self, state: dict[str, Any]) -> None:
        raw = json.dumps(dict(state), ensure_ascii=False, separators=(",", ":"))
        encrypted = protect_current_user(raw)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


__all__ = [
    "DEFAULT_COCKPIT_API_BASE",
    "LocalCredentialStore",
    "LocalSecureStateStore",
    "default_local_credential_dir",
    "normalize_cockpit_api_base",
    "protect_current_user",
    "unprotect_current_user",
]
