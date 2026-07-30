from types import SimpleNamespace

from urllib.request import ProxyHandler

from app.packaging_smoke import _api_route_paths, _open_local_url


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
