from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any


_DESCRIPTION = "BlueBloodLab Cockpit Bridge"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


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
    "LocalCredentialStore",
    "LocalSecureStateStore",
    "default_local_credential_dir",
    "protect_current_user",
    "unprotect_current_user",
]
