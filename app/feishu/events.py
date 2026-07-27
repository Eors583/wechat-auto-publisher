from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class IncomingFeishuMessage:
    event_id: str
    message_id: str
    chat_id: str
    open_id: str
    message_type: str
    text: str


def parse_message_event(data: Any) -> IncomingFeishuMessage:
    event = data.event
    message = event.message
    sender_id = event.sender.sender_id
    message_id = str(message.message_id or "")
    event_id = str(
        getattr(getattr(data, "header", None), "event_id", "") or message_id
    )
    message_type = getattr(message, "message_type", None) or getattr(
        message, "msg_type", None
    )
    raw_content = getattr(message, "content", None)
    if raw_content is None:
        raw_content = getattr(getattr(message, "body", None), "content", None)
    text = ""
    if str(message_type or "") == "text":
        payload = json.loads(str(raw_content or "{}"))
        text = re.sub(r"@_user_\d+", "", str(payload.get("text") or "")).strip()
    return IncomingFeishuMessage(
        event_id=event_id,
        message_id=message_id,
        chat_id=str(message.chat_id or ""),
        open_id=str(sender_id.open_id or ""),
        message_type=str(message_type or ""),
        text=text,
    )
