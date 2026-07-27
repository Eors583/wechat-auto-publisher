from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


IMAGE_ALIBABA = "image_alibaba"
IMAGE_MINIMAX = "image_minimax"
IMAGE_VOLCENGINE = "image_volcengine"
IMAGE_ZHIPU = "image_zhipu"
IMAGE_CUSTOM = "openai_image"


@dataclass(frozen=True)
class ImageProviderPreset:
    provider_type: str
    label: str
    endpoint: str
    models: tuple[str, ...]
    default_model: str
    description: str
    key_placeholder: str = "sk-..."


IMAGE_PROVIDER_PRESETS: dict[str, ImageProviderPreset] = {
    IMAGE_ALIBABA: ImageProviderPreset(
        provider_type=IMAGE_ALIBABA,
        label="阿里云百炼（通义万相）",
        endpoint=(
            "https://dashscope.aliyuncs.com/api/v1/services/"
            "aigc/text2image/image-synthesis"
        ),
        models=("wan2.6-t2i", "wan2.5-t2i-preview", "wan2.2-t2i-flash"),
        default_model="wan2.6-t2i",
        description="已内置异步任务创建、状态轮询和结果下载，只需百炼 API Key。",
        key_placeholder="sk-...（百炼 API Key）",
    ),
    IMAGE_MINIMAX: ImageProviderPreset(
        provider_type=IMAGE_MINIMAX,
        label="MiniMax",
        endpoint="https://api.minimaxi.com/v1/image_generation",
        models=("image-01", "image-01-live"),
        default_model="image-01",
        description="已内置 MiniMax 文生图协议、16:9 参数和 image_urls 结果解析。",
        key_placeholder="MiniMax API Key",
    ),
    IMAGE_VOLCENGINE: ImageProviderPreset(
        provider_type=IMAGE_VOLCENGINE,
        label="火山方舟（豆包 Seedream）",
        endpoint="https://ark.cn-beijing.volces.com/api/v3/images/generations",
        models=(
            "doubao-seedream-4-5-251128",
            "doubao-seedream-4-0-250828",
        ),
        default_model="doubao-seedream-4-5-251128",
        description="已内置火山方舟图片生成协议、单图模式和无水印参数。",
        key_placeholder="火山方舟 API Key",
    ),
    IMAGE_ZHIPU: ImageProviderPreset(
        provider_type=IMAGE_ZHIPU,
        label="智谱 AI（GLM-Image / CogView）",
        endpoint="https://open.bigmodel.cn/api/paas/v4/images/generations",
        models=("glm-image", "cogview-4-250304", "cogview-4", "cogview-3-flash"),
        default_model="glm-image",
        description="已内置智谱图像生成协议和横版尺寸参数。",
        key_placeholder="智谱 API Key",
    ),
    IMAGE_CUSTOM: ImageProviderPreset(
        provider_type=IMAGE_CUSTOM,
        label="自定义接口（高级）",
        endpoint="",
        models=(),
        default_model="",
        description=(
            "仅用于未列出的服务商或自建网关，需要自行填写 API 地址和模型名；"
            "默认按 OpenAI Images API 解析。"
        ),
    ),
}

IMAGE_PROVIDER_TYPES = frozenset(IMAGE_PROVIDER_PRESETS)


def is_image_provider(provider_type: str | None) -> bool:
    return str(provider_type or "") in IMAGE_PROVIDER_TYPES


def image_provider_options() -> dict[str, str]:
    return {
        provider_type: preset.label
        for provider_type, preset in IMAGE_PROVIDER_PRESETS.items()
    }


def get_image_provider_preset(provider_type: str) -> ImageProviderPreset:
    try:
        return IMAGE_PROVIDER_PRESETS[provider_type]
    except KeyError as exc:
        raise ValueError("不支持的生图厂商") from exc


def infer_image_provider(provider_type: str | None, api_base: str | None) -> str:
    """Identify legacy generic records without changing their stored credentials."""
    provider_type = str(provider_type or "")
    if provider_type in IMAGE_PROVIDER_TYPES and provider_type != IMAGE_CUSTOM:
        return provider_type
    parsed = urlparse(str(api_base or "").strip())
    host = (parsed.hostname or "").casefold()
    if host == "api.minimaxi.com" or host.endswith(".minimaxi.com"):
        return IMAGE_MINIMAX
    if host == "dashscope.aliyuncs.com" or host.endswith(".maas.aliyuncs.com"):
        return IMAGE_ALIBABA
    if host.endswith(".volces.com") or host.endswith(".volcengineapi.com"):
        return IMAGE_VOLCENGINE
    if host == "open.bigmodel.cn" or host.endswith(".bigmodel.cn"):
        return IMAGE_ZHIPU
    return IMAGE_CUSTOM


def resolved_image_endpoint(provider_type: str, api_base: str | None = None) -> str:
    preset = get_image_provider_preset(provider_type)
    configured = str(api_base or "").strip()
    if provider_type == IMAGE_CUSTOM:
        return configured
    # Preserve workspace-specific or previously configured official endpoints.
    return configured or preset.endpoint


def image_provider_label(provider_type: str | None, api_base: str | None = None) -> str:
    inferred = infer_image_provider(provider_type, api_base)
    return IMAGE_PROVIDER_PRESETS[inferred].label
