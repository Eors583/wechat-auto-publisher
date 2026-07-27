from __future__ import annotations

from typing import Any

import pytest

from app.ai.image_providers import IMAGE_MINIMAX
from app.ai.model_registry import OPENAI_COMPATIBLE, decrypt_api_key
from app.db import Database
from app.prompt_templates import (
    ARTICLE_PROMPT_PURPOSE,
    IMAGE_PROMPT_PURPOSE,
    PROMPT_MODE_TEMPLATE,
)
from app.services.configuration import ConfigurationService


def _assert_no_credentials(value: Any) -> None:
    sensitive = {
        "api_key",
        "apikey",
        "api_key_encrypted",
        "app_secret",
        "appsecret",
        "app_secret_encrypted",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized.startswith("has_"):
                _assert_no_credentials(item)
                continue
            assert normalized not in sensitive
            assert not normalized.endswith("_secret")
            assert not normalized.endswith("_token")
            assert not normalized.endswith("_cookie")
            _assert_no_credentials(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_credentials(item)


def _save_text_model(
    service: ConfigurationService,
    *,
    name: str = "文本模型",
) -> dict[str, Any]:
    return service.save_model(
        name=name,
        provider_type=OPENAI_COMPATIBLE,
        api_base="https://llm.example.test/v1",
        model="test-chat",
        api_key="sk-private-model-key",
    )


def test_account_and_model_configuration_is_safe_and_shared(tmp_path) -> None:
    db = Database(tmp_path / "configuration.db")
    service = ConfigurationService(
        db,
        {
            "ai": {
                "primary": "moonshot",
                "moonshot": {
                    "api_key": "env-private-key",
                    "api_base": "https://api.moonshot.cn/v1",
                    "model": "moonshot-v1-8k",
                },
            }
        },
    )

    model = _save_text_model(service)
    _assert_no_credentials(model)
    stored_model = db.get_ai_model(str(model["id"]))
    assert stored_model is not None
    assert decrypt_api_key(str(stored_model["api_key_encrypted"])) == "sk-private-model-key"

    account = service.save_account(
        name="企业管理公众号",
        app_id="wx-safe-account",
        app_secret="wechat-private-secret",
        model_id=str(model["id"]),
    )
    _assert_no_credentials(account)
    assert account["has_app_secret"] is True
    assert "wechat-private-secret" not in repr(account)

    stored_account = db.get_official_account(str(account["id"]))
    assert stored_account is not None
    assert "wechat-private-secret" not in str(stored_account["app_secret_encrypted"])

    second_model = _save_text_model(service, name="备用文本模型")
    rebound = service.bind_account_model(str(account["id"]), str(second_model["id"]))
    assert rebound["model_id"] == second_model["id"]
    assert service.set_account_enabled(str(account["id"]), False)["enabled"] is False
    assert service.set_account_enabled(str(account["id"]), True)["enabled"] is True

    all_models = service.list_models()
    _assert_no_credentials(all_models)
    config_model = next(item for item in all_models if item["id"] == "config:moonshot")
    assert config_model["has_api_key"] is True
    assert "env-private-key" not in repr(config_model)

    with pytest.raises(ValueError, match="被公众号"):
        service.delete_model(str(second_model["id"]))
    assert service.delete_account(str(account["id"]))["deleted"] is True
    assert service.delete_model(str(second_model["id"]))["deleted"] is True


def test_prompt_bindings_layout_and_image_settings_reuse_domain_validation(
    tmp_path,
) -> None:
    db = Database(tmp_path / "configuration-prompts.db")
    service = ConfigurationService(db, {"ai": {}})
    text_model = _save_text_model(service)
    image_model = service.save_model(
        name="MiniMax 正文配图",
        provider_type=IMAGE_MINIMAX,
        api_base="",
        model="image-01",
        api_key="image-private-key",
    )
    account = service.save_account(
        name="双提示词公众号",
        app_id="wx-prompts",
        app_secret="wechat-secret",
        model_id=str(text_model["id"]),
    )
    article_prompt = service.save_prompt_template(
        name="深度文章",
        content="面向经营者，强调核心观点和行动建议。",
        purpose=ARTICLE_PROMPT_PURPOSE,
    )
    image_prompt = service.save_prompt_template(
        name="商业纪实图片",
        content="真实商业新闻摄影，不出现文字和水印。",
        purpose=IMAGE_PROMPT_PURPOSE,
    )

    article_bound = service.bind_account_article_prompt(
        str(account["id"]), str(article_prompt["id"])
    )
    assert article_bound["layout"]["article_prompt"]["prompt_mode"] == PROMPT_MODE_TEMPLATE
    assert article_bound["selected_prompt"]["name"] == "深度文章"

    image_bound = service.bind_account_image_prompt(
        str(account["id"]), str(image_prompt["id"])
    )
    assert image_bound["layout"]["inline_images"]["prompt_mode"] == PROMPT_MODE_TEMPLATE
    assert image_bound["selected_prompt"]["name"] == "商业纪实图片"

    laid_out = service.save_account_layout(
        str(account["id"]),
        {
            **image_bound["layout"],
            "body": {
                **image_bound["layout"]["body"],
                "font_size": "18px",
                "color": "#123456",
            },
        },
    )
    assert laid_out["layout"]["body"]["font_size"] == "18px"

    with_images = service.save_account_image_settings(
        str(account["id"]),
        {
            "enabled": True,
            "generate_cover": True,
            "source_mode": "generate",
            "image_model_id": str(image_model["id"]),
            "generation_concurrency": 3,
        },
    )
    settings = with_images["layout"]["inline_images"]
    assert settings["enabled"] is True
    assert settings["image_model_id"] == image_model["id"]
    assert settings["generation_concurrency"] == 3
    assert settings["prompt_template_id"] == image_prompt["id"]
    _assert_no_credentials(with_images)

    with pytest.raises(ValueError, match="不支持的生图配置项"):
        service.save_account_image_settings(
            str(account["id"]), {"api_key": "must-not-be-stored"}
        )
    with pytest.raises(ValueError, match="图片提示词模板"):
        service.bind_account_image_prompt(
            str(account["id"]), str(article_prompt["id"])
        )
    with pytest.raises(ValueError, match="被公众号"):
        service.delete_prompt_template(str(article_prompt["id"]))

    assert [
        item["id"]
        for item in service.list_prompt_templates(purpose=ARTICLE_PROMPT_PURPOSE)
    ] == [article_prompt["id"]]
    assert [
        item["id"]
        for item in service.list_prompt_templates(purpose=IMAGE_PROMPT_PURPOSE)
    ] == [image_prompt["id"]]


def test_model_lifecycle_and_connection_test_are_wrapped(tmp_path, monkeypatch) -> None:
    db = Database(tmp_path / "configuration-model-test.db")
    service = ConfigurationService(db, {"ai": {}})
    model = _save_text_model(service)
    called: list[str] = []

    def fake_test_model_connection(_db: Database, model_id: str) -> str:
        called.append(model_id)
        return "连接成功"

    monkeypatch.setattr(
        "app.services.configuration.test_model_connection",
        fake_test_model_connection,
    )
    result = service.test_model(str(model["id"]))
    assert result == {
        "model_id": model["id"],
        "ok": True,
        "message": "连接成功",
    }
    assert called == [model["id"]]

    disabled = service.set_model_enabled(str(model["id"]), False)
    assert disabled["enabled"] is False
    enabled = service.set_model_enabled(str(model["id"]), True)
    assert enabled["enabled"] is True
    _assert_no_credentials([result, disabled, enabled])

    with pytest.raises(ValueError, match="只读配置"):
        service.set_model_enabled("config:moonshot", False)
    with pytest.raises(ValueError, match="只读配置"):
        service.delete_model("config:moonshot")


def test_account_rejects_image_model_as_text_binding(tmp_path) -> None:
    db = Database(tmp_path / "configuration-image-binding.db")
    service = ConfigurationService(db, {"ai": {}})
    image_model = service.save_model(
        name="MiniMax",
        provider_type=IMAGE_MINIMAX,
        api_base="",
        model="image-01",
        api_key="image-key",
    )

    with pytest.raises(ValueError, match="只能绑定文本模型"):
        service.save_account(
            name="错误绑定",
            app_id="wx-image-model",
            app_secret="secret",
            model_id=str(image_model["id"]),
        )


def test_image_model_generation_test_is_exposed_by_shared_service(
    tmp_path, monkeypatch
) -> None:
    db = Database(tmp_path / "configuration-image-test.db")
    service = ConfigurationService(db, {"_root": str(tmp_path), "ai": {}})
    image_model = service.save_model(
        name="MiniMax 测试生图",
        provider_type=IMAGE_MINIMAX,
        api_base="",
        model="image-01",
        api_key="image-key",
    )
    target = tmp_path / "data" / "model_tests" / "test.jpg"
    called: list[tuple[str, str]] = []

    def fake_generate(_db: Database, model_id: str, output_dir: Any) -> Any:
        called.append((model_id, str(output_dir)))
        return target

    monkeypatch.setattr(
        "app.ai.model_registry.generate_model_test_image",
        fake_generate,
    )
    result = service.generate_model_test_image(str(image_model["id"]))

    assert result == {
        "model_id": image_model["id"],
        "model_name": "MiniMax 测试生图",
        "path": str(target),
    }
    assert called == [
        (str(image_model["id"]), str(tmp_path / "data" / "model_tests"))
    ]
