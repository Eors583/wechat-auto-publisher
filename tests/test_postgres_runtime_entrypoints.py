from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app import launcher
from app.admin import server as admin_server
from app.ui import desktop
from app.ui import server as ui_server


@pytest.mark.parametrize(
    "module",
    [ui_server, desktop, admin_server],
    ids=["web-ui", "desktop", "admin"],
)
def test_ui_entrypoints_validate_database_before_starting_nicegui(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
) -> None:
    calls: list[str] = []
    config = {"_database_url": "postgresql://db/publisher"}

    monkeypatch.setattr(module, "load_config", lambda: config)

    def validate(received: dict[str, Any]) -> str:
        assert received is config
        calls.append("database")
        return str(received["_database_url"])

    monkeypatch.setattr(module, "database_target", validate)
    monkeypatch.setattr(
        module.ui,
        "run",
        lambda **_kwargs: calls.append("nicegui"),
    )

    module.main()

    assert calls == ["database", "nicegui"]


@pytest.mark.parametrize(
    "module",
    [ui_server, desktop, admin_server],
    ids=["web-ui", "desktop", "admin"],
)
def test_ui_entrypoints_do_not_start_nicegui_without_postgres(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
) -> None:
    monkeypatch.setattr(module, "load_config", dict)
    monkeypatch.setattr(
        module,
        "database_target",
        lambda _config: (_ for _ in ()).throw(
            RuntimeError("未配置 PostgreSQL。请设置 DATABASE_URL=postgresql://...")
        ),
    )
    monkeypatch.setattr(
        module.ui,
        "run",
        lambda **_kwargs: pytest.fail("NiceGUI must not start"),
    )

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        module.main()


def _prepare_launcher(
    monkeypatch: pytest.MonkeyPatch,
    *,
    argv: list[str],
) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(launcher.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(launcher, "_ensure_standard_streams", lambda: None)
    monkeypatch.setattr(launcher, "_configure_file_logging", lambda _name: None)


def test_launcher_remote_mode_does_not_require_a_local_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_launcher(
        monkeypatch,
        argv=["publisher", "--remote-url", "https://publisher.example"],
    )
    monkeypatch.setattr(
        launcher,
        "load_config",
        lambda: pytest.fail("remote mode must not load local configuration"),
    )
    monkeypatch.setattr(
        launcher,
        "database_target",
        lambda _config: pytest.fail("remote mode must not require PostgreSQL"),
    )
    opened: list[str] = []
    monkeypatch.setattr(
        launcher,
        "_run_remote_desktop",
        lambda url: opened.append(url) or 0,
    )

    assert launcher.main() == 0
    assert opened == ["https://publisher.example"]


def test_launcher_local_mode_reports_database_failure_before_starting_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_launcher(monkeypatch, argv=["publisher"])
    monkeypatch.setattr(launcher, "_remote_ui_url", lambda: "")
    monkeypatch.setattr(launcher, "load_config", dict)
    monkeypatch.setattr(
        launcher,
        "database_target",
        lambda _config: (_ for _ in ()).throw(
            RuntimeError("未配置 PostgreSQL。请设置 DATABASE_URL=postgresql://...")
        ),
    )
    monkeypatch.setattr(
        launcher,
        "_api_port",
        lambda: pytest.fail("API startup must not be reached"),
    )
    warnings: list[str] = []
    monkeypatch.setattr(launcher, "_show_warning", warnings.append)

    assert launcher.main() == 2
    assert warnings == [
        "本地模式启动失败：未配置 PostgreSQL。请设置 DATABASE_URL=postgresql://..."
    ]


def test_list_drafts_uses_the_configured_database_target() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "_list_drafts.py"
    ).read_text(encoding="utf-8")

    assert "Database(database_target(cfg))" in source
    assert 'Database(cfg["_db_path"])' not in source
