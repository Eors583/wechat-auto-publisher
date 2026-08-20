from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


@dataclass(frozen=True, slots=True)
class WeChatMessage:
    to_user: str
    from_user: str
    created_at: int
    message_type: str
    content: str
    message_id: str
    event: str = ""


def message_signature(token: str, *parts: str) -> str:
    payload = "".join(sorted([str(token), *(str(part) for part in parts)]))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def verify_message_signature(signature: str, token: str, *parts: str) -> bool:
    expected = message_signature(token, *parts)
    return bool(signature) and hmac.compare_digest(str(signature), expected)


def parse_message_xml(xml_text: str) -> WeChatMessage:
    try:
        root = ET.fromstring(str(xml_text or ""))
    except ET.ParseError as exc:
        raise ValueError("微信消息 XML 格式无效") from exc
    value = lambda name: str(root.findtext(name) or "").strip()
    try:
        created_at = int(value("CreateTime") or 0)
    except ValueError:
        created_at = 0
    return WeChatMessage(
        to_user=value("ToUserName"),
        from_user=value("FromUserName"),
        created_at=created_at,
        message_type=value("MsgType").casefold(),
        content=value("Content"),
        message_id=value("MsgId") or value("MsgID"),
        event=value("Event").casefold(),
    )


def encrypted_payload(xml_text: str) -> str:
    try:
        root = ET.fromstring(str(xml_text or ""))
    except ET.ParseError as exc:
        raise ValueError("微信消息 XML 格式无效") from exc
    return str(root.findtext("Encrypt") or "").strip()


def render_text_reply(message: WeChatMessage, text: str) -> str:
    root = ET.Element("xml")
    values = {
        "ToUserName": message.from_user,
        "FromUserName": message.to_user,
        "CreateTime": str(int(time.time())),
        "MsgType": "text",
        "Content": str(text or "")[:600],
    }
    for key, value in values.items():
        ET.SubElement(root, key).text = value
    return ET.tostring(root, encoding="unicode", short_empty_elements=False)


class WeChatMessageCipher:
    """Encrypt and decrypt WeChat safe-mode callback payloads."""

    def __init__(self, encoding_aes_key: str, app_id: str) -> None:
        try:
            key = base64.b64decode(str(encoding_aes_key or "").strip() + "=")
        except (ValueError, TypeError) as exc:
            raise ValueError("EncodingAESKey 格式不正确") from exc
        if len(key) != 32:
            raise ValueError("EncodingAESKey 必须是 43 位")
        self.key = key
        self.app_id = str(app_id or "").strip()

    def encrypt(self, plaintext: str) -> str:
        encoded = str(plaintext or "").encode("utf-8")
        app_id = self.app_id.encode("utf-8")
        payload = os.urandom(16) + struct.pack("!I", len(encoded)) + encoded + app_id
        payload = _pkcs7_pad(payload)
        encryptor = Cipher(
            algorithms.AES(self.key), modes.CBC(self.key[:16])
        ).encryptor()
        return base64.b64encode(
            encryptor.update(payload) + encryptor.finalize()
        ).decode()

    def decrypt(self, encrypted: str) -> str:
        try:
            payload = base64.b64decode(str(encrypted or "").strip())
            decryptor = Cipher(
                algorithms.AES(self.key), modes.CBC(self.key[:16])
            ).decryptor()
            decoded = _pkcs7_unpad(decryptor.update(payload) + decryptor.finalize())
        except (ValueError, TypeError) as exc:
            raise ValueError("微信加密消息无法解密") from exc
        if len(decoded) < 20:
            raise ValueError("微信加密消息内容不完整")
        content_size = struct.unpack("!I", decoded[16:20])[0]
        end = 20 + content_size
        content = decoded[20:end]
        app_id = decoded[end:].decode("utf-8", errors="strict")
        if not hmac.compare_digest(app_id, self.app_id):
            raise ValueError("微信消息 AppID 与当前公众号不匹配")
        return content.decode("utf-8", errors="strict")

    def render_encrypted_reply(
        self,
        plaintext_xml: str,
        *,
        token: str,
        timestamp: str | None = None,
        nonce: str | None = None,
    ) -> str:
        timestamp = str(timestamp or int(time.time()))
        nonce = str(nonce or base64.urlsafe_b64encode(os.urandom(9)).decode())
        encrypted = self.encrypt(plaintext_xml)
        signature = message_signature(token, timestamp, nonce, encrypted)
        root = ET.Element("xml")
        for key, value in (
            ("Encrypt", encrypted),
            ("MsgSignature", signature),
            ("TimeStamp", timestamp),
            ("Nonce", nonce),
        ):
            ET.SubElement(root, key).text = value
        return ET.tostring(root, encoding="unicode", short_empty_elements=False)


def _pkcs7_pad(payload: bytes, block_size: int = 32) -> bytes:
    padding = block_size - (len(payload) % block_size)
    return payload + bytes([padding]) * padding


def _pkcs7_unpad(payload: bytes, block_size: int = 32) -> bytes:
    if not payload:
        raise ValueError("empty payload")
    padding = payload[-1]
    if (
        padding < 1
        or padding > block_size
        or payload[-padding:] != bytes([padding]) * padding
    ):
        raise ValueError("invalid padding")
    return payload[:-padding]
