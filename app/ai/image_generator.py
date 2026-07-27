from __future__ import annotations

import base64
from io import BytesIO
import logging
from pathlib import Path
import random
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps

from app.ai.image_providers import (
    IMAGE_ALIBABA,
    IMAGE_CUSTOM,
    IMAGE_MINIMAX,
    IMAGE_VOLCENGINE,
    IMAGE_ZHIPU,
    infer_image_provider,
    resolved_image_endpoint,
)


OPENAI_IMAGE_PROTOCOL = "openai"
DASHSCOPE_IMAGE_PROTOCOL = "dashscope"
MINIMAX_IMAGE_PROTOCOL = "minimax"
VOLCENGINE_IMAGE_PROTOCOL = "volcengine"
ZHIPU_IMAGE_PROTOCOL = "zhipu"
DASHSCOPE_ASYNC_IMAGE_PROTOCOL = "dashscope_async"
_DASHSCOPE_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
_MAX_RATE_LIMIT_ATTEMPTS = 5
_RATE_LIMIT_DELAYS = (5.0, 10.0, 20.0, 40.0)

logger = logging.getLogger(__name__)


class ImageProviderError(RuntimeError):
    """Structured provider error used for safe rate-limit retries."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def detect_image_protocol(api_base: str, provider_type: str | None = None) -> str:
    """Select an image API adapter from the configured endpoint.

    Alibaba Model Studio's native Qwen Image endpoint is not OpenAI compatible.
    Existing records use the generic ``openai_image`` database type, so endpoint
    detection keeps them working without asking the user to re-enter the API key.
    """
    provider = infer_image_provider(provider_type, api_base)
    path = urlparse(api_base.strip()).path.rstrip("/").casefold()
    if provider == IMAGE_MINIMAX:
        return MINIMAX_IMAGE_PROTOCOL
    if provider == IMAGE_VOLCENGINE:
        return VOLCENGINE_IMAGE_PROTOCOL
    if provider == IMAGE_ZHIPU:
        return ZHIPU_IMAGE_PROTOCOL
    if provider == IMAGE_ALIBABA and "text2image/image-synthesis" in path:
        return DASHSCOPE_ASYNC_IMAGE_PROTOCOL
    if provider == IMAGE_ALIBABA:
        return DASHSCOPE_IMAGE_PROTOCOL

    parsed = urlparse(api_base.strip())
    host = (parsed.hostname or "").casefold()
    if (
        "multimodal-generation/generation" in path
        or host.endswith(".maas.aliyuncs.com")
        or host == "dashscope.aliyuncs.com"
    ):
        return DASHSCOPE_IMAGE_PROTOCOL
    return OPENAI_IMAGE_PROTOCOL


def generate_image(
    *,
    api_key: str,
    api_base: str,
    model: str,
    prompt: str,
    output_path: str | Path,
    timeout: float = 180,
    provider_type: str | None = None,
) -> Path:
    """Generate and immediately download one landscape article image.

    Both OpenAI-compatible image APIs and Alibaba Model Studio's native Qwen
    Image API are supported. Remote image URLs are downloaded immediately because
    providers commonly return short-lived signed URLs.
    """
    resolved_provider = infer_image_provider(provider_type, api_base)
    api_base = resolved_image_endpoint(resolved_provider, api_base)
    _validate_settings(api_key=api_key, api_base=api_base, model=model)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    protocol = detect_image_protocol(api_base, resolved_provider)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        item = _request_with_rate_limit_retry(
            client=client,
            protocol=protocol,
            api_base=api_base,
            headers=headers,
            model=model,
            prompt=prompt,
        )
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        _save_image_item(client, item, target)
    _normalize_generated_image(target)
    return target


def _request_with_rate_limit_retry(
    *,
    client: httpx.Client,
    protocol: str,
    api_base: str,
    headers: dict[str, str],
    model: str,
    prompt: str,
) -> dict[str, Any]:
    for attempt in range(_MAX_RATE_LIMIT_ATTEMPTS):
        try:
            if protocol == DASHSCOPE_IMAGE_PROTOCOL:
                return _generate_dashscope(
                    client=client,
                    endpoint=_dashscope_endpoint(api_base),
                    headers=headers,
                    model=model,
                    prompt=prompt,
                )
            if protocol == DASHSCOPE_ASYNC_IMAGE_PROTOCOL:
                return _generate_dashscope_async(
                    client=client,
                    endpoint=api_base,
                    headers=headers,
                    model=model,
                    prompt=prompt,
                )
            if protocol == MINIMAX_IMAGE_PROTOCOL:
                return _generate_minimax(
                    client=client,
                    endpoint=api_base,
                    headers=headers,
                    model=model,
                    prompt=prompt,
                )
            if protocol == VOLCENGINE_IMAGE_PROTOCOL:
                return _generate_volcengine(
                    client=client,
                    endpoint=api_base,
                    headers=headers,
                    model=model,
                    prompt=prompt,
                )
            if protocol == ZHIPU_IMAGE_PROTOCOL:
                return _generate_zhipu(
                    client=client,
                    endpoint=api_base,
                    headers=headers,
                    model=model,
                    prompt=prompt,
                )
            return _generate_openai_compatible(
                client=client,
                endpoint=_openai_endpoint(api_base),
                headers=headers,
                model=model,
                prompt=prompt,
            )
        except ImageProviderError as exc:
            if exc.status_code != 429:
                raise
            if attempt >= _MAX_RATE_LIMIT_ATTEMPTS - 1:
                raise ImageProviderError(
                    f"{exc}（已自动等待并重试 {_MAX_RATE_LIMIT_ATTEMPTS} 次，"
                    "接口仍处于限流状态）",
                    status_code=exc.status_code,
                    retry_after=exc.retry_after,
                ) from exc
            delay = _retry_delay(exc, attempt)
            logger.warning(
                "Image provider rate limited request; retrying in %.1fs (%s/%s)",
                delay,
                attempt + 2,
                _MAX_RATE_LIMIT_ATTEMPTS,
            )
            time.sleep(delay)
    raise RuntimeError("生图接口重试状态异常")


def _retry_delay(exc: ImageProviderError, attempt: int) -> float:
    if exc.retry_after is not None and exc.retry_after >= 0:
        return min(120.0, exc.retry_after)
    base = _RATE_LIMIT_DELAYS[min(attempt, len(_RATE_LIMIT_DELAYS) - 1)]
    # Jitter prevents concurrent image requests from retrying at the exact same
    # instant and immediately triggering another burst limit together.
    return base + random.uniform(0.0, min(2.0, base * 0.2))


def _generate_openai_compatible(
    *,
    client: httpx.Client,
    endpoint: str,
    headers: dict[str, str],
    model: str,
    prompt: str,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": "1536x1024",
        "response_format": "b64_json",
    }
    response = client.post(endpoint, headers=headers, json=payload)
    if response.status_code in {400, 404, 422}:
        # Some compatible providers do not accept size/response_format.
        response = client.post(
            endpoint,
            headers=headers,
            json={"model": model, "prompt": prompt, "n": 1},
        )
    _raise_for_provider_error(response, "生图接口")
    data = response.json()
    rows = data.get("data") or []
    if not rows or not isinstance(rows[0], dict):
        raise RuntimeError(f"生图接口没有返回图片：{_safe_response_summary(data)}")
    return dict(rows[0])


def _generate_dashscope(
    *,
    client: httpx.Client,
    endpoint: str,
    headers: dict[str, str],
    model: str,
    prompt: str,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "input": {
            "messages": [
                {"role": "user", "content": [{"text": prompt}]},
            ]
        },
        "parameters": {
            "negative_prompt": (
                "水印、二维码、品牌标识、媒体Logo、新闻网站界面、文字、字母、数字、"
                "乱码、低清晰度、与主题无关的装饰图、通用商务会议、握手摆拍、"
                "无关城市天际线、抽象科技背景、海报、PPT、信息图、杂志页面、文档、"
                "白底排版、标题、说明文字、长段文字、分栏、边框、拼贴画"
            ),
            # The article pipeline already produces a tightly controlled visual
            # brief. Disabling provider-side rewriting prevents generic scenery
            # from being added and keeps the image close to the argument.
            "prompt_extend": False,
            "watermark": False,
            "size": "1920*1080",
            "n": 1,
        },
    }
    response = client.post(endpoint, headers=headers, json=payload)
    _raise_for_provider_error(response, "阿里云千问生图接口")
    data = response.json()
    content = (
        (((data.get("output") or {}).get("choices") or [{}])[0].get("message") or {})
        .get("content")
        or []
    )
    for item in content:
        if isinstance(item, dict) and (item.get("image") or item.get("url")):
            return {"url": item.get("image") or item.get("url")}

    # Compatibility with older DashScope image response shapes.
    results = (data.get("output") or {}).get("results") or []
    if results and isinstance(results[0], dict) and results[0].get("url"):
        return {"url": results[0]["url"]}
    raise RuntimeError(f"阿里云千问生图接口没有返回图片：{_safe_response_summary(data)}")


def _generate_dashscope_async(
    *,
    client: httpx.Client,
    endpoint: str,
    headers: dict[str, str],
    model: str,
    prompt: str,
) -> dict[str, Any]:
    async_headers = {**headers, "X-DashScope-Async": "enable"}
    payload = {
        "model": model,
        "input": {"prompt": prompt},
        "parameters": {
            "negative_prompt": (
                "水印、二维码、Logo、媒体标识、文字、字母、数字、海报、PPT、信息图、"
                "白底排版、分栏、拼贴、与主题无关的装饰画"
            ),
            "size": "1696*960",
            "n": 1,
            "prompt_extend": False,
            "watermark": False,
        },
    }
    response = client.post(endpoint, headers=async_headers, json=payload)
    _raise_for_provider_error(response, "阿里云百炼生图接口")
    data = response.json()
    output = data.get("output") or {}
    task_id = str(output.get("task_id") or "").strip()
    if not task_id:
        item = _extract_dashscope_item(data)
        if item:
            return item
        raise RuntimeError(f"阿里云百炼未返回任务 ID：{_safe_response_summary(data)}")

    poll_endpoint = _dashscope_task_endpoint(endpoint, task_id)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        time.sleep(3)
        poll = client.get(poll_endpoint, headers=headers)
        _raise_for_provider_error(poll, "查询阿里云百炼生图任务")
        task_data = poll.json()
        task_output = task_data.get("output") or {}
        status = str(task_output.get("task_status") or "").upper()
        if status == "SUCCEEDED":
            item = _extract_dashscope_item(task_data)
            if item:
                return item
            raise RuntimeError(
                f"阿里云百炼任务成功但没有图片：{_safe_response_summary(task_data)}"
            )
        if status in {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}:
            detail = task_output.get("message") or task_data.get("message") or status
            raise RuntimeError(f"阿里云百炼生图任务失败：{detail}")
    raise RuntimeError("阿里云百炼生图任务等待超过 180 秒，请稍后重试")


def _extract_dashscope_item(data: dict[str, Any]) -> dict[str, Any] | None:
    output = data.get("output") or {}
    for row in output.get("results") or []:
        if isinstance(row, dict) and row.get("url"):
            return {"url": row["url"]}
    choices = output.get("choices") or []
    for choice in choices:
        content = ((choice.get("message") or {}).get("content") or []) if isinstance(choice, dict) else []
        for item in content:
            if isinstance(item, dict) and (item.get("image") or item.get("url")):
                return {"url": item.get("image") or item.get("url")}
    return None


def _generate_minimax(
    *,
    client: httpx.Client,
    endpoint: str,
    headers: dict[str, str],
    model: str,
    prompt: str,
) -> dict[str, Any]:
    response = client.post(
        endpoint,
        headers=headers,
        json={
            "model": model,
            "prompt": prompt[:1500],
            "aspect_ratio": "16:9",
            "response_format": "url",
            "n": 1,
            "prompt_optimizer": False,
        },
    )
    _raise_for_provider_error(response, "MiniMax 生图接口")
    data = response.json()
    base_resp = data.get("base_resp") or {}
    if int(base_resp.get("status_code") or 0) != 0:
        raise RuntimeError(
            f"MiniMax 生图失败：{base_resp.get('status_msg') or _safe_response_summary(data)}"
        )
    image_urls = (data.get("data") or {}).get("image_urls") or []
    if image_urls:
        return {"url": image_urls[0]}
    image_base64 = (data.get("data") or {}).get("image_base64") or []
    if image_base64:
        return {"b64_json": image_base64[0]}
    raise RuntimeError(f"MiniMax 生图接口没有返回图片：{_safe_response_summary(data)}")


def _generate_volcengine(
    *,
    client: httpx.Client,
    endpoint: str,
    headers: dict[str, str],
    model: str,
    prompt: str,
) -> dict[str, Any]:
    response = client.post(
        endpoint,
        headers=headers,
        json={
            "model": model,
            "prompt": prompt,
            "size": "1920x1080",
            "sequential_image_generation": "disabled",
            "response_format": "url",
            "watermark": False,
        },
    )
    _raise_for_provider_error(response, "火山方舟 Seedream 生图接口")
    return _first_standard_image(response.json(), "火山方舟 Seedream")


def _generate_zhipu(
    *,
    client: httpx.Client,
    endpoint: str,
    headers: dict[str, str],
    model: str,
    prompt: str,
) -> dict[str, Any]:
    response = client.post(
        endpoint,
        headers=headers,
        json={"model": model, "prompt": prompt, "size": "1440x720"},
    )
    _raise_for_provider_error(response, "智谱图片生成接口")
    return _first_standard_image(response.json(), "智谱图片生成")


def _first_standard_image(data: dict[str, Any], label: str) -> dict[str, Any]:
    rows = data.get("data") or []
    if rows and isinstance(rows[0], dict):
        item = dict(rows[0])
        if item.get("url") or item.get("b64_json"):
            return item
    raise RuntimeError(f"{label}接口没有返回图片：{_safe_response_summary(data)}")


def _save_image_item(client: httpx.Client, item: dict[str, Any], target: Path) -> None:
    if item.get("b64_json"):
        try:
            target.write_bytes(base64.b64decode(str(item["b64_json"]), validate=True))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("生图接口返回的 base64 图片无效") from exc
        return
    image_url = str(item.get("url") or "").strip()
    if not image_url:
        raise RuntimeError("生图接口返回结果缺少 b64_json 或图片 URL")
    image_response = client.get(image_url)
    _raise_for_provider_error(image_response, "下载生成图片")
    if not image_response.content:
        raise RuntimeError("生成图片下载结果为空")
    target.write_bytes(image_response.content)


def _openai_endpoint(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.casefold().endswith("/images/generations"):
        return base
    return base + "/images/generations"


def _dashscope_endpoint(api_base: str) -> str:
    base = api_base.rstrip("/")
    if "multimodal-generation/generation" in urlparse(base).path.casefold():
        return base
    return base + _DASHSCOPE_PATH


def _dashscope_task_endpoint(endpoint: str, task_id: str) -> str:
    parsed = urlparse(endpoint)
    return f"{parsed.scheme}://{parsed.netloc}/api/v1/tasks/{task_id}"


def _validate_settings(*, api_key: str, api_base: str, model: str) -> None:
    if not api_key.strip() or not api_base.strip() or not model.strip():
        raise ValueError("API Key、API Base URL 和图片模型名称不能为空")
    parsed = urlparse(api_base.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("API Base URL 格式不正确")


def _raise_for_provider_error(response: httpx.Response, label: str) -> None:
    if response.status_code < 400:
        return
    message = ""
    request_id = ""
    try:
        payload = response.json()
        message = str(
            payload.get("message")
            or payload.get("error", {}).get("message")
            or payload.get("code")
            or ""
        )
        request_id = str(payload.get("request_id") or payload.get("requestId") or "")
    except (ValueError, AttributeError):
        message = response.text[:300].strip()
    suffix = f"（request_id: {request_id}）" if request_id else ""
    detail = message or response.reason_phrase or "请求失败"
    retry_after: float | None = None
    raw_retry_after = str(response.headers.get("Retry-After") or "").strip()
    if raw_retry_after:
        try:
            retry_after = max(0.0, float(raw_retry_after))
        except ValueError:
            retry_after = None
    raise ImageProviderError(
        f"{label}返回 HTTP {response.status_code}：{detail}{suffix}",
        status_code=int(response.status_code),
        retry_after=retry_after,
    )


def _safe_response_summary(data: Any) -> str:
    text = str(data)
    return text if len(text) <= 800 else text[:800] + "…"


def _normalize_generated_image(target: Path) -> None:
    """Convert model output to a compact WeChat-friendly landscape JPEG."""
    try:
        with Image.open(target) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            encoded = b""
            for quality in (88, 82, 76, 70, 64):
                buffer = BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True)
                encoded = buffer.getvalue()
                if len(encoded) <= 900 * 1024:
                    break
    except Exception as exc:  # Pillow raises several format-specific exceptions.
        raise RuntimeError("生图接口返回的内容不是有效图片") from exc
    target.write_bytes(encoded)


def test_image_endpoint(
    *,
    api_key: str,
    api_base: str,
    model: str,
    provider_type: str | None = None,
) -> str:
    """Validate an image configuration without triggering a billable generation."""
    provider = infer_image_provider(provider_type, api_base)
    api_base = resolved_image_endpoint(provider, api_base)
    _validate_settings(api_key=api_key, api_base=api_base, model=model)
    protocol = detect_image_protocol(api_base, provider)
    labels = {
        DASHSCOPE_IMAGE_PROTOCOL: "阿里云千问生图（百炼同步）",
        DASHSCOPE_ASYNC_IMAGE_PROTOCOL: "阿里云百炼万相异步生图",
        MINIMAX_IMAGE_PROTOCOL: "MiniMax 文生图",
        VOLCENGINE_IMAGE_PROTOCOL: "火山方舟 Seedream",
        ZHIPU_IMAGE_PROTOCOL: "智谱图片生成",
    }
    if protocol in labels:
        return f"已识别{labels[protocol]}配置；请点击“生成测试图”实际验证 API Key"

    endpoint = _openai_endpoint(api_base)
    models_endpoint = endpoint[: -len("images/generations")] + "models"
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        response = client.get(
            models_endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        # Image-only gateways frequently omit /models. Authentication failures
        # are definitive; 404/405 only mean discovery is not implemented.
        if response.status_code in {401, 403}:
            _raise_for_provider_error(response, "模型连接测试")
        if response.status_code not in {200, 404, 405}:
            _raise_for_provider_error(response, "模型连接测试")
    return "连接成功"
