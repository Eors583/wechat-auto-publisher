from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import load_config
from app.db import Database
from app.feishu.settings import effective_feishu_settings


def _write_config(
    tmp_path: Path,
    *,
    feishu_enabled: bool | None,
) -> Path:
    config_path = tmp_path / "config.yaml"
    lines = [
        f"data_dir: {json.dumps(str(tmp_path / 'data'))}",
        "db:",
        f"  path: {json.dumps(str(tmp_path / 'data' / 'app.db'))}",
    ]
    if feishu_enabled is not None:
        lines.extend(
            [
                "feishu:",
                f"  enabled: {'true' if feishu_enabled else 'false'}",
            ]
        )
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        (" TRUE ", True),
        ("1", True),
        ("yes", True),
        ("ON", True),
        ("false", False),
        (" FALSE ", False),
        ("0", False),
        ("no", False),
        ("OFF", False),
    ],
)
def test_feishu_enabled_env_uses_strict_boolean_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: bool,
) -> None:
    config_path = _write_config(tmp_path, feishu_enabled=not expected)
    monkeypatch.setenv("FEISHU_ENABLED", raw)

    config = load_config(config_path)

    assert config["feishu"]["enabled"] is expected
    assert isinstance(config["feishu"]["enabled"], bool)


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_feishu_enabled_env_preserves_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    config_path = _write_config(tmp_path, feishu_enabled=True)
    monkeypatch.setenv("FEISHU_ENABLED", raw)

    config = load_config(config_path)

    assert config["feishu"]["enabled"] is True


def test_unset_feishu_enabled_env_preserves_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, feishu_enabled=False)
    monkeypatch.delenv("FEISHU_ENABLED", raising=False)

    config = load_config(config_path)

    assert config["feishu"]["enabled"] is False


def test_feishu_enabled_env_creates_missing_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, feishu_enabled=None)
    monkeypatch.setenv("FEISHU_ENABLED", "true")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_from_env")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret-from-env")

    config = load_config(config_path)

    assert config["feishu"] == {
        "enabled": True,
        "app_id": "cli_from_env",
        "app_secret": "secret-from-env",
    }


def test_blank_feishu_credential_env_does_not_erase_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, feishu_enabled=True)
    with config_path.open("a", encoding="utf-8") as file:
        file.write("  app_id: cli_from_yaml\n")
    monkeypatch.setenv("FEISHU_APP_ID", " ")

    config = load_config(config_path)

    assert config["feishu"]["app_id"] == "cli_from_yaml"


def test_invalid_feishu_enabled_env_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, feishu_enabled=False)
    monkeypatch.setenv("FEISHU_ENABLED", "enabled")

    with pytest.raises(ValueError, match="FEISHU_ENABLED"):
        load_config(config_path)


def test_saved_feishu_settings_remain_higher_priority_than_env_and_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, feishu_enabled=True)
    monkeypatch.setenv("FEISHU_ENABLED", "true")
    config = load_config(config_path)
    db = Database(config["_db_path"])
    db.set_setting(
        "feishu_integration",
        json.dumps(
            {
                "enabled": False,
                "app_id": "cli_saved",
                "agent_model_id": "",
            }
        ),
    )

    effective = effective_feishu_settings(db, config["feishu"])

    assert config["feishu"]["enabled"] is True
    assert effective["enabled"] is False
