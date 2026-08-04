from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from app.db_backend import is_postgres_url

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
_TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_ENV_VALUES = frozenset({"0", "false", "no", "off"})


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            return os.getenv(match.group(1), "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _optional_env_bool(name: str) -> bool | None:
    """Return a strict boolean override, or ``None`` when it is not configured."""

    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    normalized = value.strip().casefold()
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False
    raise ValueError(
        f"环境变量 {name} 只能填写 "
        "true/false、1/0、yes/no 或 on/off"
    )


def _apply_env_overrides(config: dict[str, Any]) -> None:
    _optional_env_bool("AUTH_REQUIRED")
    current_auth = config.get("auth")
    if current_auth is None:
        current_auth = {}
    if not isinstance(current_auth, dict):
        raise ValueError("配置项 auth 必须是对象")
    # Multi-user customer data must never be reachable through the historical
    # unauthenticated compatibility mode. Keep parsing AUTH_REQUIRED so an
    # invalid value is still reported, but always fail closed.
    config["auth"] = {**current_auth, "required": True}

    feishu_enabled = _optional_env_bool("FEISHU_ENABLED")
    feishu_string_overrides = {
        field: str(os.getenv(env_name) or "").strip()
        for env_name, field in {
            "FEISHU_APP_ID": "app_id",
            "FEISHU_APP_SECRET": "app_secret",
            "FEISHU_VERIFICATION_TOKEN": "verification_token",
            "FEISHU_ENCRYPT_KEY": "encrypt_key",
        }.items()
        if str(os.getenv(env_name) or "").strip()
    }
    if feishu_enabled is not None or feishu_string_overrides:
        current = config.get("feishu")
        if current is None:
            current = {}
        if not isinstance(current, dict):
            raise ValueError("配置项 feishu 必须是对象")
        overrides: dict[str, Any] = dict(feishu_string_overrides)
        if feishu_enabled is not None:
            overrides["enabled"] = feishu_enabled
        config["feishu"] = {**current, **overrides}

    relay_enabled = _optional_env_bool("WECHAT_RELAY_ENABLED")
    relay_string_overrides = {
        field: str(os.getenv(env_name) or "").strip()
        for env_name, field in {
            "WECHAT_RELAY_URL": "gateway_url",
            "WECHAT_RELAY_USERNAME": "username",
            "WECHAT_RELAY_PASSWORD": "password",
        }.items()
        if str(os.getenv(env_name) or "").strip()
    }
    if relay_enabled is not None or relay_string_overrides:
        current = config.get("wechat_relay")
        if current is None:
            current = {}
        if not isinstance(current, dict):
            raise ValueError("配置项 wechat_relay 必须是对象")
        overrides = dict(relay_string_overrides)
        if relay_enabled is not None:
            overrides["enabled"] = relay_enabled
        config["wechat_relay"] = {**current, **overrides}


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    root = project_root()
    load_dotenv(root / ".env")
    path = Path(config_path) if config_path else root / "config.yaml"
    if not path.exists():
        example = root / "config.example.yaml"
        if not example.exists():
            raise FileNotFoundError(f"Config not found: {path}")
        path = example
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = _expand_env(raw)
    _apply_env_overrides(cfg)
    data_dir = root / str(cfg.get("data_dir", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    database_url = str(
        os.getenv("DATABASE_URL")
        or (cfg.get("db") or {}).get("url")
        or ""
    ).strip()
    allow_sqlite_tests = str(
        os.getenv("WECHAT_PUBLISHER_ALLOW_SQLITE_FOR_TESTS") or ""
    ).strip().casefold() in {"1", "true", "yes", "on"}
    legacy_db_path = ""
    if allow_sqlite_tests:
        db_rel = str(
            (cfg.get("db") or {}).get("path")
            or os.getenv("WECHAT_PUBLISHER_TEST_SQLITE_PATH")
            or ""
        ).strip()
        if db_rel:
            db_path = root / db_rel
            db_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_db_path = str(db_path)
    cfg["_root"] = str(root)
    # ``_db_path`` remains as a compatibility identity for in-process locks
    # and older callers. In application runtime it is the PostgreSQL URL, not
    # a path which can recreate data/app.db. Only explicitly opted-in tests
    # receive a local SQLite path.
    cfg["_db_path"] = database_url or legacy_db_path
    cfg["_database_url"] = database_url
    cfg["_db_target"] = database_url or legacy_db_path
    cfg["_data_dir"] = str(data_dir)
    return cfg


def database_target(config: dict[str, Any]) -> str:
    """Return the required PostgreSQL target.

    SQLite remains available only to isolated automated tests and the one-time
    legacy importer; application entry points must use PostgreSQL.
    """

    target = str(
        config.get("_database_url")
        or config.get("_db_target")
        or ""
    ).strip()
    if is_postgres_url(target):
        return target
    allow_sqlite = str(
        os.getenv("WECHAT_PUBLISHER_ALLOW_SQLITE_FOR_TESTS") or ""
    ).strip().casefold() in {"1", "true", "yes", "on"}
    if allow_sqlite:
        fallback = str(config.get("_db_path") or target).strip()
        if fallback:
            return fallback
    raise RuntimeError(
        "未配置 PostgreSQL。请设置 DATABASE_URL=postgresql://...；"
        "应用不再回退创建 data/app.db。"
    )
