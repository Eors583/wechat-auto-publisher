from __future__ import annotations

import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.ai.image_providers import is_image_provider
from app.api.editorial_reviews import create_editorial_review_router
from app.api.local_agents import create_local_agent_router
from app.api.wechat_commands import create_wechat_command_router
from app.config import load_config
from app.db import customer_data_scope
from app.services import (
    BatchService,
    FollowedContentService,
    TopicSourceService,
    get_batch_service,
)
from app.services.auth import AuthService
from app.services.billing import BillingService, live_configuration_issues
from app.services.configuration import ConfigurationService
from app.services.failures import (
    classify_job_failure,
    public_failure,
    sanitize_failure_payload,
    sanitize_failure_text,
)
from app.services.feishu_integrations import FeishuIntegrationService
from app.services.onboarding import OnboardingService

logger = logging.getLogger(__name__)


class StructuredHTTPException(HTTPException):
    """HTTP error carrying an explicit workflow stage for failure projection."""

    def __init__(
        self,
        status_code: int,
        detail: Any,
        *,
        stage: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.failure_stage = str(stage or "").strip()


def _structured_http_error(
    status_code: int,
    error: Any,
    *,
    stage: str = "",
) -> StructuredHTTPException:
    return StructuredHTTPException(
        status_code,
        sanitize_failure_text(error),
        stage=stage,
    )


def _request_failure_stage(request: Request) -> str:
    path = str(request.url.path or "").casefold()
    if "/retry" in path:
        return "retry"
    if path.endswith("/drafts") or "draft" in path:
        return "inject"
    if "/preflight" in path or "connection-health" in path:
        return "preflight"
    if "/paragraph" in path:
        return "rewrite"
    if "/cover" in path or "/inline-images" in path or "/rerender" in path:
        return "render"
    if "/selection" in path:
        return "title_optimize"
    if "/confirm" in path or "/view" in path or "/needs-changes" in path:
        return "review"
    if "/batches" in path:
        return "batch"
    if "/accounts" in path:
        return "account"
    return "api"


def _failure_response(
    detail: Any,
    *,
    stage: str,
) -> dict[str, Any]:
    safe_detail = sanitize_failure_payload(detail)
    if isinstance(safe_detail, str):
        source = safe_detail
    else:
        try:
            source = json.dumps(safe_detail, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            source = sanitize_failure_text(safe_detail)
    failure = public_failure(
        classify_job_failure(source, step=stage or "api", status="failed")
    )
    return {"detail": safe_detail, "failure": failure}


class CreateBatchRequest(BaseModel):
    topic: str | None = None
    source_mode: str | None = None
    reference_urls: list[str] = Field(default_factory=list)
    required_facts: str | None = None
    rewrite_intensity: str | None = None
    source_url: str | None = None
    raw_content: str | None = None
    account_ids: list[str] = Field(min_length=1)
    requested_by: str | None = None
    chat_id: str | None = None


class SelectJobRequest(BaseModel):
    title_index: int = Field(ge=0)
    subtitle_index: int | None = Field(default=None, ge=0)


class RetryJobRequest(BaseModel):
    step: str = "auto"
    model_id: str | None = None
    source_url: str | None = None
    raw_content: str | None = None
    image_index: int | None = Field(default=None, ge=0)
    image_id: str | None = None


class UpdateJobContentRequest(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    digest: str | None = None
    body: str | None = None


class RegenerateParagraphRequest(BaseModel):
    paragraph_index: int = Field(ge=0)
    instruction: str = Field(min_length=1, max_length=2000)


class RegenerateInlineImageRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


class RegenerateCoverRequest(BaseModel):
    instruction: str = Field(default="", max_length=2000)


class SelectCoverRequest(BaseModel):
    thumb_media_id: str = Field(min_length=1)


class TopicSourceRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1)
    source_type: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ManualTopicRequest(BaseModel):
    title: str = Field(min_length=1)
    url: str = ""
    summary: str = ""
    category: str = ""


class FollowedAccountRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1)
    wechat_id: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    fetch_method: str = "public_search"
    sample_url: str = ""
    source_url: str = ""
    keywords: list[str] = Field(default_factory=list)
    is_owned: bool = False
    enabled: bool = True
    refresh_hours: int = Field(default=12, ge=1, le=720)


class FollowedArticleRequest(BaseModel):
    url: str = Field(min_length=1)
    followed_account_id: str | None = None
    source_channel: str = "api"


class FollowedArticleStateRequest(BaseModel):
    is_read: bool | None = None
    is_favorite: bool | None = None
    is_ignored: bool | None = None
    rewritten_batch_id: str | None = None


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class AdminModelRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    provider_type: str = "openai_compatible"
    api_base: str = ""
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = None
    local_agent_id: str | None = None
    enabled: bool = True


class ModelPriceCardRequest(BaseModel):
    id: str | None = None
    provider: str = Field(min_length=1, max_length=80)
    provider_model: str = Field(default="*", min_length=1, max_length=160)
    modality: str = Field(default="text", pattern="^(text|image)$")
    input_micro_cny_per_million: int = Field(default=0, ge=0)
    cached_input_micro_cny_per_million: int = Field(default=0, ge=0)
    output_micro_cny_per_million: int = Field(default=0, ge=0)
    image_micro_cny_each: int = Field(default=0, ge=0)
    fixed_request_micro_cny: int = Field(default=0, ge=0)
    metering_mode: str = Field(
        default="TOKEN",
        pattern="^(TOKEN|FIXED|UNIT|BYOK)$",
    )
    reasoning_micro_cny_per_million: int = Field(default=0, ge=0)
    provider_unit_micro_cny_each: int = Field(default=0, ge=0)
    provider_risk_basis_points: int = Field(default=10_000, ge=1)
    markup_basis_points: int = Field(default=10_000, ge=0)
    points_per_cny: int = Field(default=100, ge=0)
    effective_from: str | None = None
    effective_to: str | None = None
    enabled: bool = True


class BillingPolicyRequest(BaseModel):
    name: str = Field(default="默认商业积分政策", min_length=1, max_length=120)
    mode: str = Field(default="shadow", pattern="^(off|shadow|live)$")
    point_retail_micro_cny: int = Field(default=10_000, ge=1)
    max_package_discount_basis_points: int = Field(default=2_000, ge=0, le=10_000)
    payment_fee_basis_points: int = Field(default=150, ge=0, le=10_000)
    tax_basis_points: int = Field(default=600, ge=0, le=10_000)
    target_margin_basis_points: int = Field(default=6_500, ge=0, le=10_000)
    provider_risk_reserve_basis_points: int = Field(default=1_500, ge=0, le=10_000)
    platform_task_cost_micro_cny: int = Field(default=30_000, ge=0)
    rounding_points: int = Field(default=5, ge=1)
    byok_infrastructure_points: int = Field(default=15, ge=0)


class BillingTaskRateRequest(BaseModel):
    task_code: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    base_points: int = Field(default=0, ge=0)
    max_reserve_points: int = Field(default=0, ge=0)
    enabled: bool = True


class CreditGrantRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    points: int = Field(gt=0)
    source_id: str = Field(default="", max_length=120)
    expires_at: str | None = None
    reason: str = Field(default="", max_length=500)


class FeishuIntegrationRequest(BaseModel):
    app_id: str = Field(min_length=1, max_length=200)
    app_secret: str | None = Field(default=None, max_length=500)
    verification_token: str | None = Field(default=None, max_length=500)
    encrypt_key: str | None = Field(default=None, max_length=500)
    agent_model_id: str = Field(min_length=1, max_length=200)
    account_ids: list[str] = Field(min_length=1)
    default_account_id: str = Field(min_length=1, max_length=200)
    enabled: bool = True


class FeishuCredentialTestRequest(BaseModel):
    app_id: str = ""
    app_secret: str | None = None


def create_api_app(
    config: dict[str, Any] | None = None,
    service: BatchService | None = None,
    *,
    start_feishu: bool = True,
) -> FastAPI:
    # Provider URLs can contain short-lived WeChat/Feishu credentials in query
    # parameters. Keep transport-level request logging out of shared logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("Lark").setLevel(logging.WARNING)
    cfg = config or load_config()
    batch_service = service or get_batch_service(cfg)
    cfg = dict(cfg)
    api_cfg = dict(cfg.get("api") or {})
    expected_token = str(api_cfg.get("token") or "").strip()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        default_admin = auth_service.ensure_default_admin()
        onboarding_service.migrate_legacy_state()
        FeishuIntegrationService(batch_service.db, cfg).migrate_legacy_global(
            str(default_admin["id"])
        )
        # Multi-user Feishu uses per-integration HTTPS callbacks. Never bind a
        # process-global bot to the default administrator here.
        yield

    app = FastAPI(
        title="公众号改写助手 API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.batch_service = batch_service
    app.state.config = cfg
    topic_service = TopicSourceService(batch_service.db, cfg)
    followed_service = FollowedContentService(batch_service.db, cfg)
    onboarding_service = OnboardingService(batch_service.db, cfg)
    auth_service = AuthService(batch_service.db)
    configuration_service = ConfigurationService(batch_service.db, cfg)
    feishu_integration_service = FeishuIntegrationService(batch_service.db, cfg)

    @app.exception_handler(StarletteHTTPException)
    async def sanitized_http_error(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        stage = str(getattr(exc, "failure_stage", "") or "").strip()
        content = _failure_response(
            exc.detail,
            stage=stage or _request_failure_stage(request),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def sanitized_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # Pydantic includes the original input by default.  Omitting it keeps
        # ``loc/msg/type`` compatibility without reflecting credentials.
        detail = [
            {
                key: value
                for key, value in dict(item).items()
                if key not in {"input", "ctx", "url"}
            }
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_failure_response(
                detail,
                stage=_request_failure_stage(request),
            ),
        )

    @app.exception_handler(Exception)
    async def sanitized_unhandled_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        safe_error = sanitize_failure_text(exc)
        logger.error(
            "Unhandled API error on %s: %s",
            request.url.path,
            safe_error,
        )
        content = _failure_response(
            safe_error or "服务器内部错误",
            stage=_request_failure_stage(request),
        )
        # Do not expose the provider's raw message through the compatibility
        # detail field on unexpected failures.
        content["detail"] = "服务器内部错误"
        return JSONResponse(status_code=500, content=content)

    def _bearer_token(authorization: str | None) -> str:
        prefix = "Bearer "
        return (
            authorization[len(prefix):].strip()
            if authorization and authorization.startswith(prefix)
            else ""
        )

    def require_token(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        supplied = _bearer_token(authorization)
        if (
            expected_token
            and supplied
            and hmac.compare_digest(supplied, expected_token)
        ):
            return auth_service.ensure_default_admin()
        user = auth_service.authenticate(supplied)
        if user:
            return user
        if not supplied:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="请先登录后再操作",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
        )

    def require_admin(
        principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        if str(principal.get("role") or "") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有管理员可以配置模型和密钥",
            )
        return principal

    @app.middleware("http")
    async def bind_customer_data_scope(
        request: Request,
        call_next: Any,
    ) -> Any:
        """Bind shared service objects to the authenticated user's data."""

        supplied = _bearer_token(request.headers.get("authorization"))
        principal = auth_service.authenticate(supplied) if supplied else None
        if (
            principal is None
            and expected_token
            and supplied
            and hmac.compare_digest(supplied, expected_token)
        ):
            principal = auth_service.ensure_default_admin()
        with customer_data_scope(
            str((principal or {}).get("id") or "")
        ):
            return await call_next(request)

    @app.post("/api/v1/auth/register")
    def register_user(payload: RegisterRequest) -> dict[str, Any]:
        try:
            auth_service.register(payload.username, payload.password)
            return auth_service.login(payload.username, payload.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/auth/login")
    def login_user(payload: LoginRequest) -> dict[str, Any]:
        try:
            return auth_service.login(payload.username, payload.password)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc

    @app.get("/api/v1/auth/me")
    def current_user(
        principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        return principal

    @app.get("/api/v1/billing/plans")
    def billing_plans() -> list[dict[str, Any]]:
        platform_db = batch_service.db.for_user("")
        policy = platform_db.get_billing_pricing_policy()
        mode = str(policy.get("mode") or "shadow")
        return [{
            "id": str(policy.get("id") or "default"),
            "name": str(policy.get("name") or "商业积分"),
            "mode": mode,
            "enabled": mode != "off",
            "point_retail_micro_cny": int(
                policy.get("point_retail_micro_cny") or 10_000
            ),
            "task_rates": [
                {
                    "task_code": str(item.get("task_code") or ""),
                    "label": str(item.get("label") or ""),
                    "base_points": int(item.get("base_points") or 0),
                    "max_reserve_points": int(
                        item.get("max_reserve_points") or 0
                    ),
                }
                for item in platform_db.list_billing_task_rates(
                    enabled_only=True
                )
            ],
            "notice": (
                "任务开始时冻结最高积分，完成后按实际成本结算并退回差额。"
                if mode == "live"
                else "当前为积分试算，不扣积分、不限制任何现有功能。"
            ),
        }]

    @app.get(
        "/api/v1/me/billing/summary",
        dependencies=[Depends(require_token)],
    )
    def my_billing_summary() -> dict[str, Any]:
        return BillingService(batch_service.db).summary()

    @app.get(
        "/api/v1/me/billing/usage",
        dependencies=[Depends(require_token)],
    )
    def my_billing_usage(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, Any]]:
        return BillingService(batch_service.db).list_usage(
            limit=limit, offset=offset
        )

    @app.get(
        "/api/v1/me/billing/ledger",
        dependencies=[Depends(require_token)],
    )
    def my_billing_ledger(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, Any]]:
        return BillingService(batch_service.db).list_ledger(
            limit=limit, offset=offset
        )

    @app.get("/api/v1/me/feishu-integration")
    def get_my_feishu_integration(
        request: Request,
        _principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        return feishu_integration_service.public(
            callback_base_url=str(request.base_url).rstrip("/")
        )

    @app.put("/api/v1/me/feishu-integration")
    def save_my_feishu_integration(
        payload: FeishuIntegrationRequest,
        request: Request,
        _principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        try:
            feishu_integration_service.save(**payload.model_dump())
            return feishu_integration_service.public(
                callback_base_url=str(request.base_url).rstrip("/")
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/me/feishu-integration/test")
    def test_my_feishu_integration(
        payload: FeishuCredentialTestRequest,
        _principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        try:
            return feishu_integration_service.test_credentials(
                app_id=payload.app_id,
                app_secret=payload.app_secret,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/me/feishu-integration/pairing-code")
    def create_my_feishu_pairing_code(
        _principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        try:
            return feishu_integration_service.create_pairing_code()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/me/feishu-integration/unbind")
    def unbind_my_feishu_integration(
        _principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        return feishu_integration_service.unbind()

    @app.post("/api/v1/me/feishu-integration/disable")
    def disable_my_feishu_integration(
        _principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        return feishu_integration_service.set_enabled(False)

    @app.post("/api/v1/me/feishu-integration/enable")
    def enable_my_feishu_integration(
        _principal: dict[str, Any] = Depends(require_token),
    ) -> dict[str, Any]:
        try:
            return feishu_integration_service.set_enabled(True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/auth/logout")
    def logout_user(
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        auth_service.logout(_bearer_token(authorization))
        return {"ok": True}

    @app.get(
        "/api/v1/admin/users",
        dependencies=[Depends(require_admin)],
    )
    def admin_users() -> list[dict[str, Any]]:
        return auth_service.list_users()

    @app.get(
        "/api/v1/admin/billing/usage-summary",
        dependencies=[Depends(require_admin)],
    )
    def admin_billing_usage_summary() -> dict[str, int]:
        with customer_data_scope(""):
            return batch_service.db.admin_billing_usage_summary()

    @app.get(
        "/api/v1/admin/billing/usage-events",
        dependencies=[Depends(require_admin)],
    )
    def admin_billing_usage_events(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, Any]]:
        with customer_data_scope(""):
            return batch_service.db.admin_list_ai_usage_events(
                limit=limit, offset=offset
            )

    @app.get(
        "/api/v1/admin/billing/price-cards",
        dependencies=[Depends(require_admin)],
    )
    def admin_billing_price_cards() -> list[dict[str, Any]]:
        with customer_data_scope(""):
            return batch_service.db.list_model_price_cards()

    @app.get(
        "/api/v1/admin/billing/policy",
        dependencies=[Depends(require_admin)],
    )
    def admin_billing_policy() -> dict[str, Any]:
        with customer_data_scope(""):
            policy = batch_service.db.get_billing_pricing_policy()
            return {
                **policy,
                "live_configuration_issues": live_configuration_issues(
                    batch_service.db
                ),
            }

    @app.put(
        "/api/v1/admin/billing/policy",
        dependencies=[Depends(require_admin)],
    )
    def save_admin_billing_policy(
        payload: BillingPolicyRequest,
    ) -> dict[str, Any]:
        try:
            with customer_data_scope(""):
                if payload.mode == "live":
                    issues = live_configuration_issues(batch_service.db)
                    if issues:
                        raise HTTPException(
                            status_code=409,
                            detail="；".join(issues),
                        )
                batch_service.db.upsert_billing_pricing_policy(
                    payload.model_dump()
                )
                return batch_service.db.get_billing_pricing_policy()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/api/v1/admin/billing/task-rates",
        dependencies=[Depends(require_admin)],
    )
    def admin_billing_task_rates() -> list[dict[str, Any]]:
        with customer_data_scope(""):
            return batch_service.db.list_billing_task_rates()

    @app.put(
        "/api/v1/admin/billing/task-rates/{task_code}",
        dependencies=[Depends(require_admin)],
    )
    def save_admin_billing_task_rate(
        task_code: str,
        payload: BillingTaskRateRequest,
    ) -> dict[str, Any]:
        if str(payload.task_code) != str(task_code):
            raise HTTPException(status_code=400, detail="任务编码与路径不一致")
        try:
            with customer_data_scope(""):
                batch_service.db.upsert_billing_task_rate(payload.model_dump())
                return next(
                    item
                    for item in batch_service.db.list_billing_task_rates()
                    if str(item.get("task_code")) == str(task_code)
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/admin/billing/credits/grants")
    def grant_admin_billing_credits(
        payload: CreditGrantRequest,
        principal: dict[str, Any] = Depends(require_admin),
    ) -> dict[str, Any]:
        platform_db = batch_service.db.for_user("")
        if not platform_db.get_user(payload.user_id):
            raise HTTPException(status_code=404, detail="用户不存在")
        user_db = platform_db.for_user(payload.user_id)
        try:
            bucket_id = user_db.grant_credit_points(
                points=payload.points,
                source_type="admin",
                source_id=payload.source_id,
                expires_at=payload.expires_at,
                actor_user_id=str(principal.get("id") or "admin-api"),
                reason=payload.reason or "管理员发放积分",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "bucket_id": bucket_id,
            "user_id": payload.user_id,
            "wallet": user_db.credit_wallet_summary(),
        }

    @app.post(
        "/api/v1/admin/billing/price-cards",
        dependencies=[Depends(require_admin)],
    )
    def save_admin_billing_price_card(
        payload: ModelPriceCardRequest,
    ) -> dict[str, Any]:
        try:
            with customer_data_scope(""):
                card_id = batch_service.db.upsert_model_price_card(
                    payload.model_dump()
                )
                return next(
                    item
                    for item in batch_service.db.list_model_price_cards()
                    if str(item.get("id")) == card_id
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/api/v1/admin/models",
        dependencies=[Depends(require_admin)],
    )
    def admin_models() -> list[dict[str, Any]]:
        with customer_data_scope(""):
            return configuration_service.list_models(include_config=False)

    @app.post(
        "/api/v1/admin/models",
        dependencies=[Depends(require_admin)],
    )
    def save_admin_model(payload: AdminModelRequest) -> dict[str, Any]:
        try:
            with customer_data_scope(""):
                return configuration_service.save_model(
                    model_id=payload.id,
                    name=payload.name,
                    provider_type=payload.provider_type,
                    api_base=payload.api_base,
                    model=payload.model,
                    api_key=payload.api_key,
                    local_agent_id=payload.local_agent_id,
                    enabled=payload.enabled,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/v1/admin/models/{model_id}/test",
        dependencies=[Depends(require_admin)],
    )
    def test_admin_model(model_id: str) -> dict[str, Any]:
        try:
            with customer_data_scope(""):
                return configuration_service.test_model(model_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete(
        "/api/v1/admin/models/{model_id}",
        dependencies=[Depends(require_admin)],
    )
    def delete_admin_model(model_id: str) -> dict[str, Any]:
        try:
            with customer_data_scope(""):
                return configuration_service.delete_model(model_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    app.include_router(
        create_editorial_review_router(batch_service, require_token)
    )
    app.include_router(
        create_local_agent_router(batch_service.db, require_token)
    )
    app.include_router(create_wechat_command_router(batch_service, cfg))

    @app.post("/api/feishu/events/{callback_key}")
    async def receive_feishu_event(
        callback_key: str,
        request: Request,
    ) -> Response:
        from app.feishu.webhook import FeishuWebhookProcessor

        raw = FeishuWebhookProcessor(batch_service, cfg).handle(
            callback_key,
            uri=str(request.url.path),
            headers=dict(request.headers),
            body=await request.body(),
        )
        status_code = int(raw.status_code or 500)
        content = raw.content or b'{}'
        if status_code >= 400:
            content = b'{"msg":"invalid event"}'
        return Response(
            content=content,
            status_code=status_code,
            headers=dict(raw.headers or {}),
            media_type="application/json",
        )

    @app.get("/health")
    def health() -> dict[str, Any]:
        feishu_health = batch_service.db.feishu_integration_health()
        feishu_enabled = feishu_health["enabled"] > 0
        return {
            "ok": True,
            "service": "wechat-auto-publisher",
            "version": __version__,
            "instance_root": str(cfg.get("_root") or ""),
            "pid": os.getpid(),
            "launcher_session_id": str(
                os.getenv("WECHAT_PUBLISHER_LAUNCH_SESSION_ID") or ""
            ),
            "feishu_enabled": feishu_enabled,
            "feishu_status": "error"
            if feishu_health["errors"]
            else ("running" if feishu_enabled else "disabled"),
            "feishu_integrations": feishu_health["total"],
            "feishu_enabled_integrations": feishu_health["enabled"],
            # Keep the compatibility field but never expose the raw runtime
            # exception from this unauthenticated endpoint.
            "feishu_error": (
                "部分飞书机器人配置异常"
                if feishu_health["errors"]
                else None
            ),
            "feishu_error_code": (
                "feishu.integration_error"
                if feishu_health["errors"]
                else None
            ),
        }

    @app.get("/api/v1/accounts", dependencies=[Depends(require_token)])
    def accounts() -> list[dict[str, Any]]:
        return batch_service.list_accounts()

    @app.get("/api/v1/models", dependencies=[Depends(require_token)])
    def available_models(
        purpose: str = Query(default="text", pattern="^(text|image)$"),
        enabled_only: bool = Query(default=True),
    ) -> list[dict[str, Any]]:
        """Models users may select; credentials are always removed."""

        return configuration_service.list_models(
            enabled_only=enabled_only,
            purpose=purpose,
            include_config=False,
        )

    @app.post("/api/v1/models", dependencies=[Depends(require_token)])
    def save_user_model(payload: AdminModelRequest) -> dict[str, Any]:
        """Create or update one model owned by the authenticated user."""

        if is_image_provider(payload.provider_type):
            raise HTTPException(
                status_code=400,
                detail="个人模型配置目前仅支持文本大模型",
            )
        try:
            return configuration_service.save_model(
                model_id=payload.id,
                name=payload.name,
                provider_type=payload.provider_type,
                api_base=payload.api_base,
                model=payload.model,
                api_key=payload.api_key,
                local_agent_id=payload.local_agent_id,
                enabled=payload.enabled,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post(
        "/api/v1/models/{model_id}/test",
        dependencies=[Depends(require_token)],
    )
    def test_user_model(model_id: str) -> dict[str, Any]:
        try:
            model = configuration_service.get_model(model_id)
            if not bool(model.get("editable")):
                raise HTTPException(
                    status_code=403,
                    detail="平台公共模型只能由管理员测试",
                )
            return configuration_service.test_model(model_id)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete(
        "/api/v1/models/{model_id}",
        dependencies=[Depends(require_token)],
    )
    def delete_user_model(model_id: str) -> dict[str, Any]:
        try:
            return configuration_service.delete_model(model_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/api/v1/onboarding/status",
        dependencies=[Depends(require_token)],
    )
    def onboarding_status() -> dict[str, Any]:
        """Expose setup readiness without returning credentials or secrets."""

        return onboarding_service.status()

    @app.post("/api/v1/accounts/preflight", dependencies=[Depends(require_token)])
    def preflight_accounts_endpoint(
        account_ids: list[str],
        deep_model_check: bool = Query(default=False),
        force_wechat_check: bool = Query(default=False),
    ) -> list[dict[str, Any]]:
        return batch_service.preflight(
            account_ids,
            deep_model_check=deep_model_check,
            force_wechat_check=force_wechat_check,
        )

    @app.get(
        "/api/v1/wechat/connection-health",
        dependencies=[Depends(require_token)],
    )
    def wechat_connection_health(
        account_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        account_ids = (
            [str(account_id)]
            if account_id
            else [str(item["id"]) for item in batch_service.list_accounts()]
        )
        items: list[dict[str, Any]] = []
        for current_id in account_ids:
            health = batch_service.db.get_wechat_connection_health(
                current_id
            ) or {
                "status": "unknown",
                "mode": "direct",
                "checked_at": None,
                "expires_at": None,
                "latency_ms": None,
                "last_error_code": None,
                "error": None,
                "last_successful_write_at": None,
                "details": {},
            }
            health.pop("details_json", None)
            items.append({"account_id": current_id, **health})
        return {"items": items}

    @app.get("/api/v1/topics/hot", dependencies=[Depends(require_token)])
    def recent_hot_topics(
        limit: int = Query(default=10, ge=1, le=30),
        refresh: bool = Query(default=True),
    ) -> dict[str, Any]:
        if refresh:
            topic_service.refresh()
        items = topic_service.list_topics(days=7, limit=limit)
        return {"days": 7, "count": len(items), "items": items}

    @app.get("/api/v1/topic-sources", dependencies=[Depends(require_token)])
    def list_topic_sources(enabled_only: bool = Query(default=False)) -> list[dict[str, Any]]:
        return topic_service.list_sources(enabled_only=enabled_only)

    @app.post("/api/v1/topic-sources", dependencies=[Depends(require_token)])
    def save_topic_source(payload: TopicSourceRequest) -> dict[str, Any]:
        try:
            return topic_service.save_source(payload.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/v1/topic-sources/{source_id}", dependencies=[Depends(require_token)])
    def delete_topic_source(source_id: str) -> dict[str, bool]:
        try:
            topic_service.delete_source(source_id)
            return {"ok": True}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/topic-sources/refresh", dependencies=[Depends(require_token)])
    def refresh_topic_sources(source_ids: list[str] = Query(default=[])) -> dict[str, Any]:
        return topic_service.refresh(source_ids or None)

    @app.get("/api/v1/topics", dependencies=[Depends(require_token)])
    def list_topics(
        source_ids: list[str] = Query(default=[]),
        days: int = Query(default=7, ge=1, le=365),
        keyword: str = "",
        favorite_only: bool = False,
        unused_only: bool = False,
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        return topic_service.list_topics(
            source_ids=source_ids or None,
            days=days,
            keyword=keyword,
            favorite_only=favorite_only,
            unused_only=unused_only,
            limit=limit,
        )

    @app.get("/api/v1/topics/search", dependencies=[Depends(require_token)])
    def search_topics(
        keyword: str = Query(min_length=1),
        source_ids: list[str] = Query(default=[]),
        days: int = Query(default=7, ge=1, le=365),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        try:
            return topic_service.search(
                keyword,
                source_ids or None,
                days=days,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/topics/manual", dependencies=[Depends(require_token)])
    def add_manual_topic(payload: ManualTopicRequest) -> dict[str, Any]:
        try:
            return topic_service.add_manual_topic(**payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/followed-accounts", dependencies=[Depends(require_token)])
    def list_followed_accounts(enabled_only: bool = Query(default=False)) -> list[dict[str, Any]]:
        return followed_service.list_accounts(enabled_only=enabled_only)

    @app.post("/api/v1/followed-accounts", dependencies=[Depends(require_token)])
    def save_followed_account(payload: FollowedAccountRequest) -> dict[str, Any]:
        try:
            return followed_service.save_account(payload.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/v1/followed-accounts/{account_id}", dependencies=[Depends(require_token)])
    def delete_followed_account(account_id: str) -> dict[str, bool]:
        followed_service.delete_account(account_id)
        return {"ok": True}

    @app.post("/api/v1/followed-accounts/{account_id}/refresh", dependencies=[Depends(require_token)])
    def refresh_followed_account(account_id: str) -> dict[str, Any]:
        try:
            return followed_service.discover_account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/followed-accounts/refresh", dependencies=[Depends(require_token)])
    def refresh_all_followed_accounts() -> dict[str, Any]:
        return followed_service.discover_all()

    @app.get("/api/v1/followed-articles", dependencies=[Depends(require_token)])
    def list_followed_articles(
        account_ids: list[str] = Query(default=[]),
        days: int = Query(default=7, ge=1, le=3650),
        keyword: str = "",
        unread_only: bool = False,
        favorite_only: bool = False,
        unrewritten_only: bool = False,
        include_ignored: bool = False,
        limit: int = Query(default=500, ge=1, le=2000),
    ) -> list[dict[str, Any]]:
        return followed_service.list_articles(
            account_ids=account_ids or None,
            days=days,
            keyword=keyword,
            unread_only=unread_only,
            favorite_only=favorite_only,
            unrewritten_only=unrewritten_only,
            include_ignored=include_ignored,
            limit=limit,
        )

    @app.post("/api/v1/followed-articles", dependencies=[Depends(require_token)])
    def add_followed_article(payload: FollowedArticleRequest) -> dict[str, Any]:
        try:
            return followed_service.add_article_url(**payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/v1/followed-articles/{article_id}", dependencies=[Depends(require_token)])
    def update_followed_article(
        article_id: str, payload: FollowedArticleStateRequest
    ) -> dict[str, Any]:
        try:
            return followed_service.update_article(
                article_id, **payload.model_dump(exclude_unset=True)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_token)],
    )
    def create_batch(payload: CreateBatchRequest) -> dict[str, Any]:
        try:
            return batch_service.create_batch(**payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/batches/{batch_id}", dependencies=[Depends(require_token)])
    def get_batch(
        batch_id: str,
        include_content: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            return batch_service.get_batch(batch_id, include_content=include_content)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/batches", dependencies=[Depends(require_token)])
    def list_batches(
        limit: int = Query(default=100, ge=1, le=500),
        include_archived: bool = Query(default=False),
    ) -> list[dict[str, Any]]:
        return batch_service.list_batches(
            limit=limit, include_archived=include_archived
        )

    @app.get(
        "/api/v1/review-inbox",
        dependencies=[Depends(require_token)],
    )
    def review_inbox(
        bucket: str = Query(default="review"),
        account_id: str | None = Query(default=None),
        search: str = Query(default="", max_length=200),
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            return batch_service.list_review_inbox(
                bucket=bucket,
                account_id=account_id,
                search=search,
                limit=limit,
                cursor=cursor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/attempts",
        dependencies=[Depends(require_token)],
    )
    def list_job_attempts(
        batch_id: str,
        job_id: int,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        try:
            return batch_service.list_job_attempts(
                batch_id, job_id, limit=limit
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/retry",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_token)],
    )
    def retry_job(
        batch_id: str,
        job_id: int,
        payload: RetryJobRequest,
    ) -> dict[str, Any]:
        try:
            return batch_service.retry_job(
                batch_id,
                job_id,
                **payload.model_dump(),
            )
        except KeyError as exc:
            raise _structured_http_error(
                404,
                exc,
                stage=payload.step if payload.step != "auto" else "retry",
            ) from exc
        except ValueError as exc:
            raise _structured_http_error(
                409,
                exc,
                stage=payload.step if payload.step != "auto" else "retry",
            ) from exc

    @app.put(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/selection",
        dependencies=[Depends(require_token)],
    )
    def select_job(
        batch_id: str,
        job_id: int,
        payload: SelectJobRequest,
    ) -> dict[str, Any]:
        try:
            return batch_service.select_job(batch_id, job_id, **payload.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/view",
        dependencies=[Depends(require_token)],
    )
    def view_job(batch_id: str, job_id: int) -> dict[str, Any]:
        try:
            return batch_service.mark_job_viewed(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/confirm",
        dependencies=[Depends(require_token)],
    )
    def confirm_job(batch_id: str, job_id: int) -> dict[str, Any]:
        try:
            return batch_service.confirm_job(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/needs-changes",
        dependencies=[Depends(require_token)],
    )
    def request_job_changes(batch_id: str, job_id: int) -> dict[str, Any]:
        try:
            return batch_service.request_job_changes(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/content",
        dependencies=[Depends(require_token)],
    )
    def update_job_content(
        batch_id: str, job_id: int, payload: UpdateJobContentRequest
    ) -> dict[str, Any]:
        try:
            return batch_service.update_job_content(
                batch_id, job_id, **payload.model_dump()
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/rerender",
        dependencies=[Depends(require_token)],
    )
    def rerender_job(batch_id: str, job_id: int) -> dict[str, Any]:
        try:
            return batch_service.rerender_job(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/paragraph",
        dependencies=[Depends(require_token)],
    )
    def regenerate_paragraph(
        batch_id: str, job_id: int, payload: RegenerateParagraphRequest
    ) -> dict[str, Any]:
        try:
            return batch_service.regenerate_paragraph(
                batch_id,
                job_id,
                payload.paragraph_index,
                instruction=payload.instruction,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/inline-images/regenerate",
        dependencies=[Depends(require_token)],
    )
    def regenerate_job_inline_images(batch_id: str, job_id: int) -> dict[str, Any]:
        try:
            return batch_service.regenerate_inline_images(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/inline-images/{image_index}/regenerate",
        dependencies=[Depends(require_token)],
    )
    def regenerate_job_inline_image(
        batch_id: str,
        job_id: int,
        image_index: int,
        payload: RegenerateInlineImageRequest,
    ) -> dict[str, Any]:
        try:
            return batch_service.regenerate_inline_image(
                batch_id,
                job_id,
                image_index,
                instruction=payload.instruction,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/inline-images/{image_index}",
        dependencies=[Depends(require_token)],
    )
    def remove_job_inline_image(
        batch_id: str, job_id: int, image_index: int
    ) -> dict[str, Any]:
        try:
            return batch_service.remove_inline_image(batch_id, job_id, image_index)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/versions",
        dependencies=[Depends(require_token)],
    )
    def list_job_versions(batch_id: str, job_id: int) -> list[dict[str, Any]]:
        try:
            return batch_service.list_job_versions(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/versions/{version_id}/restore",
        dependencies=[Depends(require_token)],
    )
    def restore_job_version(
        batch_id: str, job_id: int, version_id: int
    ) -> dict[str, Any]:
        try:
            return batch_service.restore_job_version(batch_id, job_id, version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/covers",
        dependencies=[Depends(require_token)],
    )
    def list_job_covers(
        batch_id: str,
        job_id: int,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, str]]:
        try:
            return batch_service.list_cover_options(
                batch_id, job_id, limit=limit, offset=offset
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/cover",
        dependencies=[Depends(require_token)],
    )
    def select_job_cover(
        batch_id: str, job_id: int, payload: SelectCoverRequest
    ) -> dict[str, Any]:
        try:
            return batch_service.select_job_cover(
                batch_id, job_id, payload.thumb_media_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/cover/generate",
        dependencies=[Depends(require_token)],
    )
    def regenerate_job_cover(
        batch_id: str,
        job_id: int,
        payload: RegenerateCoverRequest | None = None,
    ) -> dict[str, Any]:
        try:
            return batch_service.regenerate_cover(
                batch_id,
                job_id,
                instruction=payload.instruction if payload else "",
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/drafts",
        dependencies=[Depends(require_token)],
    )
    def inject_batch(batch_id: str) -> dict[str, Any]:
        try:
            return batch_service.inject_batch(batch_id)
        except KeyError as exc:
            raise _structured_http_error(404, exc, stage="inject") from exc
        except ValueError as exc:
            raise _structured_http_error(409, exc, stage="inject") from exc

    @app.post(
        "/api/v1/batches/{batch_id}/cancel",
        dependencies=[Depends(require_token)],
    )
    def cancel_batch(batch_id: str) -> dict[str, Any]:
        try:
            return batch_service.cancel_batch(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/retry-failed",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_token)],
    )
    def retry_failed(batch_id: str) -> dict[str, Any]:
        try:
            return batch_service.retry_failed(batch_id)
        except KeyError as exc:
            raise _structured_http_error(404, exc, stage="retry") from exc
        except ValueError as exc:
            raise _structured_http_error(409, exc, stage="retry") from exc

    @app.post(
        "/api/v1/batches/{batch_id}/copy",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_token)],
    )
    def copy_batch(batch_id: str) -> dict[str, Any]:
        try:
            return batch_service.copy_batch(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/archive",
        dependencies=[Depends(require_token)],
    )
    def archive_batch(batch_id: str) -> dict[str, Any]:
        try:
            return batch_service.archive_batch(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


def main() -> None:
    cfg = load_config()
    api_cfg = dict(cfg.get("api") or {})
    port_override = str(os.getenv("WECHAT_PUBLISHER_API_PORT") or "").strip()
    uvicorn.run(
        create_api_app(cfg),
        host=str(api_cfg.get("host") or "127.0.0.1"),
        port=int(port_override or api_cfg.get("port") or 18766),
        log_level=str(api_cfg.get("log_level") or "info"),
        log_config=None,
    )


if __name__ == "__main__":
    main()
