from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


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
    data_dir = root / str(cfg.get("data_dir", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    db_rel = cfg.get("db", {}).get("path", "data/app.db")
    db_path = root / db_rel
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cfg["_root"] = str(root)
    cfg["_db_path"] = str(db_path)
    cfg["_data_dir"] = str(data_dir)
    return cfg
