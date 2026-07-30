from types import SimpleNamespace

from app.packaging_smoke import _api_route_paths


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
