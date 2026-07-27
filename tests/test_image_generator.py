from __future__ import annotations

from io import BytesIO
import json

import httpx
from PIL import Image

from app.ai import image_generator
from app.ai.image_providers import (
    IMAGE_ALIBABA,
    IMAGE_MINIMAX,
    IMAGE_VOLCENGINE,
    IMAGE_ZHIPU,
)


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (120, 80), (20, 110, 150)).save(buffer, format="PNG")
    return buffer.getvalue()


def _install_transport(monkeypatch, handler) -> None:
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(image_generator.httpx, "Client", client_factory)


def test_detects_alibaba_model_studio_native_endpoint() -> None:
    endpoint = (
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/"
        "aigc/multimodal-generation/generation"
    )
    assert image_generator.detect_image_protocol(endpoint) == "dashscope"
    assert (
        image_generator.detect_image_protocol("https://api.openai.com/v1")
        == "openai"
    )


def test_qwen_image_uses_native_payload_and_downloads_result(tmp_path, monkeypatch) -> None:
    endpoint = (
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/"
        "aigc/multimodal-generation/generation"
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["url"] = str(request.url)
            captured["authorization"] = request.headers.get("Authorization")
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "output": {
                        "choices": [
                            {
                                "message": {
                                    "content": [
                                        {"image": "https://signed.example.test/generated.png"}
                                    ]
                                }
                            }
                        ]
                    }
                },
            )
        assert str(request.url) == "https://signed.example.test/generated.png"
        return httpx.Response(200, content=_png_bytes(), headers={"Content-Type": "image/png"})

    _install_transport(monkeypatch, handler)
    target = image_generator.generate_image(
        api_key="secret-key",
        api_base=endpoint,
        model="qwen-image-2.0-pro-2026-04-22",
        prompt="企业团队讨论增长策略",
        output_path=tmp_path / "result.jpg",
    )

    assert captured["url"] == endpoint
    assert captured["authorization"] == "Bearer secret-key"
    payload = captured["payload"]
    assert payload["input"]["messages"][0]["content"][0]["text"] == "企业团队讨论增长策略"
    assert payload["parameters"]["size"] == "1920*1080"
    assert payload["parameters"]["watermark"] is False
    assert payload["parameters"]["prompt_extend"] is False
    with Image.open(target) as result:
        assert result.format == "JPEG"
        assert result.size == (120, 80)


def test_openai_full_generation_endpoint_is_not_duplicated(tmp_path, monkeypatch) -> None:
    endpoint = "https://images.example.test/v1/images/generations"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"url": "https://images.example.test/result.png"}
                    ]
                },
            )
        return httpx.Response(200, content=_png_bytes())

    _install_transport(monkeypatch, handler)
    image_generator.generate_image(
        api_key="secret-key",
        api_base=endpoint,
        model="image-model",
        prompt="test",
        output_path=tmp_path / "result.jpg",
    )

    assert seen[0] == endpoint
    assert "/images/generations/images/generations" not in seen[0]


def test_minimax_template_uses_native_payload_and_image_urls(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content)
            captured["authorization"] = request.headers.get("Authorization")
            return httpx.Response(
                200,
                json={
                    "data": {"image_urls": ["https://minimax.example.test/result.png"]},
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                },
            )
        return httpx.Response(200, content=_png_bytes())

    _install_transport(monkeypatch, handler)
    target = image_generator.generate_image(
        api_key="minimax-key",
        api_base="",
        provider_type=IMAGE_MINIMAX,
        model="image-01",
        prompt="与企业战略执行高度相关的写实场景",
        output_path=tmp_path / "minimax.jpg",
    )

    assert captured["url"] == "https://api.minimaxi.com/v1/image_generation"
    assert captured["authorization"] == "Bearer minimax-key"
    assert captured["payload"]["aspect_ratio"] == "16:9"
    assert captured["payload"]["response_format"] == "url"
    assert target.exists()


def test_alibaba_template_creates_and_polls_async_task(tmp_path, monkeypatch) -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, str(request.url)))
        if request.method == "POST":
            assert request.headers.get("X-DashScope-Async") == "enable"
            payload = json.loads(request.content)
            assert payload["model"] == "wan2.6-t2i"
            assert payload["parameters"]["n"] == 1
            return httpx.Response(
                200,
                json={"output": {"task_id": "task-1", "task_status": "PENDING"}},
            )
        if "/api/v1/tasks/task-1" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "output": {
                        "task_id": "task-1",
                        "task_status": "SUCCEEDED",
                        "results": [{"url": "https://aliyun.example.test/result.png"}],
                    }
                },
            )
        return httpx.Response(200, content=_png_bytes())

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(image_generator.time, "sleep", lambda _seconds: None)
    target = image_generator.generate_image(
        api_key="dashscope-key",
        api_base="",
        provider_type=IMAGE_ALIBABA,
        model="wan2.6-t2i",
        prompt="企业运营团队分析供应链风险",
        output_path=tmp_path / "alibaba.jpg",
    )

    assert requests[0][1].endswith("/aigc/text2image/image-synthesis")
    assert any("/api/v1/tasks/task-1" in url for _, url in requests)
    assert target.exists()


def test_volcengine_template_uses_seedream_fields(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"data": [{"url": "https://volc.example.test/result.png"}]},
            )
        return httpx.Response(200, content=_png_bytes())

    _install_transport(monkeypatch, handler)
    image_generator.generate_image(
        api_key="ark-key",
        api_base="",
        provider_type=IMAGE_VOLCENGINE,
        model="doubao-seedream-4-0-250828",
        prompt="真实的制造业数字化场景",
        output_path=tmp_path / "seedream.jpg",
    )
    assert captured["url"] == "https://ark.cn-beijing.volces.com/api/v3/images/generations"
    assert captured["payload"]["sequential_image_generation"] == "disabled"
    assert captured["payload"]["watermark"] is False


def test_zhipu_template_uses_official_image_endpoint(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"data": [{"url": "https://zhipu.example.test/result.png"}]},
            )
        return httpx.Response(200, content=_png_bytes())

    _install_transport(monkeypatch, handler)
    image_generator.generate_image(
        api_key="zhipu-key",
        api_base="",
        provider_type=IMAGE_ZHIPU,
        model="glm-image",
        prompt="企业协作的纪实摄影场景",
        output_path=tmp_path / "zhipu.jpg",
    )
    assert captured["url"] == "https://open.bigmodel.cn/api/paas/v4/images/generations"
    assert captured["payload"]["size"] == "1440x720"


def test_qwen_connection_check_does_not_generate_a_billable_image() -> None:
    message = image_generator.test_image_endpoint(
        api_key="secret-key",
        api_base=(
            "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/"
            "aigc/multimodal-generation/generation"
        ),
        model="qwen-image-2.0-pro",
    )
    assert "阿里云千问生图" in message
    assert "生成测试图" in message


def test_rate_limit_is_retried_until_image_succeeds(tmp_path, monkeypatch) -> None:
    endpoint = (
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/"
        "aigc/multimodal-generation/generation"
    )
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.method == "POST":
            attempts += 1
            if attempts < 3:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "0"},
                    json={
                        "message": "Requests rate limit exceeded, please try again later.",
                        "request_id": f"rate-{attempts}",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "output": {
                        "choices": [
                            {
                                "message": {
                                    "content": [
                                        {"image": "https://signed.example.test/retried.png"}
                                    ]
                                }
                            }
                        ]
                    }
                },
            )
        return httpx.Response(200, content=_png_bytes())

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(image_generator.time, "sleep", sleeps.append)
    target = image_generator.generate_image(
        api_key="rate-limit-key",
        api_base=endpoint,
        model="qwen-image-2.0-pro",
        prompt="真实商业现场",
        output_path=tmp_path / "retried.jpg",
    )

    assert attempts == 3
    assert sleeps == [0.0, 0.0]
    assert target.exists()


def test_non_rate_limit_provider_error_is_not_retried(tmp_path, monkeypatch) -> None:
    endpoint = (
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/"
        "aigc/multimodal-generation/generation"
    )
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"message": "invalid api key"})

    _install_transport(monkeypatch, handler)
    try:
        image_generator.generate_image(
            api_key="invalid-key",
            api_base=endpoint,
            model="qwen-image-2.0-pro",
            prompt="test",
            output_path=tmp_path / "must-not-exist.jpg",
        )
    except image_generator.ImageProviderError as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("401 must be surfaced without retry")
    assert attempts == 1


def test_exhausted_rate_limit_reports_automatic_retry(tmp_path, monkeypatch) -> None:
    endpoint = (
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/"
        "aigc/multimodal-generation/generation"
    )
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "0"},
            json={"message": "Requests rate limit exceeded"},
        )

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(image_generator.time, "sleep", lambda _seconds: None)
    try:
        image_generator.generate_image(
            api_key="still-limited-key",
            api_base=endpoint,
            model="qwen-image-2.0-pro",
            prompt="test",
            output_path=tmp_path / "limited.jpg",
        )
    except image_generator.ImageProviderError as exc:
        assert "自动等待" in str(exc)
        assert "重试 5 次" in str(exc)
    else:
        raise AssertionError("persistent 429 must eventually fail")
    assert attempts == 5
