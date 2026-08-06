from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.accounts import apply_account_selection
from app.feishu.runtime import get_runtime as get_feishu_runtime
from app.feishu.settings import public_feishu_settings, save_feishu_settings
from app.layout_profiles import normalize_layout
from app.services.configuration import ConfigurationService
from app.services.creation_plans import CreationPlanService
from app.services.onboarding import OnboardingService
from app.services.wechat_relay_settings import (
    public_wechat_relay_connection_info,
    public_wechat_relay_settings,
    save_wechat_relay_access_code,
    save_wechat_relay_settings,
)
from app.wechat.factory import build_wechat_client
from app.wechat.template_snapshot import (
    list_template_draft_candidates,
    save_template_draft_candidate,
)


class AccountRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    app_id: str = Field(min_length=1, max_length=160)
    app_secret: str | None = None
    model_id: str = ""
    review_priority: int = Field(default=0, ge=0, le=100)
    enabled: bool = True


class PromptTemplateRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=50000)
    purpose: str = "article"
    enabled: bool = True


class CreationPlanRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    article_prompt_template_id: str | None = None
    image_prompt_template_id: str | None = None
    editorial_review_profile_id: str | None = None
    layout: dict[str, Any] | None = None
    image_settings: dict[str, Any] | None = None
    draft_template_account_id: str | None = None
    enabled: bool = True


class ApplyCreationPlanRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=160)


class AccountLayoutRequest(BaseModel):
    layout: dict[str, Any] = Field(default_factory=dict)


class AccountPromptRequest(BaseModel):
    template_id: str | None = Field(default=None, max_length=160)


class TemplateDraftRequest(BaseModel):
    media_id: str = Field(min_length=1, max_length=300)
    article_index: int = Field(default=0, ge=0)
    placeholder: str = Field(default="蓝血经营管理系统正文", min_length=1, max_length=200)


class FeishuCredentialTestRequest(BaseModel):
    app_id: str = Field(default="", max_length=200)
    app_secret: str | None = Field(default=None, max_length=500)


class FeishuSettingsRequest(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str | None = None
    verification_token: str | None = None
    encrypt_key: str | None = None
    clear_event_security: bool = False
    allow_all: bool = False
    allowed_open_ids: list[str] = Field(default_factory=list)
    allowed_chat_ids: list[str] = Field(default_factory=list)
    default_account_ids: list[str] = Field(default_factory=list)
    agent_model_id: str = ""


class WechatRelaySettingsRequest(BaseModel):
    enabled: bool = False
    gateway_url: str = ""
    username: str = ""
    password: str | None = None
    clear_password: bool = False
    access_code: str | None = None


def create_configuration_router(
    configuration: ConfigurationService,
    plans: CreationPlanService,
    onboarding: OnboardingService,
    require_admin: Callable[..., Any],
) -> APIRouter:
    """Expose account, model-adjacent and external-service settings to the SPA."""

    router = APIRouter(
        prefix="/api/v1/configuration",
        dependencies=[Depends(require_admin)],
    )

    @router.get("/accounts")
    def list_accounts() -> list[dict[str, Any]]:
        return configuration.list_accounts()

    @router.post("/accounts")
    def save_account(payload: AccountRequest) -> dict[str, Any]:
        return _domain_call(
            configuration.save_account,
            account_id=payload.id,
            name=payload.name,
            app_id=payload.app_id,
            app_secret=payload.app_secret,
            model_id=payload.model_id,
            review_priority=payload.review_priority,
            enabled=payload.enabled,
        )

    @router.delete("/accounts/{account_id}")
    def delete_account(account_id: str) -> dict[str, Any]:
        return _domain_call(configuration.delete_account, account_id)

    @router.put("/accounts/{account_id}/layout")
    def save_account_layout(
        account_id: str,
        payload: AccountLayoutRequest,
    ) -> dict[str, Any]:
        return _domain_call(
            configuration.save_account_layout,
            account_id,
            payload.layout,
        )

    @router.put("/accounts/{account_id}/prompts/{purpose}")
    def bind_account_prompt(
        account_id: str,
        purpose: str,
        payload: AccountPromptRequest,
    ) -> dict[str, Any]:
        return _domain_call(
            configuration.bind_account_prompt,
            account_id,
            payload.template_id,
            purpose=purpose,
        )

    @router.get("/accounts/{account_id}/template-drafts")
    def list_account_template_drafts(
        account_id: str,
        placeholder: str = "蓝血经营管理系统正文",
    ) -> dict[str, Any]:
        editor, client, layout = _account_template_context(
            configuration,
            account_id,
            placeholder=placeholder,
        )
        rows = _domain_call(list_template_draft_candidates, client, editor)
        selected = dict(layout.get("editor_template") or {})
        return {
            "current": {
                "media_id": str(selected.get("selected_media_id") or ""),
                "article_index": int(selected.get("selected_article_index") or 0),
                "title": str(selected.get("selected_title") or ""),
                "placeholder": str(selected.get("placeholder") or placeholder),
            },
            "items": [
                {
                    "key": item.key,
                    "media_id": item.media_id,
                    "article_index": item.article_index,
                    "title": item.title,
                    "has_placeholder": item.has_placeholder,
                }
                for item in rows
            ],
        }

    @router.put("/accounts/{account_id}/template-draft")
    def apply_account_template_draft(
        account_id: str,
        payload: TemplateDraftRequest,
    ) -> dict[str, Any]:
        editor, client, layout = _account_template_context(
            configuration,
            account_id,
            placeholder=payload.placeholder,
        )
        rows = _domain_call(list_template_draft_candidates, client, editor)
        candidate = next(
            (
                item
                for item in rows
                if item.media_id == payload.media_id
                and item.article_index == payload.article_index
            ),
            None,
        )
        if candidate is None:
            raise HTTPException(status_code=404, detail="所选模板草稿已不存在，请重新读取")
        _domain_call(save_template_draft_candidate, editor, candidate)
        layout["editor_template"].update(
            enabled=True,
            selected_media_id=candidate.media_id,
            selected_article_index=candidate.article_index,
            selected_title=candidate.title,
            placeholder=payload.placeholder.strip(),
        )
        saved = _domain_call(configuration.save_account_layout, account_id, layout)
        return {
            "account": saved,
            "template": {
                "media_id": candidate.media_id,
                "article_index": candidate.article_index,
                "title": candidate.title,
                "placeholder": payload.placeholder.strip(),
            },
        }

    @router.get("/prompt-templates")
    def list_prompt_templates() -> list[dict[str, Any]]:
        return configuration.list_prompt_templates()

    @router.post("/prompt-templates")
    def save_prompt_template(payload: PromptTemplateRequest) -> dict[str, Any]:
        return _domain_call(
            configuration.save_prompt_template,
            template_id=payload.id,
            name=payload.name,
            content=payload.content,
            purpose=payload.purpose,
            enabled=payload.enabled,
        )

    @router.delete("/prompt-templates/{template_id}")
    def delete_prompt_template(template_id: str) -> dict[str, Any]:
        return _domain_call(configuration.delete_prompt_template, template_id)

    @router.get("/creation-plans")
    def list_creation_plans() -> list[dict[str, Any]]:
        return plans.list_plans(include_builtin=True)

    @router.post("/creation-plans")
    def save_creation_plan(payload: CreationPlanRequest) -> dict[str, Any]:
        return _domain_call(
            plans.save_plan,
            plan_id=payload.id,
            name=payload.name,
            description=payload.description,
            article_prompt_template_id=payload.article_prompt_template_id,
            image_prompt_template_id=payload.image_prompt_template_id,
            editorial_review_profile_id=payload.editorial_review_profile_id,
            layout=payload.layout,
            image_settings=payload.image_settings,
            draft_template_account_id=payload.draft_template_account_id,
            enabled=payload.enabled,
        )

    @router.delete("/creation-plans/{plan_id}")
    def delete_creation_plan(plan_id: str) -> dict[str, Any]:
        return _domain_call(plans.delete_plan, plan_id)

    @router.get("/accounts/{account_id}/creation-plan")
    def get_account_creation_plan(account_id: str) -> dict[str, Any]:
        return _domain_call(plans.get_account_default, account_id)

    @router.put("/accounts/{account_id}/creation-plan")
    def apply_account_creation_plan(
        account_id: str,
        payload: ApplyCreationPlanRequest,
    ) -> dict[str, Any]:
        return _domain_call(plans.apply_to_account, account_id, payload.plan_id)

    @router.get("/feishu")
    def get_feishu() -> dict[str, Any]:
        return {
            "settings": public_feishu_settings(configuration.db),
            "runtime": get_feishu_runtime(configuration.db),
        }

    @router.post("/feishu/test")
    def test_feishu_credentials(
        payload: FeishuCredentialTestRequest,
    ) -> dict[str, Any]:
        return _domain_call(
            onboarding.test_feishu_credentials,
            app_id=payload.app_id,
            app_secret=payload.app_secret,
        )

    @router.get("/feishu/pairing")
    def get_feishu_pairing() -> dict[str, Any]:
        return onboarding.feishu_pairing_status()

    @router.post("/feishu/pairing")
    def create_feishu_pairing() -> dict[str, Any]:
        return _domain_call(onboarding.create_feishu_pairing_code)

    @router.put("/feishu")
    def save_feishu(payload: FeishuSettingsRequest) -> dict[str, Any]:
        _domain_call(
            save_feishu_settings,
            configuration.db,
            **payload.model_dump(),
        )
        return {
            "settings": public_feishu_settings(configuration.db),
            "runtime": get_feishu_runtime(configuration.db),
        }

    @router.get("/wechat-relay")
    def get_wechat_relay() -> dict[str, Any]:
        return {
            "settings": public_wechat_relay_settings(configuration.db),
            "connection": public_wechat_relay_connection_info(),
        }

    @router.put("/wechat-relay")
    def save_wechat_relay(payload: WechatRelaySettingsRequest) -> dict[str, Any]:
        if str(payload.access_code or "").strip():
            settings = _domain_call(
                save_wechat_relay_access_code,
                configuration.db,
                str(payload.access_code),
                enabled=payload.enabled,
                gateway_url=payload.gateway_url,
            )
        else:
            _domain_call(
                save_wechat_relay_settings,
                configuration.db,
                enabled=payload.enabled,
                gateway_url=payload.gateway_url,
                username=payload.username,
                password=payload.password,
                clear_password=payload.clear_password,
            )
            settings = public_wechat_relay_settings(configuration.db)
        return {
            "settings": settings,
            "connection": public_wechat_relay_connection_info(),
        }

    return router


def _account_template_context(
    configuration: ConfigurationService,
    account_id: str,
    *,
    placeholder: str,
) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    record = configuration.db.get_official_account(str(account_id))
    if record is None:
        raise HTTPException(status_code=404, detail="公众号不存在")
    try:
        stored = json.loads(str(record.get("layout_json") or "{}"))
    except json.JSONDecodeError:
        stored = {}
    layout = normalize_layout(stored)
    effective, _ = apply_account_selection(
        configuration.config,
        configuration.db,
        str(account_id),
        allow_disabled=True,
    )
    editor = dict(effective.get("editor_template") or {})
    editor.update(layout["editor_template"])
    editor.update(
        placeholder=str(placeholder or "").strip(),
        snapshot_path=f"data/templates/{account_id}.html",
        _root=effective.get("_root"),
    )
    wechat = dict(effective.get("wechat") or {})
    client = build_wechat_client(
        effective,
        configuration.db,
        app_id=str(wechat.get("app_id") or ""),
        app_secret=str(wechat.get("app_secret") or ""),
    )
    return editor, client, layout


def _domain_call(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return callback(*args, **kwargs)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


__all__ = [
    "AccountRequest",
    "AccountLayoutRequest",
    "AccountPromptRequest",
    "ApplyCreationPlanRequest",
    "CreationPlanRequest",
    "FeishuCredentialTestRequest",
    "FeishuSettingsRequest",
    "PromptTemplateRequest",
    "TemplateDraftRequest",
    "WechatRelaySettingsRequest",
    "create_configuration_router",
]
