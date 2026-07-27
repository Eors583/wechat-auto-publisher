from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.feishu.gateway import FeishuGateway


class _Response:
    def __init__(self, *, image_key: str = "", success: bool = True) -> None:
        self.code = 0 if success else 230001
        self.msg = "ok" if success else "failed"
        self.data = SimpleNamespace(image_key=image_key) if image_key else None
        self._success = success

    def success(self) -> bool:
        return self._success


class _ImageResource:
    def __init__(self) -> None:
        self.requests: list[object] = []
        self.uploads: list[tuple[str, bytes]] = []

    def create(self, request: object) -> _Response:
        self.requests.append(request)
        stream = request.request_body.image
        self.uploads.append((Path(stream.name).name, stream.read()))
        return _Response(image_key="img_v2_test")


class _MessageResource:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def create(self, request: object) -> _Response:
        self.requests.append(request)
        return _Response()


def _gateway() -> tuple[FeishuGateway, _ImageResource, _MessageResource]:
    image_resource = _ImageResource()
    message_resource = _MessageResource()
    gateway = FeishuGateway.__new__(FeishuGateway)
    gateway.client = SimpleNamespace(
        im=SimpleNamespace(
            v1=SimpleNamespace(image=image_resource, message=message_resource)
        )
    )
    return gateway, image_resource, message_resource


def test_send_image_uploads_bytes_and_creates_image_message() -> None:
    gateway, images, messages = _gateway()

    image_key = gateway.send_image(
        "oc_chat_123", b"\x89PNG\r\n\x1a\nimage", file_name="cover.png"
    )

    assert image_key == "img_v2_test"
    assert images.uploads == [("cover.png", b"\x89PNG\r\n\x1a\nimage")]
    upload_body = images.requests[0].request_body
    assert upload_body.image_type == "message"

    request = messages.requests[0]
    assert request.receive_id_type == "chat_id"
    assert request.request_body.receive_id == "oc_chat_123"
    assert request.request_body.msg_type == "image"
    assert json.loads(request.request_body.content) == {"image_key": "img_v2_test"}


def test_send_image_accepts_a_local_file(tmp_path: Path) -> None:
    gateway, images, messages = _gateway()
    image_path = tmp_path / "local-image.webp"
    image_path.write_bytes(b"RIFF-test-webp")

    gateway.send_image("oc_chat_456", image_path)

    assert images.uploads == [("local-image.webp", b"RIFF-test-webp")]
    assert messages.requests[0].request_body.receive_id == "oc_chat_456"


def test_upload_image_rejects_a_missing_local_file(tmp_path: Path) -> None:
    gateway, _, _ = _gateway()

    with pytest.raises(FileNotFoundError):
        gateway.upload_image(tmp_path / "missing.png")
