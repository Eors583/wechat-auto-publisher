from __future__ import annotations

import json

from app.accounts import (
    IMPORTED_BENCHMARK_ACCOUNT_ID,
    IMPORTED_DEFAULT_ACCOUNT_ID,
    apply_account_selection,
    ensure_account_layouts_initialized,
    ensure_config_account_imported,
    ensure_config_accounts_imported,
    public_accounts,
    save_account,
    save_account_layout,
)
from app.ai.failover import FailoverRewriter
from app.ai.image_providers import IMAGE_MINIMAX
from app.ai.model_registry import (
    MANUS,
    OPENAI_COMPATIBLE,
    apply_model_selection,
    build_text_client,
    configured_models,
    decrypt_api_key,
    public_models,
    save_model,
)
from app.ai.model_registry import (
    test_model_connection as probe_model_connection,
)
from app.db import Database


def test_model_crud_keeps_api_key_private(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    model_id = save_model(
        db,
        name="我的 DeepSeek",
        provider_type=OPENAI_COMPATIBLE,
        api_base="https://api.deepseek.com",
        model="deepseek-chat",
        api_key="sk-private-value",
    )

    stored = db.get_ai_model(model_id)
    assert stored is not None
    assert "sk-private-value" not in stored["api_key_encrypted"]
    assert decrypt_api_key(stored["api_key_encrypted"]) == "sk-private-value"
    assert "api_key_encrypted" not in public_models(db)[0]

    save_model(
        db,
        model_id=model_id,
        name="改名后的模型",
        provider_type=OPENAI_COMPATIBLE,
        api_base="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key=None,
    )
    updated = db.get_ai_model(model_id)
    assert updated is not None
    assert decrypt_api_key(updated["api_key_encrypted"]) == "sk-private-value"


def test_server_credential_key_uses_portable_authenticated_encryption(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "test-only-server-credential-key-with-sufficient-length",
    )

    from app.ai.model_registry import encrypt_api_key

    encrypted = encrypt_api_key("portable-private-value")

    assert encrypted.startswith("fernet:")
    assert "portable-private-value" not in encrypted
    assert decrypt_api_key(encrypted) == "portable-private-value"


def test_image_vendor_template_fills_endpoint_and_stays_out_of_text_models(tmp_path) -> None:
    db = Database(tmp_path / "image-model.db")
    model_id = save_model(
        db,
        name="MiniMax 配图",
        provider_type=IMAGE_MINIMAX,
        api_base="",
        model="image-01",
        api_key="minimax-secret",
    )

    stored = db.get_ai_model(model_id)
    assert stored is not None
    assert stored["api_base"] == "https://api.minimaxi.com/v1/image_generation"
    assert [item["id"] for item in public_models(db, purpose="image")] == [model_id]
    assert public_models(db, purpose="text") == []


def test_selected_custom_model_is_available_to_failover(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    model_id = save_model(
        db,
        name="自定义网关",
        provider_type=OPENAI_COMPATIBLE,
        api_base="https://llm.example.test/v1",
        model="company-model",
        api_key="test-key",
    )
    config = apply_model_selection(
        {"ai": {"max_retries_per_model": 1}}, db, model_id, model_id
    )

    rewriter = FailoverRewriter(config)
    assert rewriter.primary == model_id
    assert rewriter.fallback == ""
    client = rewriter._clients[model_id]
    assert client.api_base == "https://llm.example.test/v1"
    assert client.model == "company-model"
    assert client.api_key == "test-key"


def test_manus_can_be_saved_and_selected_without_env_file(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    model_id = save_model(
        db,
        name="运营 Manus",
        provider_type=MANUS,
        api_base="",
        model="manus-1.6",
        api_key="manus-private-key",
    )

    stored = db.get_ai_model(model_id)
    assert stored is not None
    assert stored["api_base"] == "https://api.manus.ai"

    config = apply_model_selection({"ai": {}}, db, model_id, model_id)
    rewriter = FailoverRewriter(config)
    client = rewriter._clients[model_id]
    assert client.api_key == "manus-private-key"
    assert client.api_base == "https://api.manus.ai"
    assert client.model == "manus-1.6"

    assistant_client = build_text_client(db, config, model_id)
    assert assistant_client.api_key == "manus-private-key"
    assert assistant_client.api_base == "https://api.manus.ai"


def test_manus_connection_probe_allows_async_task_completion(
    tmp_path,
    monkeypatch,
) -> None:
    db = Database(tmp_path / "manus-probe.db")
    model_id = save_model(
        db,
        name="Manus probe",
        provider_type=MANUS,
        api_base="https://api.manus.ai",
        model="manus-1.6",
        api_key="manus-private-key",
    )
    captured: dict[str, object] = {}

    class FakeManusClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def complete(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return "OK"

    monkeypatch.setattr("app.ai.manus.ManusClient", FakeManusClient)

    assert probe_model_connection(db, model_id) == "连接成功"
    assert captured["timeout"] == 180
    assert captured["prompt"] == "只回复 OK"


def test_account_has_one_model_and_injects_its_own_credentials(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    model_id = save_model(
        db,
        name="账号专用模型",
        provider_type=OPENAI_COMPATIBLE,
        api_base="https://llm.example.test/v1",
        model="account-model",
        api_key="model-secret",
    )
    account_id = save_account(
        db,
        name="品牌公众号",
        app_id="wx-account-one",
        app_secret="wechat-secret",
        model_id=model_id,
    )

    visible = public_accounts(db)[0]
    assert visible["model_id"] == model_id
    assert visible["model_name"] == "账号专用模型"
    assert "app_secret_encrypted" not in visible

    config, account = apply_account_selection(
        {"ai": {}, "wechat": {"author": "运营"}}, db, account_id
    )
    assert account["name"] == "品牌公众号"
    assert config["wechat"]["app_id"] == "wx-account-one"
    assert config["wechat"]["app_secret"] == "wechat-secret"
    assert config["wechat"]["author"] == "运营"
    assert config["ai"]["primary"] == model_id
    assert config["ai"]["fallback"] == ""

    save_account(
        db,
        account_id=account_id,
        name="品牌公众号（新名称）",
        app_id="wx-account-one",
        app_secret=None,
        model_id=model_id,
    )
    edited_config, _ = apply_account_selection({}, db, account_id)
    assert edited_config["wechat"]["app_secret"] == "wechat-secret"


def test_each_account_keeps_and_applies_its_own_layout(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    model_id = save_model(
        db,
        name="排版测试模型",
        provider_type=OPENAI_COMPATIBLE,
        api_base="https://llm.example.test/v1",
        model="layout-model",
        api_key="model-secret",
    )
    first_id = save_account(
        db,
        name="账号甲",
        app_id="wx-layout-a",
        app_secret="secret-a",
        model_id=model_id,
    )
    second_id = save_account(
        db,
        name="账号乙",
        app_id="wx-layout-b",
        app_secret="secret-b",
        model_id=model_id,
    )
    save_account_layout(
        db,
        first_id,
        {
            "body": {"font_size": "18px", "first_line_indent": "2em"},
            "argument": {"color": "#123456"},
            "editor_template": {"enabled": True},
        },
    )
    save_account_layout(
        db,
        second_id,
        {"body": {"font_size": "15px"}, "argument": {"color": "#abcdef"}},
    )

    first_config, _ = apply_account_selection({"ai": {}}, db, first_id)
    second_config, _ = apply_account_selection({"ai": {}}, db, second_id)

    assert first_config["template"]["body_font_size"] == "18px"
    assert first_config["template"]["body_first_line_indent"] == "2em"
    assert first_config["template"]["argument_color"] == "#123456"
    assert second_config["template"]["body_font_size"] == "15px"
    assert second_config["template"]["argument_color"] == "#abcdef"
    assert first_config["editor_template"]["snapshot_path"].endswith(f"{first_id}.html")
    assert second_config["editor_template"]["snapshot_path"].endswith(f"{second_id}.html")
    assert all(item["has_custom_layout"] for item in public_accounts(db))

    # Editing credentials or the bound model must not erase the saved layout.
    save_account(
        db,
        account_id=first_id,
        name="账号甲（改名）",
        app_id="wx-layout-a",
        app_secret=None,
        model_id=model_id,
    )
    preserved, _ = apply_account_selection({"ai": {}}, db, first_id)
    assert preserved["template"]["body_font_size"] == "18px"


def test_cannot_delete_model_bound_to_account(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    model_id = save_model(
        db,
        name="绑定模型",
        provider_type=OPENAI_COMPATIBLE,
        api_base="https://llm.example.test/v1",
        model="bound-model",
        api_key="model-secret",
    )
    save_account(
        db,
        name="公众号 A",
        app_id="wx-a",
        app_secret="wechat-secret",
        model_id=model_id,
    )

    try:
        db.delete_ai_model(model_id)
    except ValueError as exc:
        assert "公众号 A" in str(exc)
    else:
        raise AssertionError("bound model deletion should fail")


def test_env_configured_primary_remains_available_only_for_legacy_runtime(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    config = {
        "ai": {
            "primary": "manus",
            "fallback": "manus",
            "manus": {
                "api_key": "env-manus-key",
                "api_base": "https://api.manus.ai",
                "model": "manus-1.6",
            },
        },
        "wechat": {},
    }
    items = configured_models(config)
    assert items[0]["id"] == "config:manus"
    assert items[0]["is_default"] is True

    selected = apply_model_selection(config, db, "config:manus", "config:manus")
    assert selected["ai"]["primary"] == "manus"
    assert "custom_models" not in selected["ai"]

    assert public_models(db, purpose="text") == []


def test_existing_env_wechat_account_is_imported_only_once(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    merchant_model_id = save_model(
        db,
        name="后台 Manus",
        provider_type=MANUS,
        api_base="https://api.manus.ai",
        model="manus-1.6",
        api_key="merchant-manus-key",
    )
    db.set_setting("merchant.default_text_model_id", merchant_model_id)
    config = {
        "ai": {
            "primary": "manus",
            "manus": {"api_key": "manus-key", "model": "manus-1.6"},
        },
        "wechat": {"app_id": "wx-existing", "app_secret": "existing-secret"},
    }
    first = ensure_config_account_imported(db, config)
    second = ensure_config_account_imported(db, config)

    assert first == IMPORTED_DEFAULT_ACCOUNT_ID
    assert second == IMPORTED_DEFAULT_ACCOUNT_ID
    accounts = db.list_official_accounts()
    assert len(accounts) == 1
    assert accounts[0]["name"] == "默认公众号"
    assert accounts[0]["model_id"] == merchant_model_id


def test_publish_and_benchmark_accounts_are_both_imported_with_names(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    config = {
        "ai": {
            "primary": "manus",
            "manus": {"api_key": "manus-key", "model": "manus-1.6"},
        },
        "wechat": {
            "account_name": "蓝血经营管理系统",
            "app_id": "wx-publisher",
            "app_secret": "publisher-secret",
        },
        "benchmark": {
            "name": "蓝血研究",
            "app_id": "wx-benchmark",
            "app_secret": "benchmark-secret",
        },
    }
    imported = ensure_config_accounts_imported(db, config)
    assert imported == [IMPORTED_DEFAULT_ACCOUNT_ID, IMPORTED_BENCHMARK_ACCOUNT_ID]
    names = {item["name"] for item in db.list_official_accounts()}
    assert names == {"蓝血经营管理系统", "蓝血研究"}


def test_legacy_accounts_receive_independent_layouts_and_template_copies(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    shared = tmp_path / "editor_template.html"
    shared.write_text("<p>公众号正文</p>", encoding="utf-8")
    for account_id in ("account-a", "account-b"):
        db.upsert_official_account(
            {
                "id": account_id,
                "name": account_id,
                "app_id": "wx-" + account_id,
                "app_secret_encrypted": "secret",
                "model_id": "config:manus",
                "layout": {},
                "enabled": True,
            }
        )
    config = {
        "_root": str(tmp_path),
        "template": {"body_font_size": "17px", "body_line_height": "32px"},
        "editor_template": {
            "enabled": True,
            "snapshot_path": str(shared),
            "capture_title": "模板",
            "placeholder": "公众号正文",
        },
    }
    assert ensure_account_layouts_initialized(db, config) == 2
    assert ensure_account_layouts_initialized(db, config) == 0
    for account_id in ("account-a", "account-b"):
        account = db.get_official_account(account_id)
        assert account is not None
        assert json.loads(account["layout_json"])["body"]["font_size"] == "17px"
        private = tmp_path / "data" / "templates" / f"{account_id}.html"
        assert private.read_text(encoding="utf-8") == "<p>公众号正文</p>"
