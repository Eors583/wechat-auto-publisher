import inspect
from types import SimpleNamespace
from urllib.request import ProxyHandler

import pytest

from app.packaging_smoke import (
    _api_route_paths,
    _open_local_url,
    _packaging_remote_url,
    _runtime_storage_contract,
    run_packaging_self_test,
)


def test_api_route_paths_ignores_framework_internal_router_entries() -> None:
    application = SimpleNamespace(
        routes=[
            SimpleNamespace(path="/health"),
            SimpleNamespace(),
            SimpleNamespace(path="/api/v1/accounts"),
        ]
    )

    assert _api_route_paths(application) == {
        "/health",
        "/api/v1/accounts",
    }


def test_open_local_url_bypasses_environment_proxy(monkeypatch) -> None:
    opened: dict[str, object] = {}

    class FakeOpener:
        def open(self, url: str, *, timeout: float) -> object:
            opened.update(url=url, timeout=timeout)
            return object()

    def fake_build_opener(handler: object) -> FakeOpener:
        opened["handler"] = handler
        return FakeOpener()

    monkeypatch.setattr("app.packaging_smoke.build_opener", fake_build_opener)

    result = _open_local_url("http://127.0.0.1:18765/", timeout=3.5)

    assert result is not None
    assert opened["url"] == "http://127.0.0.1:18765/"
    assert opened["timeout"] == 3.5
    assert isinstance(opened["handler"], ProxyHandler)
    assert opened["handler"].proxies == {}


def test_packaging_remote_url_accepts_installer_argument(monkeypatch) -> None:
    monkeypatch.setenv("WECHAT_PUBLISHER_REMOTE_URL", "https://ignored.example")

    assert _packaging_remote_url(
        [
            "--self-test",
            "--remote-url",
            "https://api.bluebloodlab.cn/publisher/",
        ]
    ) == "https://api.bluebloodlab.cn/publisher"


def test_packaging_remote_url_rejects_invalid_address() -> None:
    with pytest.raises(ValueError, match="HTTP"):
        _packaging_remote_url(["--remote-url", "not-a-url"])


def test_remote_storage_contract_does_not_require_database() -> None:
    detail = _runtime_storage_contract({}, "https://publisher.example")

    assert detail == "remote-client=https://publisher.example"


def test_postgres_storage_contract_only_validates_driver_and_address() -> None:
    detail = _runtime_storage_contract(
        {"_database_url": "postgresql://user:secret@db.internal/publisher"},
        "",
    )

    assert detail == "postgresql=db.internal/publisher"


def test_packaging_self_test_has_no_sqlite_schema_probe() -> None:
    source = inspect.getsource(run_packaging_self_test)

    assert "sqlite_master" not in source
    assert "PRAGMA" not in source
