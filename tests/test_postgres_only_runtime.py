from __future__ import annotations

from pathlib import Path

import pytest

from app.config import database_target
from app.db import Database
from scripts.migrate_sqlite_to_postgres import (
    BUSINESS_TABLES,
    SKIPPED_EPHEMERAL_TABLES,
)


def test_runtime_rejects_sqlite_without_explicit_test_opt_in(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("WECHAT_PUBLISHER_ALLOW_SQLITE_FOR_TESTS", raising=False)

    with pytest.raises(RuntimeError, match="PostgreSQL-only"):
        Database(tmp_path / "runtime.db")
    with pytest.raises(RuntimeError, match="未配置 PostgreSQL"):
        database_target({"_db_path": str(tmp_path / "runtime.db")})


def test_legacy_import_contract_includes_user_settings_and_skips_sessions() -> None:
    assert "user_settings" in BUSINESS_TABLES
    assert set(SKIPPED_EPHEMERAL_TABLES) == {
        "token_cache",
        "user_sessions",
    }
