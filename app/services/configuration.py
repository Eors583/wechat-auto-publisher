from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.accounts import (
    public_accounts,
    save_account as persist_account,
    save_account_layout as persist_account_layout,
    save_account_prompt_selection,
)
from app.ai.image_providers import is_image_provider
from app.ai.model_registry import (
    CONFIG_MODEL_PREFIX,
    build_text_client,
    configured_models,
    decrypt_api_key,
    public_models,
    save_model as persist_model,
    test_model_connection,
)
from app.benchmark import fetch_official_publish_record
from app.config import load_config
from app.db import Database
from app.layout_profiles import normalize_layout
from app.prompt_templates import (
    ARTICLE_PROMPT_PURPOSE,
    IMAGE_PROMPT_PURPOSE,
    PROMPT_PURPOSES,
    delete_prompt_template as remove_prompt_template,
    public_prompt_templates,
    save_prompt_template as persist_prompt_template,
)
from app.wechat.factory import build_wechat_client


_SENSITIVE_KEYS = {
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
    "password",
    "proxy_password",
    "relay_password",
}


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().casefold()
    if normalized.startswith("has_"):
        return False
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("_secret")
        or normalized.endswith("_token")
        or normalized.endswith("_cookie")
        or normalized.endswith("_password")
    )


def _public(value: Any) -> Any:
    """Return a detached value with credentials removed at every nesting level."""

    if isinstance(value, dict):
        return {
            str(key): _public(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [_public(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_public(item) for item in value)
    return deepcopy(value)


class ConfigurationService:
    """Shared account, model and prompt configuration boundary.

    Desktop UI, HTTP API and conversational clients can use this service without
    receiving encrypted or plaintext credentials. Domain validation and storage
    remain in the existing account/model/prompt modules.
    """

    def __init__(self, db: Database, config: dict[str, Any] | None = None) -> None:
        self.db = db
        self.config = config if config is not None else load_config()

    # ------------------------------------------------------------------
    # Official accounts
    # ------------------------------------------------------------------
    def list_accounts(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        return _public(public_accounts(self.db, enabled_only=enabled_only))

    def get_account(self, account_id: str) -> dict[str, Any]:
        account_id = self._required_id(account_id, "公众号")
        item = next(
            (
                account
                for account in public_accounts(self.db)
                if str(account.get("id") or "") == account_id
            ),
            None,
        )
        if item is None:
            raise ValueError("公众号不存在")
        return _public(item)

    def save_account(
        self,
        *,
        name: str,
        app_id: str,
        app_secret: str | None,
        model_id: str = "",
        enabled: bool = True,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        model_id = str(model_id or "").strip()
        if model_id:
            self._require_text_model(model_id)
        saved_id = persist_account(
            self.db,
            account_id=account_id,
            name=name,
            app_id=app_id,
            app_secret=app_secret,
            model_id=model_id,
            enabled=bool(enabled),
        )
        return self.get_account(saved_id)

    def set_account_enabled(
        self, account_id: str, enabled: bool
    ) -> dict[str, Any]:
        record = self._account_record(account_id)
        record["enabled"] = bool(enabled)
        self.db.upsert_official_account(record)
        return self.get_account(str(record["id"]))

    def delete_account(self, account_id: str) -> dict[str, Any]:
        account_id = self._required_id(account_id, "公众号")
        existed = self.db.get_official_account(account_id) is not None
        if existed:
            self.db.delete_official_account(account_id)
        return {"id": account_id, "deleted": existed}

    def bind_account_model(
        self, account_id: str, model_id: str
    ) -> dict[str, Any]:
        record = self._account_record(account_id)
        self._require_text_model(model_id)
        saved_id = persist_account(
            self.db,
            account_id=str(record["id"]),
            name=str(record.get("name") or ""),
            app_id=str(record.get("app_id") or ""),
            app_secret=None,
            model_id=str(model_id or ""),
            enabled=bool(record.get("enabled")),
        )
        return self.get_account(saved_id)

    def bind_account_prompt(
        self,
        account_id: str,
        template_id: str | None,
        *,
        purpose: str,
    ) -> dict[str, Any]:
        if purpose not in PROMPT_PURPOSES:
            raise ValueError("提示词模板类型无效")
        prompt_name = save_account_prompt_selection(
            self.db,
            self._required_id(account_id, "公众号"),
            template_id,
            purpose=purpose,
        )
        result = self.get_account(account_id)
        result["selected_prompt"] = {
            "purpose": purpose,
            "template_id": str(template_id or ""),
            "name": prompt_name,
        }
        return _public(result)

    def bind_account_article_prompt(
        self, account_id: str, template_id: str | None
    ) -> dict[str, Any]:
        return self.bind_account_prompt(
            account_id,
            template_id,
            purpose=ARTICLE_PROMPT_PURPOSE,
        )

    def bind_account_image_prompt(
        self, account_id: str, template_id: str | None
    ) -> dict[str, Any]:
        return self.bind_account_prompt(
            account_id,
            template_id,
            purpose=IMAGE_PROMPT_PURPOSE,
        )

    def save_account_layout(
        self, account_id: str, layout: dict[str, Any]
    ) -> dict[str, Any]:
        persist_account_layout(
            self.db,
            self._required_id(account_id, "公众号"),
            dict(layout or {}),
        )
        return self.get_account(account_id)

    def save_account_image_settings(
        self, account_id: str, settings: dict[str, Any]
    ) -> dict[str, Any]:
        """Patch only the per-account inline-image settings and validate layout."""

        record = self._account_record(account_id)
        layout = self._record_layout(record)
        allowed = set(normalize_layout({})["inline_images"])
        unknown = set(settings or {}) - allowed
        if unknown:
            raise ValueError(
                "不支持的生图配置项：" + "、".join(sorted(str(item) for item in unknown))
            )
        layout["inline_images"].update(dict(settings or {}))
        persist_account_layout(self.db, str(record["id"]), layout)
        return self.get_account(str(record["id"]))

    def save_account_benchmark_settings(
        self, account_id: str, settings: dict[str, Any]
    ) -> dict[str, Any]:
        """Save one publishing account's benchmark-ad source and match rules."""

        record = self._account_record(account_id)
        layout = self._record_layout(record)
        allowed = set(normalize_layout({})["benchmark"]) - {"configured"}
        unknown = set(settings or {}) - allowed
        if unknown:
            raise ValueError(
                "不支持的广告栏配置项："
                + "、".join(sorted(str(item) for item in unknown))
            )
        benchmark = dict(layout["benchmark"])
        benchmark.update(dict(settings or {}))
        benchmark["configured"] = True
        source_account_id = str(
            benchmark.get("source_account_id") or ""
        ).strip()
        if bool(benchmark.get("enabled")):
            source = self._account_record(source_account_id)
            if str(source["id"]) == str(record["id"]):
                raise ValueError("对标公众号不能与当前发布公众号相同")
        layout["benchmark"] = benchmark
        persist_account_layout(self.db, str(record["id"]), layout)
        return self.get_account(str(record["id"]))

    def preview_account_benchmark(self, source_account_id: str) -> dict[str, Any]:
        """Read the selected source's latest published group without caching."""

        source = self._account_record(source_account_id)
        client = build_wechat_client(
            self.config,
            self.db,
            str(source.get("app_id") or ""),
            decrypt_api_key(str(source.get("app_secret_encrypted") or "")),
        )
        record = fetch_official_publish_record(client)
        if record is None or not record.articles:
            raise ValueError("对标公众号暂未返回可识别的发表记录")
        return {
            "source_account_id": str(source["id"]),
            "source_account_name": str(source.get("name") or "对标公众号"),
            "published_at": record.published_at,
            "source": record.source,
            "articles": [
                {
                    "title": article.title,
                    "cover_url": article.cover_url,
                    "url": article.url,
                }
                for article in record.articles[:8]
            ],
        }

    # ------------------------------------------------------------------
    # Models
    # ------------------------------------------------------------------
    def list_models(
        self,
        *,
        enabled_only: bool = False,
        purpose: str | None = None,
        include_config: bool = True,
    ) -> list[dict[str, Any]]:
        if purpose not in {None, "text", "image"}:
            raise ValueError("模型用途必须是 text 或 image")
        items = public_models(
            self.db,
            enabled_only=enabled_only,
            purpose=purpose,
        )
        if include_config and purpose != "image":
            items = [*configured_models(self.config), *items]
        return _public(items)

    def get_model(self, model_id: str) -> dict[str, Any]:
        model_id = self._required_id(model_id, "模型")
        item = next(
            (
                model
                for model in self.list_models(include_config=True)
                if str(model.get("id") or "") == model_id
            ),
            None,
        )
        if item is None:
            raise ValueError("模型不存在")
        return _public(item)

    def save_model(
        self,
        *,
        name: str,
        provider_type: str,
        api_base: str,
        model: str,
        api_key: str | None,
        local_agent_id: str | None = None,
        enabled: bool = True,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        if model_id is not None:
            model_id = self._editable_model_id(model_id)
        saved_id = persist_model(
            self.db,
            model_id=model_id,
            name=name,
            provider_type=provider_type,
            api_base=api_base,
            model=model,
            api_key=api_key,
            local_agent_id=local_agent_id,
            enabled=bool(enabled),
        )
        return self.get_model(saved_id)

    def test_model(self, model_id: str) -> dict[str, Any]:
        model_id = self._required_id(model_id, "模型")
        if model_id.startswith(CONFIG_MODEL_PREFIX):
            client = build_text_client(self.db, self.config, model_id)
            client.complete("只回复 OK")
            message = "连接成功"
        else:
            message = test_model_connection(self.db, model_id)
        return {"model_id": model_id, "ok": True, "message": str(message)}

    def generate_model_test_image(self, model_id: str) -> dict[str, Any]:
        """Call an enabled image provider once and return its local test asset.

        Keeping this in the shared configuration service gives the desktop UI,
        Feishu and future API callers one validation and output-path rule.
        """

        from app.ai.model_registry import generate_model_test_image

        model_id = self._editable_model_id(model_id)
        record = self.db.get_ai_model(model_id)
        if record is None:
            raise ValueError("模型不存在")
        root = Path(str(self.config.get("_root") or "."))
        target = generate_model_test_image(
            self.db,
            model_id,
            root / "data" / "model_tests",
        )
        return {
            "model_id": model_id,
            "model_name": str(record.get("name") or model_id),
            "path": str(target),
        }

    def set_model_enabled(self, model_id: str, enabled: bool) -> dict[str, Any]:
        model_id = self._editable_model_id(model_id)
        record = self.db.get_ai_model(model_id)
        if record is None:
            raise ValueError("模型不存在")
        record["enabled"] = bool(enabled)
        self.db.upsert_ai_model(record)
        return self.get_model(model_id)

    def delete_model(self, model_id: str) -> dict[str, Any]:
        model_id = self._editable_model_id(model_id)
        existed = self.db.get_ai_model(model_id) is not None
        if existed:
            self.db.delete_ai_model(model_id)
        return {"id": model_id, "deleted": existed}

    # ------------------------------------------------------------------
    # Prompt templates
    # ------------------------------------------------------------------
    def list_prompt_templates(
        self,
        *,
        purpose: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        if purpose is not None:
            if purpose not in PROMPT_PURPOSES:
                raise ValueError("提示词模板类型无效")
            return _public(
                public_prompt_templates(
                    self.db,
                    purpose=purpose,
                    enabled_only=enabled_only,
                )
            )
        items: list[dict[str, Any]] = []
        for item_purpose in (ARTICLE_PROMPT_PURPOSE, IMAGE_PROMPT_PURPOSE):
            items.extend(
                public_prompt_templates(
                    self.db,
                    purpose=item_purpose,
                    enabled_only=enabled_only,
                )
            )
        return _public(items)

    def get_prompt_template(self, template_id: str) -> dict[str, Any]:
        template_id = self._required_id(template_id, "提示词模板")
        record = self.db.get_prompt_template(template_id)
        if record is None:
            raise ValueError("提示词模板不存在")
        return _public(record)

    def save_prompt_template(
        self,
        *,
        name: str,
        content: str,
        purpose: str,
        enabled: bool = True,
        template_id: str | None = None,
    ) -> dict[str, Any]:
        saved_id = persist_prompt_template(
            self.db,
            template_id=template_id,
            name=name,
            content=content,
            purpose=purpose,
            enabled=bool(enabled),
        )
        return self.get_prompt_template(saved_id)

    def delete_prompt_template(self, template_id: str) -> dict[str, Any]:
        template_id = self._required_id(template_id, "提示词模板")
        existed = self.db.get_prompt_template(template_id) is not None
        if existed:
            remove_prompt_template(self.db, template_id)
        return {"id": template_id, "deleted": existed}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _required_id(value: str, label: str) -> str:
        clean = str(value or "").strip()
        if not clean:
            raise ValueError(f"{label} ID 不能为空")
        return clean

    def _account_record(self, account_id: str) -> dict[str, Any]:
        clean = self._required_id(account_id, "公众号")
        record = self.db.get_official_account(clean)
        if record is None:
            raise ValueError("公众号不存在")
        return record

    @staticmethod
    def _record_layout(record: dict[str, Any]) -> dict[str, Any]:
        try:
            stored = json.loads(str(record.get("layout_json") or "{}"))
        except json.JSONDecodeError:
            stored = {}
        return normalize_layout(stored)

    def _require_text_model(self, model_id: str) -> dict[str, Any]:
        model = self.get_model(self._required_id(model_id, "模型"))
        if is_image_provider(str(model.get("provider_type") or "")):
            raise ValueError("公众号只能绑定文本模型")
        if not bool(model.get("enabled", True)):
            raise ValueError("所选文本模型已停用")
        return model

    def _editable_model_id(self, model_id: str) -> str:
        clean = self._required_id(model_id, "模型")
        if clean.startswith(CONFIG_MODEL_PREFIX):
            raise ValueError("config.yaml / .env 模型为只读配置，不能在此修改或删除")
        return clean


__all__ = ["ConfigurationService"]
