from __future__ import annotations

import pytest

from app.local_credentials import (
    DEFAULT_COCKPIT_API_BASE,
    LocalCredentialStore,
    normalize_cockpit_api_base,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:21888", "http://127.0.0.1:21888"),
        ("http://localhost:34567/v1/", "http://localhost:34567"),
        ("http://[::1]:45678/v1", "http://[::1]:45678"),
    ],
)
def test_normalize_cockpit_api_base_accepts_loopback_custom_ports(
    value: str,
    expected: str,
) -> None:
    assert normalize_cockpit_api_base(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:11797",
        "http://192.168.1.20:11797",
        "http://example.com:11797",
        "http://user:secret@127.0.0.1:11797",
        "http://127.0.0.1",
        "http://127.0.0.1:11797/admin",
        "http://127.0.0.1:11797?x=1",
    ],
)
def test_normalize_cockpit_api_base_rejects_unsafe_targets(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_cockpit_api_base(value)


def test_local_store_persists_cockpit_base_and_defaults_safely(tmp_path) -> None:
    store = LocalCredentialStore(tmp_path)
    assert store.load_cockpit_api_base() == DEFAULT_COCKPIT_API_BASE

    store.save_cockpit_api_base("http://127.0.0.1:29123/v1")
    assert store.load_cockpit_api_base() == "http://127.0.0.1:29123"

    store.api_base_path.write_text("http://evil.example:9999", encoding="utf-8")
    assert store.load_cockpit_api_base() == DEFAULT_COCKPIT_API_BASE
