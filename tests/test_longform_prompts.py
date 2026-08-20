from __future__ import annotations

import hashlib
import json

import pytest

from app.ai import ARTICLE_DIGEST_PROMPT, RewriteResult
from app.ai.failover import FailoverRewriter
from app.ai.openai_compat import OpenAICompatClient


_TEN_TITLES = [f"标题甲{index}" for index in range(1, 11)]
_TEN_SUBTITLES = [f"副标题甲{index}" for index in range(1, 11)]
_TITLE_JSON = json.dumps(
    {"titles": _TEN_TITLES, "subtitles": _TEN_SUBTITLES},
    ensure_ascii=False,
)


class RecordingLongformClient(OpenAICompatClient):
    def __init__(self, provider_name: str, *, short_first_body: bool = False) -> None:
        super().__init__(
            api_key="test-key",
            api_base="https://example.test/v1",
            model="test-model",
            provider_name=provider_name,
        )
        self.prompts: list[tuple[str, str]] = []
        self.short_first_body = short_first_body

    def complete(self, prompt: str, *, system: str | None = None, **_kwargs) -> str:
        self.prompts.append((prompt, str(system or "")))
        if "本阶段只生成文章正文" in prompt:
            return "短正文" if self.short_first_body else "全新正文内容" * 400
        if "本阶段只输出扩写后的完整正文" in prompt:
            return "扩写后的全新正文内容" * 400
        if "本阶段只生成主标题和副标题" in prompt:
            return _TITLE_JSON
        if "本阶段只补充副标题" in prompt:
            return _TITLE_JSON
        raise AssertionError("unexpected generation phase")


@pytest.mark.parametrize("provider_name", ["moonshot", "deepseek", "qwen", "自定义兼容模型"])
def test_all_openai_compatible_longform_providers_receive_configured_prompts(
    provider_name: str,
) -> None:
    client = RecordingLongformClient(provider_name)
    result = client.rewrite_longform(
        "测试话题",
        "参考原文",
        instruction="正文唯一规则MARKER：采用反常识开场。只输出严格 JSON。",
        title_instruction="标题唯一规则TITLE_MARKER：标题必须体现决策价值。",
        min_chars=100,
        target_chars=500,
    )

    body_prompt, body_system = client.prompts[0]
    title_prompt, _title_system = client.prompts[1]
    assert "正文唯一规则MARKER" in body_prompt
    assert "只输出严格 JSON" in body_prompt
    assert "本阶段暂不执行" in body_prompt
    assert "只输出正文纯文本" in body_system
    assert "正文唯一规则MARKER" in title_prompt
    assert "标题唯一规则TITLE_MARKER" in title_prompt
    assert "本阶段只生成主标题和副标题" in title_prompt
    assert ARTICLE_DIGEST_PROMPT in title_prompt
    assert ARTICLE_DIGEST_PROMPT in _title_system
    assert result.titles == _TEN_TITLES
    assert result.subtitles == _TEN_SUBTITLES


def test_expansion_phase_keeps_original_writing_instruction() -> None:
    client = RecordingLongformClient("moonshot", short_first_body=True)
    result = client.rewrite_longform(
        "扩写话题",
        "参考材料",
        instruction="EXPAND_MARKER：每个观点必须加入反例与行动建议。只输出 JSON。",
        title_instruction="标题要求",
        min_chars=2000,
        target_chars=2500,
    )

    expansion_prompts = [
        prompt for prompt, _system in client.prompts
        if "本阶段只输出扩写后的完整正文" in prompt
    ]
    assert expansion_prompts
    assert all("EXPAND_MARKER" in prompt for prompt in expansion_prompts)
    assert all("JSON、标题和副标题" in prompt for prompt in expansion_prompts)
    assert len(result.body) >= 2000


def test_failover_passes_rewrite_and_title_instructions_to_longform_client() -> None:
    rewrite_marker = "FAILOVER_REWRITE_MARKER"
    title_marker = "FAILOVER_TITLE_MARKER"
    config = {
        "ai": {
            "primary": "moonshot",
            "fallback": "moonshot",
            "max_retries_per_model": 1,
            "min_body_chars": 2000,
            "max_similarity": 0.99,
            "rewrite_prompt": rewrite_marker,
            "title_prompt": title_marker,
        }
    }
    rewriter = FailoverRewriter(config)

    class FakeLongform:
        def __init__(self) -> None:
            self.kwargs = {}

        def rewrite_longform(self, _topic, _raw, **kwargs):
            self.kwargs = kwargs
            return RewriteResult(
                body="完全不同的新正文内容与经营洞察" * 250,
                titles=[f"有效标题示例{index}" for index in range(1, 11)],
                subtitles=[f"有效副标题示例{index}" for index in range(1, 11)],
            )

    fake = FakeLongform()
    rewriter._clients = {"moonshot": fake}
    result = rewriter.rewrite("话题", "原始材料")

    assert fake.kwargs["instruction"] == rewrite_marker
    assert fake.kwargs["title_instruction"] == title_marker
    assert result.provider == "moonshot"


def test_manus_single_pass_prompt_behavior_remains_unchanged() -> None:
    marker = "MANUS_SINGLE_PASS_MARKER"
    config = {
        "ai": {
            "primary": "manus",
            "fallback": "manus",
            "max_retries_per_model": 1,
            "min_body_chars": 2000,
            "max_similarity": 0.99,
            "rewrite_prompt": marker,
            "title_prompt": "标题要求",
        }
    }
    rewriter = FailoverRewriter(config)

    class FakeManus:
        def __init__(self) -> None:
            self.prompt = ""

        def rewrite(self, prompt: str) -> RewriteResult:
            self.prompt = prompt
            return RewriteResult(
                body="Manus全新正文与组织经营洞察" * 250,
                titles=[f"Manus有效标题{index}" for index in range(1, 11)],
                subtitles=[f"Manus有效副标题{index}" for index in range(1, 11)],
            )

    fake = FakeManus()
    rewriter._clients = {"manus": fake}
    rewriter.rewrite("话题", "参考材料")

    assert marker in fake.prompt
    assert "【原始内容（仅作参考，禁止照搬）】" in fake.prompt
    assert rewriter.prompt_trace("manus")["generation_mode"] == "single_pass_structured"


def test_prompt_trace_records_hashes_without_prompt_content() -> None:
    rewrite_prompt = "内部写作规则"
    title_prompt = "内部标题规则"
    rewriter = FailoverRewriter(
        {
            "ai": {
                "rewrite_prompt": rewrite_prompt,
                "title_prompt": title_prompt,
            }
        }
    )
    trace = rewriter.prompt_trace("moonshot")

    assert trace["protocol_version"] == "rewrite-stages-v2"
    assert trace["generation_mode"] == "longform_staged"
    assert trace["rewrite_prompt_sha256"] == hashlib.sha256(
        rewrite_prompt.encode("utf-8")
    ).hexdigest()
    assert trace["title_prompt_sha256"] == hashlib.sha256(
        title_prompt.encode("utf-8")
    ).hexdigest()
    assert rewrite_prompt not in str(trace)
