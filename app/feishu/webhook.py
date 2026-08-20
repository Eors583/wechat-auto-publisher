from __future__ import annotations

import logging
import json
from typing import Any

import lark_oapi as lark
from lark_oapi.core.const import (
    LARK_REQUEST_NONCE,
    LARK_REQUEST_SIGNATURE,
    LARK_REQUEST_TIMESTAMP,
    X_REQUEST_ID,
)
from lark_oapi.core.model import RawRequest, RawResponse

from app.feishu.bot import FeishuBot
from app.feishu.runtime import utc_now
from app.services import BatchService
from app.services.feishu_integrations import FeishuIntegrationService


logger = logging.getLogger("uvicorn.error")


class FeishuWebhookProcessor:
    """Route one dedicated callback to its owner-scoped Feishu bot."""

    def __init__(
        self,
        base_service: BatchService,
        config: dict[str, Any],
    ) -> None:
        self.base_service = base_service
        self.config = dict(config)
        self.integrations = FeishuIntegrationService(base_service.db, config)

    def handle(
        self,
        callback_key: str,
        *,
        uri: str,
        headers: dict[str, str],
        body: bytes,
    ) -> RawResponse:
        integration = self.integrations.effective_for_callback(callback_key)
        if not integration or not bool(integration.get("enabled")):
            return self._json_response(404, b'{"msg":"integration not found"}')

        owner_user_id = str(integration.get("owner_user_id") or "")
        app_id = str(integration.get("app_id") or "")
        service = self.base_service._for_user(owner_user_id)
        feishu = {
            "enabled": True,
            "integration_id": str(integration.get("id") or ""),
            "owner_user_id": owner_user_id,
            "app_id": app_id,
            "app_secret": str(integration.get("app_secret") or ""),
            "verification_token": str(integration.get("verification_token") or ""),
            "encrypt_key": str(integration.get("encrypt_key") or ""),
            "bound_open_id": str(integration.get("bound_open_id") or ""),
            "allowed_open_ids": [str(integration.get("bound_open_id") or "")]
            if integration.get("bound_open_id")
            else [],
            "default_account_ids": list(
                integration.get("default_account_ids") or []
            ),
            "allowed_account_ids": list(
                integration.get("allowed_account_ids") or []
            ),
            "agent_model_id": str(integration.get("agent_model_id") or ""),
        }
        bot_config = {**self.config, "feishu": feishu}
        bot = FeishuBot(bot_config, service)

        def on_message(data: Any) -> None:
            event_app_id = str(
                getattr(getattr(data, "header", None), "app_id", "") or ""
            )
            if not event_app_id or event_app_id != app_id:
                raise PermissionError("飞书事件 App ID 与专属机器人不匹配")
            bot._on_message_event(data)

        handler = (
            lark.EventDispatcherHandler.builder(
                feishu["encrypt_key"],
                feishu["verification_token"],
                lark.LogLevel.WARNING,
            )
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )
        request = RawRequest()
        request.uri = str(uri)
        request.body = bytes(body)
        request.headers = self._sdk_headers(headers)
        response = handler.do(request)
        if int(response.status_code or 500) < 400:
            service.db.update_feishu_runtime(
                str(integration.get("id") or ""),
                {
                    "status": "running",
                    "callback_verified_at": utc_now()
                    if b'"challenge"' in (response.content or b"")
                    else str(
                        (
                            self._runtime(integration).get(
                                "callback_verified_at"
                            )
                            or ""
                        )
                    ),
                    "last_error": "",
                },
            )
        else:
            service.db.update_feishu_runtime(
                str(integration.get("id") or ""),
                {"status": "error", "last_error": "飞书回调校验失败"},
            )
        return response

    @staticmethod
    def _sdk_headers(headers: dict[str, str]) -> dict[str, str]:
        lower = {str(key).casefold(): str(value) for key, value in headers.items()}
        result = dict(headers)
        for canonical in (
            LARK_REQUEST_TIMESTAMP,
            LARK_REQUEST_NONCE,
            LARK_REQUEST_SIGNATURE,
            X_REQUEST_ID,
        ):
            result[canonical] = lower.get(canonical.casefold(), "")
        return result

    @staticmethod
    def _json_response(status_code: int, content: bytes) -> RawResponse:
        response = RawResponse()
        response.status_code = int(status_code)
        response.content = bytes(content)
        response.set_content_type("application/json; charset=utf-8")
        return response

    @staticmethod
    def _runtime(integration: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(str(integration.get("runtime_json") or "{}"))
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}


__all__ = ["FeishuWebhookProcessor"]
