from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.services.batches import BatchService


def _client(tmp_path) -> TestClient:
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "configuration-api.db"),
        "_db_target": str(tmp_path / "configuration-api.db"),
        "_data_dir": str(tmp_path / "data"),
        "auth": {"required": True},
        "api": {"token": ""},
        "ai": {},
        "feishu": {"enabled": False},
        "wechat": {},
    }
    service = BatchService(config)
    return TestClient(create_api_app(config, service, start_feishu=False))


def _admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "lanxue", "password": "lanxue"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_element_settings_configuration_contract(tmp_path) -> None:
    with _client(tmp_path) as client:
        headers = _admin_headers(client)

        model = client.post(
            "/api/v1/admin/models",
            headers=headers,
            json={
                "name": "文章模型",
                "provider_type": "openai_compatible",
                "api_base": "https://example.test/v1",
                "model": "writer-v1",
                "api_key": "secret-key",
                "enabled": True,
            },
        )
        assert model.status_code == 200

        account = client.post(
            "/api/v1/configuration/accounts",
            headers=headers,
            json={
                "name": "蓝血经营管理系统",
                "app_id": "wx-element-test",
                "app_secret": "wechat-secret",
                "model_id": model.json()["id"],
                "review_priority": 80,
                "enabled": True,
            },
        )
        assert account.status_code == 200
        assert account.json()["review_priority"] == 80
        assert "app_secret_encrypted" not in account.json()
        assert account.json()["has_app_secret"] is True

        builtin = client.put(
            f"/api/v1/configuration/accounts/{account.json()['id']}/creation-plan",
            headers=headers,
            json={"plan_id": "builtin:default"},
        )
        assert builtin.status_code == 200
        assert builtin.json()["plan_id"] == "builtin:default"

        prompt = client.post(
            "/api/v1/configuration/prompt-templates",
            headers=headers,
            json={
                "name": "深度文章",
                "purpose": "article",
                "content": "保留事实，输出结构化长文。",
                "enabled": True,
            },
        )
        assert prompt.status_code == 200

        prompt_binding = client.put(
            f"/api/v1/configuration/accounts/{account.json()['id']}/prompts/article",
            headers=headers,
            json={"template_id": prompt.json()["id"]},
        )
        assert prompt_binding.status_code == 200
        assert prompt_binding.json()["selected_prompt"]["template_id"] == prompt.json()["id"]

        layout = client.put(
            f"/api/v1/configuration/accounts/{account.json()['id']}/layout",
            headers=headers,
            json={"layout": {"paragraph_break_mode": "each_line"}},
        )
        assert layout.status_code == 200
        assert layout.json()["layout"]["paragraph_break_mode"] == "each_line"

        plan = client.post(
            "/api/v1/configuration/creation-plans",
            headers=headers,
            json={
                "name": "深度经营方案",
                "description": "用于测试 Element Plus 设置页的完整保存链路",
                "article_prompt_template_id": prompt.json()["id"],
                "layout": {"paragraph_break_mode": "blank_line"},
                "image_settings": {},
                "enabled": True,
            },
        )
        assert plan.status_code == 200

        applied = client.put(
            f"/api/v1/configuration/accounts/{account.json()['id']}/creation-plan",
            headers=headers,
            json={"plan_id": plan.json()["id"]},
        )
        assert applied.status_code == 200
        assert applied.json()["plan_id"] == plan.json()["id"]

        feishu = client.put(
            "/api/v1/configuration/feishu",
            headers=headers,
            json={
                "enabled": False,
                "app_id": "",
                "allowed_open_ids": [],
                "allowed_chat_ids": [],
                "default_account_ids": [account.json()["id"]],
                "agent_model_id": model.json()["id"],
            },
        )
        assert feishu.status_code == 200
        assert feishu.json()["settings"]["default_account_ids"] == [
            account.json()["id"]
        ]
        pairing = client.get(
            "/api/v1/configuration/feishu/pairing",
            headers=headers,
        )
        assert pairing.status_code == 200
        assert pairing.json()["status"] == "none"

        relay = client.put(
            "/api/v1/configuration/wechat-relay",
            headers=headers,
            json={
                "enabled": False,
                "gateway_url": "https://bluebloodlab.cn/wechat-relay",
                "username": "",
            },
        )
        assert relay.status_code == 200
        assert relay.json()["settings"]["gateway_url"].startswith("https://")


def test_configuration_and_user_state_require_admin(tmp_path) -> None:
    with _client(tmp_path) as client:
        admin_headers = _admin_headers(client)
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "element_user", "password": "secret123"},
        )
        assert registered.status_code == 200
        user_headers = {
            "Authorization": f"Bearer {registered.json()['token']}"
        }

        assert client.get(
            "/api/v1/configuration/accounts", headers=user_headers
        ).status_code == 403

        user_id = registered.json()["user"]["id"]
        disabled = client.patch(
            f"/api/v1/admin/users/{user_id}",
            headers=admin_headers,
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
