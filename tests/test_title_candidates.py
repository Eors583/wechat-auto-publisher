from __future__ import annotations

import inspect
import json
from typing import Any

from app.ai import (
    RewriteResult,
    TitleResult,
    parse_rewrite_output,
    parse_title_output,
)
from app.ui.panels import tasks
from app.ui.state import clean_subtitles, clean_titles
from app.workflows.generation import GenerationSteps


def _titles(prefix: str, count: int) -> list[str]:
    return [f"{prefix}{index}：这是一个完整可用的候选文案" for index in range(1, count + 1)]


def test_incomplete_json_does_not_expose_json_syntax_as_title_candidates() -> None:
    """A truncated model response must not render its JSON field line as a title."""

    output = """
    {
      "titles": [
        "少即是赢：梁文锋揭示成功的非物质之道",
        "财富新观：梁文锋谈为何拿得少的人能笑到最后",
        "真正的底气，从来不只来自账户余额",
      "subtitles": [
        "离开大厂之后，重新理解安全感与个人价值",
        "从现金焦虑看见职业选择背后的心理账户",
    """

    result = parse_rewrite_output(output)

    assert result.titles == [
        "少即是赢：梁文锋揭示成功的非物质之道",
        "财富新观：梁文锋谈为何拿得少的人能笑到最后",
        "真正的底气，从来不只来自账户余额",
    ]
    assert result.subtitles == [
        "离开大厂之后，重新理解安全感与个人价值",
        "从现金焦虑看见职业选择背后的心理账户",
    ]
    assert '"titles": [' not in result.titles
    assert all(not candidate.startswith(('"', "'")) for candidate in result.titles)
    assert all(not candidate.endswith(('"', "'", ",", "，")) for candidate in result.titles)


def test_non_strict_title_json_removes_quotes_commas_and_wrapper_lines() -> None:
    output = """
    "titles": [
      "第一条候选标题：用选择重建职业安全感",
      '第二条候选标题：现金无法消除身份焦虑',
      “第三条候选标题：离开工牌之后如何重新出发”，
    """

    result = parse_title_output(output)

    assert result.titles == [
        "第一条候选标题：用选择重建职业安全感",
        "第二条候选标题：现金无法消除身份焦虑",
        "第三条候选标题：离开工牌之后如何重新出发",
    ]
    assert '"titles": [' not in result.titles
    assert all(not candidate.endswith((",", "，")) for candidate in result.titles)


def test_rewrite_and_title_results_preserve_ten_candidates() -> None:
    titles = _titles("主标题", 12)
    subtitles = _titles("副标题", 12)
    rewrite = parse_rewrite_output(
        json.dumps(
            {"body": "正文", "titles": titles, "subtitles": subtitles},
            ensure_ascii=False,
        )
    )
    optimized = parse_title_output(
        json.dumps({"titles": titles}, ensure_ascii=False)
    )

    assert isinstance(rewrite, RewriteResult)
    assert isinstance(optimized, TitleResult)
    assert rewrite.titles == titles[:10]
    assert rewrite.subtitles == subtitles[:10]
    assert optimized.titles == titles[:10]


def test_structured_rewrite_strips_candidate_lists_accidentally_put_in_body() -> None:
    titles = _titles("主标题", 10)
    subtitles = _titles("副标题", 10)
    contaminated_body = """开场正文。

## 经营韧性来自组织能力

这里是完整文章的结尾。

10个标题
1. 不应出现在正文里的候选标题
2. 第二个候选标题

10个副标题
1. 不应出现在正文里的候选副标题
"""

    result = parse_rewrite_output(
        json.dumps(
            {
                "body": contaminated_body,
                "titles": titles,
                "subtitles": subtitles,
            },
            ensure_ascii=False,
        )
    )

    assert result.body.endswith("这里是完整文章的结尾。")
    assert "10个标题" not in result.body
    assert "候选副标题" not in result.body
    assert result.titles == titles
    assert result.subtitles == subtitles


def test_body_keeps_normal_prose_about_titles() -> None:
    result = parse_rewrite_output(
        json.dumps(
            {
                "body": "文章标题决定第一印象。\n\n## 标题决定点击率\n\n正文继续。",
                "titles": _titles("主标题", 10),
                "subtitles": _titles("副标题", 10),
            },
            ensure_ascii=False,
        )
    )

    assert "文章标题决定第一印象" in result.body
    assert "## 标题决定点击率" in result.body


def test_candidate_sections_can_be_prefixed_or_placed_before_body() -> None:
    result = parse_rewrite_output(
        json.dumps(
            {
                "body": """## 二、10个标题
1. 候选标题

## 三、10个副标题
1. 候选副标题

# 真正的文章标题

正文内容。""",
                "titles": _titles("主标题", 10),
                "subtitles": _titles("副标题", 10),
            },
            ensure_ascii=False,
        )
    )

    assert result.body == "# 真正的文章标题\n\n正文内容。"


def test_plain_text_wrapper_keeps_only_the_article_body() -> None:
    output = """10个可选标题
1. 机器人会翻跟头，离走进家庭还有多远？
2. 人形机器人真正的战场，不是表演而是稳定干活
3. 商业化的关键不是动作，而是泛化与交付

Digest
机器人商业化需要跨越泛化、成本、交付与服务体系等门槛。

正文
## 真正的价值是持续解决问题

机器人进入日常经营，靠的不是一次性炫技，而是长期稳定完成任务。"""

    result = parse_rewrite_output(output)

    assert result.body == (
        "## 真正的价值是持续解决问题\n\n"
        "机器人进入日常经营，靠的不是一次性炫技，而是长期稳定完成任务。"
    )
    assert result.titles == [
        "机器人会翻跟头，离走进家庭还有多远？",
        "人形机器人真正的战场，不是表演而是稳定干活",
        "商业化的关键不是动作，而是泛化与交付",
    ]
    assert result.subtitles == []
    assert "Digest" not in result.body
    assert "10个可选标题" not in result.body


def test_review_workbench_uses_clean_candidates_and_offers_subtitle_radio() -> None:
    job = {
        "title_candidates": [
            '"titles": [',
            '"第一条干净的主标题",',
            "'第二条干净的主标题'，",
        ],
        "titles": ['"第一条干净的主标题",'],
        "subtitles": [
            '"第一条副标题",',
            "'第二条副标题'，",
            '"subtitles": [',
            "第三条副标题",
        ],
    }

    assert clean_titles(job) == [
        "第一条干净的主标题",
        "第二条干净的主标题",
    ]
    assert clean_subtitles(job) == [
        "第一条副标题",
        "第二条副标题",
        "第三条副标题",
    ]

    source = inspect.getsource(tasks.open_review_workbench)
    assert "title_options = clean_titles(job)" in source
    assert "subtitle_options = clean_subtitles(job)" in source
    assert "subtitle_choice = ui.radio(" in source


class _CaptureDb:
    def __init__(self) -> None:
        self.last_changes: dict[str, Any] = {}

    def update_job(self, _job_id: int, **changes: Any) -> None:
        self.last_changes = changes


class _RewriteProvider:
    def rewrite(self, _topic: str, _raw_content: str) -> RewriteResult:
        return RewriteResult(
            body="生成后的正文",
            titles=_titles("主标题", 13),
            subtitles=_titles("副标题", 13),
            digest="阅读全文后形成的经营决策摘要",
            provider="fake",
        )

    def prompt_trace(self, _provider: str) -> dict[str, Any]:
        return {}

    def optimize_titles(
        self,
        _body: str,
        *,
        fallback_titles: list[str] | None = None,
    ) -> TitleResult:
        return TitleResult(titles=_titles("优化标题", 13), provider="fake")


class _StableScorer:
    def score(self, titles: list[str], _body: str) -> list[tuple[str, float]]:
        return [(title, float(len(titles) - index)) for index, title in enumerate(titles)]


class _GenerationContext:
    def __init__(self) -> None:
        self.db = _CaptureDb()
        self.rewriter = _RewriteProvider()
        self.scorer = _StableScorer()
        self.config: dict[str, Any] = {}

    def require_job(self, job_id: int) -> dict[str, Any]:
        return {"id": job_id, **self.db.last_changes}


def test_generation_rewrite_persists_at_most_ten_titles_and_subtitles() -> None:
    context = _GenerationContext()

    GenerationSteps(context).rewrite(
        {
            "id": 113,
            "topic": "职业安全感",
            "raw_content": "原文",
            "meta": {},
        }
    )

    assert context.db.last_changes["titles_json"] == _titles("主标题", 13)[:10]
    assert context.db.last_changes["subtitles_json"] == _titles("副标题", 13)[:10]
    assert context.db.last_changes["digest"] == "阅读全文后形成的经营决策摘要"


def test_generation_rewrite_accepts_partial_nonempty_candidates() -> None:
    context = _GenerationContext()
    context.rewriter.rewrite = lambda *_args: RewriteResult(
        body="有效正文",
        titles=["唯一有效主标题"],
        subtitles=["唯一有效副标题"],
        digest="有效摘要",
        provider="custom",
    )

    GenerationSteps(context).rewrite(
        {"id": 115, "topic": "话题", "raw_content": "原文", "meta": {}}
    )

    assert context.db.last_changes["titles_json"] == ["唯一有效主标题"]
    assert context.db.last_changes["subtitles_json"] == ["唯一有效副标题"]


def test_generation_title_optimization_persists_at_most_ten_candidates() -> None:
    context = _GenerationContext()

    GenerationSteps(context).optimize_titles(
        {
            "id": 114,
            "body": "生成后的正文",
            "titles": _titles("原始标题", 13),
        }
    )

    assert context.db.last_changes["title_candidates_json"] == _titles(
        "优化标题", 13
    )[:10]
