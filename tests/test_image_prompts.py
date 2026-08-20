from __future__ import annotations

import json

from app.services.image_prompts import enrich_inline_image_prompts


class _FakePromptClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self.responses)


def _plan() -> dict[str, object]:
    return {
        "index": 1,
        "anchor": "华为通过持续研发投入提升关键技术能力。",
        "offset": 120,
        "keywords": ["华为", "研发"],
        "caption": "持续研发投入形成技术能力",
        "prompt": "写实科技企业研发场景。",
        "context": "本段分析华为如何通过长期研发投入形成技术积累和产品竞争力。",
    }


def test_prompt_agent_summarizes_article_then_writes_subject_aligned_prompts() -> None:
    client = _FakePromptClient(
        [
            json.dumps(
                {
                    "article_summary": "文章分析华为持续研发形成长期竞争力。",
                    "primary_subject": "华为",
                    "subject_visual_direction": "围绕华为研发、产品和企业业务场景保持统一科技纪实风格。",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "images": [
                        {
                            "index": 1,
                            "argument_summary": "长期研发投入沉淀关键技术能力。",
                            "prompt": "华为研发团队在现代实验室验证通信设备，真实科技纪实摄影。",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ]
    )

    plans, warnings = enrich_inline_image_prompts(
        article_title="华为为什么坚持长期研发",
        body="华为围绕通信与终端持续研发。\n\n长期投入形成技术积累。",
        plans=[_plan()],
        client=client,
    )

    assert warnings == []
    assert len(client.prompts) == 2
    assert "先总结文章大意" in client.prompts[0]
    assert "华为" in client.prompts[1]
    assert "目标论点" in client.prompts[1]
    assert plans[0]["primary_subject"] == "华为"
    assert plans[0]["argument_summary"] == "长期研发投入沉淀关键技术能力。"
    assert "核心主体必须围绕华为" in str(plans[0]["prompt"])
    assert "华为研发团队" in str(plans[0]["prompt"])
    assert "不得出现任何可读的大段文字" in str(plans[0]["prompt"])
    assert "海报、PPT、信息图" in str(plans[0]["prompt"])


def test_prompt_agent_failure_keeps_existing_generation_prompt() -> None:
    original = _plan()
    client = _FakePromptClient(["不是 JSON"])

    plans, warnings = enrich_inline_image_prompts(
        article_title="测试文章",
        body="测试正文",
        plans=[original],
        client=client,
    )

    assert plans == [original]
    assert len(warnings) == 1
    assert "已使用内置提示词" in warnings[0]
