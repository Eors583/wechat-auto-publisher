from __future__ import annotations

import os

import pytest


# The production application is PostgreSQL-only. Fast unit tests may still use
# isolated temporary SQLite files, but this opt-in must never be set by runtime
# launchers or deployment configuration.
os.environ.setdefault("WECHAT_PUBLISHER_ALLOW_SQLITE_FOR_TESTS", "1")


@pytest.fixture(autouse=True)
def _isolate_legacy_sqlite_default(tmp_path, monkeypatch) -> None:
    """Never let a unit test recreate the retired workspace data/app.db."""

    monkeypatch.setenv(
        "WECHAT_PUBLISHER_TEST_SQLITE_PATH",
        str(tmp_path / "implicit-test-database.db"),
    )
