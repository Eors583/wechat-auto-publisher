from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.services.batches import BatchService


def _client(tmp_path) -> TestClient:
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "followed-content-api.db"),
        "_db_target": str(tmp_path / "followed-content-api.db"),
        "_data_dir": str(tmp_path / "data"),
        "auth": {"required": True},
        "api": {"token": ""},
        "ai": {},
        "feishu": {"enabled": False},
        "wechat": {},
    }
    service = BatchService(config)
    return TestClient(create_api_app(config, service, start_feishu=False))


def _headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "lanxue", "password": "lanxue"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_account_query_requires_backend_session_then_returns_articles(
    tmp_path,
    monkeypatch,
) -> None:
    with _client(tmp_path) as client:
        headers = _headers(client)
        account = client.post(
            "/api/v1/followed-accounts",
            headers=headers,
            json={
                "name": "蓝血研究",
                "wechat_id": "lanxueyanjiu",
                "fetch_method": "backend_search",
            },
        )
        assert account.status_code == 200

        missing_session = client.post(
            f"/api/v1/followed-accounts/{account.json()['id']}/refresh",
            headers=headers,
        )
        assert missing_session.status_code == 422
        assert "Token" in missing_session.json()["detail"]

        saved = client.put(
            "/api/v1/followed-accounts/backend-session",
            headers=headers,
            json={
                "enabled": True,
                "token": "123456",
                "cookie": "session=test",
                "session_label": "接口回归",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["has_token"] is True
        assert saved.json()["has_cookie"] is True

        monkeypatch.setattr(
            "app.services.followed_content.search_backend_account_articles",
            lambda *_args, **_kwargs: [
                {
                    "title": "Token Cookie 查询到的文章",
                    "url": "https://mp.weixin.qq.com/s/backend-api-contract",
                    "snippet": "文章摘要",
                    "account_name": "蓝血研究",
                    "published_at": "2026-08-06T00:00:00+00:00",
                    "external_key": "backend-api-contract",
                }
            ],
        )
        refreshed = client.post(
            f"/api/v1/followed-accounts/{account.json()['id']}/refresh",
            headers=headers,
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["found"] == 1
        assert refreshed.json()["added"] == 1
        assert refreshed.json()["error"] == ""

        articles = client.get(
            "/api/v1/followed-articles?days=3650",
            headers=headers,
        )
        assert articles.status_code == 200
        assert articles.json()[0]["title"] == "Token Cookie 查询到的文章"
