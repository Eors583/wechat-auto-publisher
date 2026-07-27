from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import lark_oapi as lark
from lark_oapi import ws
from lark_oapi.api.im.v1 import (
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)


logger = logging.getLogger("uvicorn.error")


class FeishuGateway:
    """Transport adapter around lark-oapi; contains no application workflow."""

    def __init__(self, app_id: str, app_secret: str, settings: dict[str, Any]) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.settings = settings
        self.client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

    def start(self, on_message: Callable[[Any], None]) -> None:
        handler = (
            lark.EventDispatcherHandler.builder(
                str(self.settings.get("encrypt_key") or ""),
                str(self.settings.get("verification_token") or ""),
                lark.LogLevel.INFO,
            )
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )
        ws.Client(
            self.app_id,
            self.app_secret,
            log_level=lark.LogLevel.INFO,
            event_handler=handler,
        ).start()

    def reply_text(self, message_id: str, text: str) -> None:
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.reply(request)
        if not response.success():
            logger.error("Feishu reply failed: code=%s msg=%s", response.code, response.msg)
            raise RuntimeError(f"飞书回复失败（{response.code}）：{response.msg}")
        else:
            logger.info("Feishu reply succeeded: message_id=%s", message_id)

    def send_text(self, chat_id: str, text: str) -> None:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        if not response.success():
            logger.error("Feishu send failed: code=%s msg=%s", response.code, response.msg)
            raise RuntimeError(f"飞书发送失败（{response.code}）：{response.msg}")
        else:
            logger.info("Feishu send succeeded: chat_id=%s", chat_id)

    def upload_image(
        self,
        image: str | Path | bytes | bytearray | memoryview,
        *,
        file_name: str | None = None,
    ) -> str:
        """Upload a local image or in-memory image and return its image key."""

        if isinstance(image, (bytes, bytearray, memoryview)):
            stream = io.BytesIO(bytes(image))
            stream.name = file_name or "image.png"
        elif isinstance(image, (str, Path)):
            image_path = Path(image).expanduser()
            if not image_path.is_file():
                raise FileNotFoundError(f"Image file not found: {image_path}")
            stream = image_path.open("rb")
        else:
            raise TypeError("image must be a local path or image bytes")

        try:
            request = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(stream)
                    .build()
                )
                .build()
            )
            response = self.client.im.v1.image.create(request)
        finally:
            stream.close()

        if not response.success():
            logger.error(
                "Feishu image upload failed: code=%s msg=%s",
                response.code,
                response.msg,
            )
            raise RuntimeError(
                f"Feishu image upload failed ({response.code}): {response.msg}"
            )

        image_key = str(getattr(getattr(response, "data", None), "image_key", "") or "")
        if not image_key:
            raise RuntimeError("Feishu image upload succeeded without an image_key")
        logger.info("Feishu image upload succeeded: image_key=%s", image_key)
        return image_key

    def send_image(
        self,
        chat_id: str,
        image: str | Path | bytes | bytearray | memoryview,
        *,
        file_name: str | None = None,
    ) -> str:
        """Upload and send an image message to a Feishu chat."""

        image_key = self.upload_image(image, file_name=file_name)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("image")
                .content(json.dumps({"image_key": image_key}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = self.client.im.v1.message.create(request)
        if not response.success():
            logger.error(
                "Feishu image send failed: code=%s msg=%s",
                response.code,
                response.msg,
            )
            raise RuntimeError(
                f"Feishu image send failed ({response.code}): {response.msg}"
            )
        logger.info("Feishu image send succeeded: chat_id=%s", chat_id)
        return image_key
