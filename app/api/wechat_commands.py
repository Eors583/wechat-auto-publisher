from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, Response

from app.services.batches import BatchService
from app.services.failures import sanitize_failure_text
from app.services.wechat_commands import WeChatCommandService
from app.wechat.messages import (
    WeChatMessageCipher,
    encrypted_payload,
    parse_message_xml,
    render_text_reply,
    verify_message_signature,
)

logger = logging.getLogger(__name__)


def create_wechat_command_router(
    batch_service: BatchService,
    config: dict[str, Any],
) -> APIRouter:
    router = APIRouter()

    def command_service(owner_user_id: str, account_id: str) -> WeChatCommandService:
        service = WeChatCommandService(
            batch_service.db.for_user(str(owner_user_id or "").strip()),
            config,
        )
        try:
            service.public_settings(account_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="接入不存在"
            ) from exc
        return service

    def load_enabled(
        owner_user_id: str,
        account_id: str,
    ) -> tuple[WeChatCommandService, dict[str, Any]]:
        service = command_service(owner_user_id, account_id)
        settings = service.effective_settings(account_id)
        if not bool(settings.get("enabled", False)):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="接入未启用"
            )
        if not settings.get("token"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="接入未配置"
            )
        return service, settings

    @router.get("/api/v1/wechat/commands/{owner_user_id}/{account_id}")
    def verify_callback(
        owner_user_id: str,
        account_id: str,
        signature: str = Query(default=""),
        timestamp: str = Query(default=""),
        nonce: str = Query(default=""),
        echostr: str = Query(default=""),
        msg_signature: str = Query(default="", alias="msg_signature"),
        encrypt_type: str = Query(default="", alias="encrypt_type"),
    ) -> PlainTextResponse:
        _service, settings = load_enabled(owner_user_id, account_id)
        token = str(settings["token"])
        encrypted = bool(msg_signature) or str(encrypt_type).casefold() == "aes"
        if encrypted:
            if not settings.get("encoding_aes_key") or not verify_message_signature(
                msg_signature,
                token,
                timestamp,
                nonce,
                echostr,
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="签名无效"
                )
            try:
                echo = WeChatMessageCipher(
                    str(settings["encoding_aes_key"]),
                    str(settings["app_id"]),
                ).decrypt(echostr)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="验证失败"
                ) from exc
            return PlainTextResponse(echo)
        if not verify_message_signature(signature, token, timestamp, nonce):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="签名无效"
            )
        return PlainTextResponse(echostr)

    @router.post("/api/v1/wechat/commands/{owner_user_id}/{account_id}")
    async def receive_message(
        owner_user_id: str,
        account_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        signature: str = Query(default=""),
        timestamp: str = Query(default=""),
        nonce: str = Query(default=""),
        msg_signature: str = Query(default="", alias="msg_signature"),
        encrypt_type: str = Query(default="", alias="encrypt_type"),
    ) -> Response:
        service, settings = load_enabled(owner_user_id, account_id)
        token = str(settings["token"])
        body = await request.body()
        if len(body) > 512 * 1024:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        raw_xml = body.decode("utf-8", errors="strict")
        encrypted = bool(msg_signature) or str(encrypt_type).casefold() == "aes"
        cipher: WeChatMessageCipher | None = None
        if encrypted:
            try:
                payload = encrypted_payload(raw_xml)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="消息格式无效"
                ) from exc
            if not settings.get("encoding_aes_key") or not verify_message_signature(
                msg_signature,
                token,
                timestamp,
                nonce,
                payload,
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="签名无效"
                )
            try:
                cipher = WeChatMessageCipher(
                    str(settings["encoding_aes_key"]),
                    str(settings["app_id"]),
                )
                raw_xml = cipher.decrypt(payload)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="消息解密失败"
                ) from exc
        elif not verify_message_signature(signature, token, timestamp, nonce):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="签名无效"
            )

        try:
            message = parse_message_xml(raw_xml)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="消息格式无效"
            ) from exc

        def reply(text: str) -> Response:
            response_xml = render_text_reply(message, text)
            if cipher is not None:
                response_xml = cipher.render_encrypted_reply(
                    response_xml,
                    token=token,
                    timestamp=timestamp,
                    nonce=nonce,
                )
            return Response(response_xml, media_type="application/xml")

        event_id = message.message_id or hashlib.sha256(raw_xml.encode()).hexdigest()
        if not service.db.claim_event(
            f"wechat:{owner_user_id}:{account_id}:{event_id}"
        ):
            return PlainTextResponse("success")
        if message.message_type == "event":
            if message.event == "subscribe":
                return reply(
                    "欢迎使用公众号内容智能体。请先发送网页中生成的“绑定 8位数字”口令。"
                )
            return PlainTextResponse("success")
        if message.message_type != "text":
            return reply("目前只支持文本指令；可以直接发送公众号文章链接或“帮助”。")

        authorized, paired = service.authorize_or_pair(
            account_id,
            open_id=message.from_user,
            text=message.content,
        )
        if paired:
            return reply("绑定成功。现在可以发送文章链接，或发送“帮助”查看完整指令。")
        if not authorized:
            return reply(
                "该微信尚未绑定。请在网页“公众号 → 微信指挥”中生成绑定口令后发送。"
            )
        if not message.content:
            return reply("请输入文章链接或改写指令。")

        background_tasks.add_task(
            _run_command_safely,
            service,
            account_id,
            message.from_user,
            message.content,
        )
        return reply("指令已收到，正在后台处理；执行结果会继续发送到当前微信会话。")

    return router


def _run_command_safely(
    service: WeChatCommandService,
    account_id: str,
    open_id: str,
    text: str,
) -> None:
    try:
        service.run_command(account_id, open_id, text)
    except Exception as exc:  # noqa: BLE001
        logger.error("WeChat command failed: %s", sanitize_failure_text(exc))
