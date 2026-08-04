from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.db import Database, customer_data_scope
from app.services.auth import AuthService
from app.services.batches import BatchService


def _account(account_id: str, name: str) -> dict[str, object]:
    return {
        "id": account_id,
        "name": name,
        "app_id": f"wx-{account_id}",
        "app_secret_encrypted": "encrypted",
        "model_id": "",
        "layout": {"body": {"font_size": "16px"}},
        "enabled": True,
    }


def _users(db: Database) -> tuple[dict[str, object], dict[str, object]]:
    auth = AuthService(db)
    alice = auth.register("alice-user", "secret1")
    bob = auth.register("bob-user", "secret2")
    return alice, bob


def test_customer_configuration_follows_login_and_is_isolated(tmp_path) -> None:
    path = tmp_path / "publisher.db"
    root = Database(path)
    alice, bob = _users(root)

    alice_db = root.for_user(str(alice["id"]))
    alice_db.upsert_official_account(_account("account-alice", "Alice公众号"))
    alice_db.upsert_prompt_template(
        {
            "id": "prompt-alice",
            "name": "Alice文章模板",
            "purpose": "article",
            "content": "仅属于 Alice",
            "enabled": True,
        }
    )
    alice_db.upsert_creation_plan(
        {
            "id": "plan-alice",
            "name": "Alice创作方案",
            "layout": {},
            "image_settings": {},
            "enabled": True,
        }
    )
    alice_db.set_user_setting(
        "ui.last_target_account_ids",
        json.dumps(["account-alice"]),
    )

    # A new Database object represents the same user logging in on another
    # device. Server-side PostgreSQL/SQLite data is still available.
    alice_other_device = Database(path, owner_user_id=str(alice["id"]))
    assert [item["id"] for item in alice_other_device.list_official_accounts()] == [
        "account-alice"
    ]
    assert [item["id"] for item in alice_other_device.list_prompt_templates()] == [
        "prompt-alice"
    ]
    assert [item["id"] for item in alice_other_device.list_creation_plans()] == [
        "plan-alice"
    ]
    assert json.loads(
        alice_other_device.get_user_setting("ui.last_target_account_ids") or "[]"
    ) == ["account-alice"]

    bob_db = root.for_user(str(bob["id"]))
    assert bob_db.list_official_accounts() == []
    assert bob_db.list_prompt_templates() == []
    assert bob_db.list_creation_plans() == []
    assert bob_db.get_user_setting("ui.last_target_account_ids") is None
    assert bob_db.get_official_account("account-alice") is None

    with pytest.raises(ValueError, match="不属于当前登录账号"):
        bob_db.upsert_official_account(_account("account-alice", "越权修改"))


def test_batches_and_jobs_are_private_but_models_are_platform_shared(tmp_path) -> None:
    path = tmp_path / "publisher.db"
    root = Database(path)
    alice, bob = _users(root)
    alice_db = root.for_user(str(alice["id"]))
    bob_db = root.for_user(str(bob["id"]))

    root.upsert_ai_model(
        {
            "id": "platform-model",
            "name": "平台模型",
            "provider_type": "openai_compatible",
            "model": "merchant-model",
            "api_key_encrypted": "encrypted",
            "enabled": True,
        }
    )
    assert alice_db.get_ai_model("platform-model") is not None
    assert bob_db.get_ai_model("platform-model") is not None

    alice_db.upsert_official_account(_account("account-alice", "Alice公众号"))
    job_id = alice_db.create_job(topic="私有任务")
    alice_db.create_batch("batch-alice", topic="私有批次")
    alice_db.attach_batch_job(
        "batch-alice",
        job_id,
        "account-alice",
        "Alice公众号",
    )

    assert alice_db.get_batch("batch-alice") is not None
    assert alice_db.get_job(job_id) is not None
    assert bob_db.get_batch("batch-alice") is None
    assert bob_db.get_job(job_id) is None
    assert bob_db.list_batches() == []

    # The HTTP API uses a request context with shared service instances.
    with customer_data_scope(str(alice["id"])):
        assert Database(path).get_batch("batch-alice") is not None
    with customer_data_scope(str(bob["id"])):
        assert Database(path).get_batch("batch-alice") is None


def test_legacy_customer_data_is_claimed_only_by_default_admin(tmp_path) -> None:
    root = Database(tmp_path / "publisher.db")
    root.upsert_official_account(_account("legacy-account", "历史公众号"))
    root.upsert_prompt_template(
        {
            "id": "legacy-prompt",
            "name": "历史提示词",
            "purpose": "article",
            "content": "历史内容",
            "enabled": True,
        }
    )

    auth = AuthService(root)
    admin = auth.ensure_default_admin()
    customer = auth.register("new-customer", "secret1")

    admin_db = root.for_user(str(admin["id"]))
    customer_db = root.for_user(str(customer["id"]))
    assert admin_db.get_official_account("legacy-account") is not None
    assert admin_db.get_prompt_template("legacy-prompt") is not None
    assert customer_db.get_official_account("legacy-account") is None
    assert customer_db.get_prompt_template("legacy-prompt") is None


def test_legacy_customer_settings_are_migrated_only_to_default_admin(
    tmp_path,
) -> None:
    root = Database(tmp_path / "publisher.db")
    expected = {
        "wechat_backend_search": json.dumps(
            {"token": "legacy-token", "cookie": "legacy-cookie"}
        ),
        "onboarding.guide": json.dumps({"current_step": "wechat"}),
        "ui.last_target_account_ids": json.dumps(["legacy-account"]),
        "ui.review_mode": "quick",
    }
    for key, value in expected.items():
        root.set_setting(key, value)

    # Simulate an installation where business rows were already claimed by an
    # earlier release, but user settings did not have their own migration.
    root.set_setting("migration.customer_data_owner.v1", "legacy-admin")

    auth = AuthService(root)
    admin = auth.ensure_default_admin()
    customer = auth.register("settings-customer", "secret1")
    admin_db = root.for_user(str(admin["id"]))
    customer_db = root.for_user(str(customer["id"]))

    for key, value in expected.items():
        assert admin_db.get_setting(key) == value
        assert admin_db.get_user_setting(key) == value
        assert customer_db.get_setting(key) is None
        assert customer_db.get_user_setting(key) is None

    # A rerun must preserve a value the administrator has changed since the
    # legacy migration.
    admin_db.set_setting("onboarding.guide", '{"current_step":"complete"}')
    auth.ensure_default_admin()
    assert admin_db.get_setting("onboarding.guide") == (
        '{"current_step":"complete"}'
    )


def test_http_api_uses_authenticated_customer_scope(tmp_path) -> None:
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "api.db"),
        "_db_target": str(tmp_path / "api.db"),
        "_data_dir": str(tmp_path / "data"),
        "auth": {"required": True},
        "api": {"token": ""},
        "ai": {},
        "feishu": {"enabled": False},
        "wechat": {},
    }
    service = BatchService(config)
    app = create_api_app(config, service, start_feishu=False)

    with TestClient(app) as client:
        alice_login = client.post(
            "/api/v1/auth/register",
            json={"username": "api-alice", "password": "secret1"},
        ).json()
        bob_login = client.post(
            "/api/v1/auth/register",
            json={"username": "api-bob", "password": "secret2"},
        ).json()
        service.db.for_user(str(alice_login["user"]["id"])).upsert_official_account(
            _account("api-alice-account", "API Alice公众号")
        )

        alice_accounts = client.get(
            "/api/v1/accounts",
            headers={"Authorization": f"Bearer {alice_login['token']}"},
        )
        bob_accounts = client.get(
            "/api/v1/accounts",
            headers={"Authorization": f"Bearer {bob_login['token']}"},
        )

    assert alice_accounts.status_code == 200
    assert [item["id"] for item in alice_accounts.json()] == [
        "api-alice-account"
    ]
    assert bob_accounts.status_code == 200
    assert bob_accounts.json() == []


def test_http_api_rejects_unauthenticated_compatibility_mode(tmp_path) -> None:
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "api.db"),
        "_db_target": str(tmp_path / "api.db"),
        "_data_dir": str(tmp_path / "data"),
        "auth": {"required": False},
        "api": {"token": ""},
        "ai": {},
        "feishu": {"enabled": False},
        "wechat": {},
    }
    service = BatchService(config)
    app = create_api_app(config, service, start_feishu=False)

    admin = AuthService(service.db).ensure_default_admin()
    service.db.for_user(str(admin["id"])).upsert_official_account(
        _account("private-admin-account", "管理员公众号")
    )

    with TestClient(app) as client:
        anonymous = client.get("/api/v1/accounts")
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "lanxue", "password": "lanxue"},
        )
        authenticated = client.get(
            "/api/v1/accounts",
            headers={
                "Authorization": f"Bearer {login.json()['token']}"
            },
        )

    assert anonymous.status_code == 401
    assert authenticated.status_code == 200
    assert [item["id"] for item in authenticated.json()] == [
        "private-admin-account"
    ]


def test_http_api_initializes_topic_sources_per_authenticated_user(
    tmp_path,
) -> None:
    config = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "api.db"),
        "_db_target": str(tmp_path / "api.db"),
        "_data_dir": str(tmp_path / "data"),
        "auth": {"required": True},
        "api": {"token": ""},
        "ai": {},
        "topics": {},
        "feishu": {"enabled": False},
        "wechat": {},
    }
    service = BatchService(config)
    app = create_api_app(config, service, start_feishu=False)

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/auth/register",
            json={"username": "topic-api-a", "password": "secret1"},
        ).json()
        second = client.post(
            "/api/v1/auth/register",
            json={"username": "topic-api-b", "password": "secret2"},
        ).json()
        first_sources = client.get(
            "/api/v1/topic-sources",
            headers={"Authorization": f"Bearer {first['token']}"},
        )
        second_sources = client.get(
            "/api/v1/topic-sources",
            headers={"Authorization": f"Bearer {second['token']}"},
        )

    assert first_sources.status_code == 200
    assert second_sources.status_code == 200
    first_items = first_sources.json()
    second_items = second_sources.json()
    assert {item["source_key"] for item in first_items} == {
        item["source_key"] for item in second_items
    }
    assert {item["id"] for item in first_items}.isdisjoint(
        {item["id"] for item in second_items}
    )
