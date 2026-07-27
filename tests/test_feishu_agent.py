from __future__ import annotations

from types import SimpleNamespace

from app.feishu.agent import AgentPlan, _parse_json_object
import app.feishu.tool_executor as executor_module
from app.feishu.session import FeishuSessionStore
from app.feishu.tool_executor import FeishuToolExecutor, explicit_draft_confirmation


def test_agent_plan_parses_fenced_json_and_summarizes_before_tool() -> None:
    value = _parse_json_object(
        '```json\n{"intent":"查询进度","analysis_summary":"用户询问当前状态",'
        '"steps":["读取当前批次","返回各账号状态"],'
        '"tool":"get_batch_status","arguments":{},"reply":""}\n```'
    )
    plan = AgentPlan(
        intent=value["intent"],
        analysis_summary=value["analysis_summary"],
        steps=value["steps"],
        tool=value["tool"],
        arguments=value["arguments"],
    )
    assert plan.tool == "get_batch_status"
    assert "已识别意图：查询进度" in plan.plan_text
    assert "执行流程：读取当前批次；返回各账号状态" in plan.plan_text


def test_draft_write_requires_explicit_confirmation() -> None:
    assert not explicit_draft_confirmation("可以了，没问题")
    assert not explicit_draft_confirmation("确认一下")
    assert explicit_draft_confirmation("确认全部写入草稿箱")


def test_agent_cannot_write_drafts_from_ambiguous_approval() -> None:
    replies: list[str] = []
    executor = FeishuToolExecutor(
        service=SimpleNamespace(),
        config={},
        sessions=SimpleNamespace(),
        default_account_ids=[],
        reply_text=lambda _message_id, text: replies.append(text),
        send_text=lambda _chat_id, _text: None,
    )
    plan = AgentPlan(
        intent="确认写入",
        analysis_summary="用户表达认可但未明确写入草稿箱",
        tool="write_all_to_drafts",
    )

    executor.execute(
        plan,
        original_text="可以了，没问题",
        message_id="m1",
        chat_id="c1",
        open_id="u1",
        current_batch_id="batch1",
    )

    assert replies == ["写入草稿箱会操作所有已选公众号。请明确回复“确认全部写入草稿箱”。"]


def test_hot_topic_tool_returns_source_date_and_link(monkeypatch) -> None:
    monkeypatch.setattr(
        executor_module,
        "fetch_hot_topics",
        lambda _config: [
            {
                "title": "企业管理热点",
                "source": "行业资讯",
                "published_at": "2026-07-20T01:00:00+00:00",
                "url": "https://example.com/hot",
            }
        ],
    )
    contexts: dict[str, dict] = {}
    fake_db = SimpleNamespace(
        get_bot_context=lambda scope_id: dict(contexts.get(scope_id) or {}),
        set_bot_context=lambda scope_id, value: contexts.__setitem__(scope_id, value),
    )
    replies: list[str] = []
    executor = FeishuToolExecutor(
        service=SimpleNamespace(db=fake_db),
        config={"topics": {"recent_days": 7}},
        sessions=FeishuSessionStore(fake_db),
        default_account_ids=[],
        reply_text=lambda _message_id, text: replies.append(text),
        send_text=lambda _chat_id, _text: None,
    )

    executor.execute(
        AgentPlan(
            intent="查询近7日热点",
            analysis_summary="用户要求热点列表",
            tool="get_recent_hot_topics",
            arguments={"limit": 5},
        ),
        original_text="查询7日热点",
        message_id="m1",
        chat_id="c1",
        open_id="u1",
        current_batch_id=None,
    )

    assert "企业管理热点" in replies[0]
    assert "行业资讯 · 2026-07-20" in replies[0]
    assert "https://example.com/hot" in replies[0]
    assert contexts["c1"]["recent_hot_topics"][0]["number"] == 1


def test_create_rewrite_can_use_recent_hot_topic_number() -> None:
    contexts = {
        "c1": {
            "recent_hot_topics": [
                {
                    "number": 2,
                    "title": "企业AI管理",
                    "url": "https://example.com/article",
                }
            ]
        }
    }
    created: list[dict] = []

    class FakeDb:
        def get_bot_context(self, scope_id):
            return dict(contexts.get(scope_id) or {})

        def set_bot_context(self, scope_id, value):
            contexts[scope_id] = value

        def set_bot_session(self, scope_id, batch_id):
            contexts.setdefault(scope_id, {})["batch_id"] = batch_id

    class FakeService:
        db = FakeDb()

        @staticmethod
        def list_accounts():
            return [{"id": "a1", "name": "蓝血家族办公室", "model_name": "Kimi"}]

        @staticmethod
        def create_batch(**kwargs):
            created.append(kwargs)
            return {
                "id": "batch1",
                "jobs": [{"account_name": "蓝血家族办公室"}],
            }

    replies: list[str] = []
    service = FakeService()
    executor = FeishuToolExecutor(
        service=service,
        config={},
        sessions=FeishuSessionStore(service.db),
        default_account_ids=["a1"],
        reply_text=lambda _message_id, text: replies.append(text),
        send_text=lambda _chat_id, _text: None,
    )

    executor.execute(
        AgentPlan(
            intent="改写第二条热点",
            analysis_summary="用户引用最近热点列表",
            tool="create_rewrite_batch",
            arguments={"hot_topic_number": 2},
        ),
        original_text="用第2条改写",
        message_id="m1",
        chat_id="c1",
        open_id="u1",
        current_batch_id=None,
    )

    assert created[0]["source_url"] == "https://example.com/article"
    assert created[0]["account_ids"] == ["a1"]
    assert contexts["c1"]["current_batch_id"] == "batch1"


def test_title_selection_requires_confirmation_before_advancing() -> None:
    contexts: dict[str, dict] = {}

    class FakeDb:
        def get_bot_context(self, scope_id):
            return dict(contexts.get(scope_id) or {})

        def set_bot_context(self, scope_id, value):
            contexts[scope_id] = value

    jobs = [
        {
            "id": 11,
            "account_name": "公众号A",
            "status": "ready_for_review",
            "body": "A正文",
            "titles": ["A标题1", "A标题2"],
            "subtitles": ["A副标题1"],
        },
        {
            "id": 12,
            "account_name": "公众号B",
            "status": "ready_for_review",
            "body": "B正文",
            "titles": ["B标题1", "B标题2"],
            "subtitles": ["B副标题1"],
        },
    ]

    class FakeService:
        @staticmethod
        def get_batch(_batch_id, include_content=False):
            return {"id": "b1", "status": "ready_for_review", "jobs": jobs}

        @staticmethod
        def select_job(_batch_id, job_id, *, title_index, subtitle_index=None):
            job = next(item for item in jobs if item["id"] == job_id)
            job["selected_title"] = job["titles"][title_index]
            job["selected_subtitle"] = (
                job["subtitles"][subtitle_index]
                if subtitle_index is not None
                else None
            )
            job["review_status"] = "viewed"
            return dict(job)

        @staticmethod
        def confirm_job(_batch_id, job_id):
            job = next(item for item in jobs if item["id"] == job_id)
            job["review_status"] = "confirmed"
            return {
                **job,
                "review_status": "confirmed",
            }

    sessions = FeishuSessionStore(FakeDb())
    sessions.start_review("c1", {"jobs": jobs})
    replies: list[str] = []
    sends: list[str] = []
    executor = FeishuToolExecutor(
        service=FakeService(),
        config={},
        sessions=sessions,
        default_account_ids=[],
        reply_text=lambda _message_id, text: replies.append(text),
        send_text=lambda _chat_id, text: sends.append(text),
    )

    executor.execute(
        AgentPlan(
            intent="选择当前文章标题",
            analysis_summary="当前审核项为公众号A",
            tool="select_article_title",
            arguments={"title_number": 2, "subtitle_number": 1},
        ),
        original_text="标题2、副标题1",
        message_id="m1",
        chat_id="c1",
        open_id="u1",
        current_batch_id="b1",
    )

    assert "审核进度：0/2" in replies[-1]
    assert "当前文章尚未确认" in replies[-1]
    assert "【公众号A】任务 #11 正文预览" in sends[-1]
    assert sessions.current_review_job_id("c1") == 11

    executor.execute(
        AgentPlan(
            intent="写入全部草稿箱",
            analysis_summary="用户明确确认，但仍有文章未审核",
            tool="write_all_to_drafts",
            arguments={},
        ),
        original_text="确认全部写入草稿箱",
        message_id="m-blocked",
        chat_id="c1",
        open_id="u1",
        current_batch_id="b1",
    )

    assert "还有 2 个公众号文章未完成审核：公众号A、公众号B" in replies[-1]

    executor.execute(
        AgentPlan(
            intent="确认当前文章",
            analysis_summary="用户已查看公众号A",
            tool="confirm_article",
            arguments={},
        ),
        original_text="确认此文章",
        message_id="m-confirm-a",
        chat_id="c1",
        open_id="u1",
        current_batch_id="b1",
    )

    assert "审核进度：1/2" in replies[-1]
    assert "下一篇：公众号B" in replies[-1]
    assert "【公众号B】任务 #12 正文预览" in sends[-1]
    assert sessions.current_review_job_id("c1") == 12

    executor.execute(
        AgentPlan(
            intent="选择当前文章标题",
            analysis_summary="当前审核项为公众号B",
            tool="select_article_title",
            arguments={"title_number": 1},
        ),
        original_text="标题1",
        message_id="m2",
        chat_id="c1",
        open_id="u1",
        current_batch_id="b1",
    )

    assert "审核进度：1/2" in replies[-1]
    assert "当前文章尚未确认" in replies[-1]
    assert sessions.all_reviews_completed("c1") is False

    executor.execute(
        AgentPlan(
            intent="确认当前文章",
            analysis_summary="用户已查看公众号B",
            tool="confirm_article",
            arguments={},
        ),
        original_text="确认此文章",
        message_id="m-confirm-b",
        chat_id="c1",
        open_id="u1",
        current_batch_id="b1",
    )

    assert "审核进度：2/2" in replies[-1]
    assert "全部文章已确认" in replies[-1]
    assert sessions.all_reviews_completed("c1") is True


def test_feishu_can_select_tenth_title_and_tenth_subtitle() -> None:
    contexts: dict[str, dict] = {}
    titles = [f"第{index}个主标题候选" for index in range(1, 11)]
    subtitles = [f"第{index}个副标题候选" for index in range(1, 11)]
    job = {
        "id": 110,
        "account_name": "十候选公众号",
        "status": "ready_for_review",
        "review_status": "viewed",
        "body": "用于飞书预览的正文内容。",
        "titles": titles,
        "subtitles": subtitles,
    }
    selected_indexes: list[tuple[int, int | None]] = []

    class FakeDb:
        def get_bot_context(self, scope_id):
            return dict(contexts.get(scope_id) or {})

        def set_bot_context(self, scope_id, value):
            contexts[scope_id] = value

    class FakeService:
        @staticmethod
        def get_batch(_batch_id, include_content=False):
            return {
                "id": "batch-ten",
                "status": "ready_for_review",
                "jobs": [job],
            }

        @staticmethod
        def select_job(
            _batch_id,
            _job_id,
            *,
            title_index,
            subtitle_index=None,
        ):
            selected_indexes.append((title_index, subtitle_index))
            return {
                **job,
                "selected_title": titles[title_index],
                "selected_subtitle": (
                    subtitles[subtitle_index]
                    if subtitle_index is not None
                    else None
                ),
            }

    sessions = FeishuSessionStore(FakeDb())
    sessions.start_review("chat-ten", {"jobs": [job]})
    replies: list[str] = []
    executor = FeishuToolExecutor(
        service=FakeService(),
        config={},
        sessions=sessions,
        default_account_ids=[],
        reply_text=lambda _message_id, text: replies.append(text),
        send_text=lambda _chat_id, _text: None,
    )

    executor.execute(
        AgentPlan(
            intent="选择第十组标题",
            analysis_summary="用户明确选择第10个主标题和第10个副标题",
            tool="select_article_title",
            arguments={"title_number": 10, "subtitle_number": 10},
        ),
        original_text="标题10，副标题10",
        message_id="message-ten",
        chat_id="chat-ten",
        open_id="user-ten",
        current_batch_id="batch-ten",
    )

    assert selected_indexes == [(9, 9)]
    assert f"标题：{titles[9]}" in replies[-1]
    assert f"副标题：{subtitles[9]}" in replies[-1]
