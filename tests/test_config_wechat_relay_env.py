from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import load_config


def _write_config(
    tmp_path: Path,
    *,
    relay_enabled: bool | None,
) -> Path:
    config_path = tmp_path / "config.yaml"
    lines = [
        f"data_dir: {json.dumps(str(tmp_path / 'data'))}",
        "db:",
        f"  path: {json.dumps(str(tmp_path / 'data' / 'app.db'))}",
    ]
    if relay_enabled is not None:
        lines.extend(
            [
                "wechat_relay:",
                f"  enabled: {'true' if relay_enabled else 'false'}",
                "  gateway_url: https://relay.from-yaml.example/wechat",
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
def test_wechat_relay_enabled_env_uses_strict_boolean_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: bool,
) -> None:
    config_path = _write_config(tmp_path, relay_enabled=not expected)
    monkeypatch.setenv("WECHAT_RELAY_ENABLED", raw)

    config = load_config(config_path)

    assert config["wechat_relay"]["enabled"] is expected
    assert isinstance(config["wechat_relay"]["enabled"], bool)


def test_wechat_relay_env_creates_missing_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, relay_enabled=None)
    monkeypatch.setenv("WECHAT_RELAY_ENABLED", "true")
    monkeypatch.setenv(
        "WECHAT_RELAY_URL",
        "https://bluebloodlab.cn/wechat-relay",
    )
    monkeypatch.setenv("WECHAT_RELAY_USERNAME", "customer-001")
    monkeypatch.setenv("WECHAT_RELAY_PASSWORD", "private-password")

    config = load_config(config_path)

    assert config["wechat_relay"] == {
        "enabled": True,
        "gateway_url": "https://bluebloodlab.cn/wechat-relay",
        "username": "customer-001",
        "password": "private-password",
    }


def test_blank_wechat_relay_env_does_not_erase_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, relay_enabled=True)
    monkeypatch.setenv("WECHAT_RELAY_URL", " ")
    monkeypatch.setenv("WECHAT_RELAY_USERNAME", "")

    config = load_config(config_path)

    assert (
        config["wechat_relay"]["gateway_url"]
        == "https://relay.from-yaml.example/wechat"
    )


def test_invalid_wechat_relay_enabled_env_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path, relay_enabled=False)
    monkeypatch.setenv("WECHAT_RELAY_ENABLED", "enabled")

    with pytest.raises(ValueError, match="WECHAT_RELAY_ENABLED"):
        load_config(config_path)
