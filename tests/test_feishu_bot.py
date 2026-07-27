from __future__ import annotations

import json
from types import SimpleNamespace

from app.db import Database
from app.feishu.bot import FeishuBot
from app.feishu.pairing import create_pairing_code
from app.feishu.runtime import get_runtime, update_runtime
from app.feishu.settings import public_feishu_settings, save_feishu_settings


class _FakeDb:
    def __init__(self) -> None:
        self.events: list[str] = []

    def claim_event(self, event_id: str) -> bool:
        self.events.append(event_id)
        return True


def test_current_lark_event_message_shape_is_dispatched() -> None:
    bot = FeishuBot.__new__(FeishuBot)
    bot.service = SimpleNamespace(db=_FakeDb())
    bot.app_id = "cli_test"
    bot.allow_all = True
    bot.allowed_open_ids = set()
    bot.allowed_chat_ids = set()
    dispatched: list[tuple[str, str, str, str]] = []
    replies: list[tuple[str, str]] = []
    bot._dispatch_text = lambda text, message_id, chat_id, open_id: dispatched.append(
        (text, message_id, chat_id, open_id)
    )
    bot._reply_text = lambda message_id, text: replies.append((message_id, text))

    data = SimpleNamespace(
        header=SimpleNamespace(event_id="event-1"),
        event=SimpleNamespace(
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="open-1")),
            message=SimpleNamespace(
                message_id="message-1",
                chat_id="chat-1",
                message_type="text",
                content=json.dumps({"text": "@_user_1 帮助"}, ensure_ascii=False),
            ),
        ),
    )

    bot._handle_message(data)

    assert bot.service.db.events == ["event-1"]
    assert dispatched == [("帮助", "message-1", "chat-1", "open-1")]
    assert replies == []


def test_bot_stays_connecting_until_a_real_event_arrives(tmp_path) -> None:
    db = Database(tmp_path / "app.db")
    update_runtime(
        db,
        status="running",
        last_message_at="2026-01-01T00:00:00+00:00",
        last_reply_at="2026-01-01T00:00:01+00:00",
        last_chat_id="oc_old",
        last_open_id="ou_old",
    )
    bot = FeishuBot.__new__(FeishuBot)
    bot.service = SimpleNamespace(db=db)
    bot.app_id = "cli_current"
    callbacks: list[object] = []
    bot.gateway = SimpleNamespace(
        start=lambda callback: callbacks.append(callback)
    )

    bot.start()

    runtime = get_runtime(db)
    assert callbacks == [bot._on_message_event]
    assert runtime["status"] == "connecting"
    assert runtime["app_id"] == "cli_current"
    assert runtime["started_at"]
    assert runtime["last_message_at"] == ""
    assert runtime["last_reply_at"] == ""
    assert runtime["last_chat_id"] == ""
    assert runtime["last_open_id"] == ""


def test_unauthorized_user_can_pair_once_and_use_current_bot(
    tmp_path,
) -> None:
    db = Database(tmp_path / "app.db")
    save_feishu_settings(
        db,
        enabled=True,
        app_id="cli_pairing",
        app_secret="secret-value",
        allow_all=False,
        allowed_open_ids=[],
        agent_model_id="model-1",
    )
    pairing = create_pairing_code(db)
    bot = FeishuBot.__new__(FeishuBot)
    bot.service = SimpleNamespace(db=db)
    bot.app_id = "cli_pairing"
    bot.allow_all = False
    bot.allowed_open_ids = set()
    bot.allowed_chat_ids = set()
    bot.tool_executor = SimpleNamespace(admin_open_ids=set())
    replies: list[tuple[str, str]] = []
    dispatched: list[tuple[str, str, str, str]] = []
    bot._reply_text = lambda message_id, text: replies.append(
        (message_id, text)
    )
    bot._dispatch_text = (
        lambda text, message_id, chat_id, open_id: dispatched.append(
            (text, message_id, chat_id, open_id)
        )
    )

    bot._handle_message(
        _message_event(
            event_id="event-pair",
            message_id="message-pair",
            chat_id="oc_pairing",
            open_id="ou_new",
            text=pairing["message"],
        )
    )

    assert len(replies) == 1
    assert "绑定成功" in replies[0][1]
    assert dispatched == []
    assert bot.allowed_open_ids == {"ou_new"}
    assert bot.tool_executor.admin_open_ids == {"ou_new"}
    assert public_feishu_settings(db)["allowed_open_ids"] == ["ou_new"]
    runtime = get_runtime(db)
    assert runtime["status"] == "running"
    assert runtime["app_id"] == "cli_pairing"
    assert runtime["last_chat_id"] == "oc_pairing"
    assert runtime["last_open_id"] == "ou_new"

    bot._handle_message(
        _message_event(
            event_id="event-help",
            message_id="message-help",
            chat_id="oc_pairing",
            open_id="ou_new",
            text="帮助",
        )
    )
    assert dispatched == [
        ("帮助", "message-help", "oc_pairing", "ou_new")
    ]


def test_partial_failed_batch_starts_review_and_marks_preview_viewed() -> None:
    events: list[tuple[str, object]] = []
    ready_job = {
        "id": 11,
        "account_name": "公众号A",
        "status": "ready_for_review",
        "review_status": "unviewed",
        "body": "已生成正文",
        "titles": ["候选标题"],
        "subtitles": [],
    }
    failed_job = {
        "id": 12,
        "account_name": "公众号B",
        "status": "failed",
        "error": "模型调用失败",
    }

    class _Sessions:
        def start_review(self, chat_id, batch):
            events.append(
                (
                    "start_review",
                    (
                        chat_id,
                        [int(job["id"]) for job in batch.get("jobs") or []],
                    ),
                )
            )
            return {"job_id": 11, "account_name": "公众号A"}

        def update(self, chat_id, **fields):
            events.append(("update", (chat_id, fields)))

    class _Service:
        @staticmethod
        def mark_job_viewed(batch_id, job_id):
            events.append(("mark_viewed", (batch_id, job_id)))
            return {**ready_job, "review_status": "viewed"}

    bot = FeishuBot.__new__(FeishuBot)
    bot.sessions = _Sessions()
    bot.service = _Service()
    bot._send_text = lambda chat_id, text: events.append(
        ("send", (chat_id, text))
    )

    bot._on_batch_changed(
        {
            "id": "batch-partial",
            "chat_id": "chat-1",
            "status": "partial_failed",
            "jobs": [ready_job, failed_job],
        }
    )

    assert ("start_review", ("chat-1", [11, 12])) in events
    assert ("mark_viewed", ("batch-partial", 11)) in events
    sent = [value[1] for kind, value in events if kind == "send"]
    assert any("1 篇文章已生成" in text and "1 篇失败或已停止" in text for text in sent)
    assert any("现在进入逐篇审核" in text for text in sent)
    preview_index = next(
        index
        for index, (kind, value) in enumerate(events)
        if kind == "send" and "正文预览" in value[1]
    )
    mark_index = events.index(("mark_viewed", ("batch-partial", 11)))
    assert mark_index < preview_index
    assert "公众号B" not in next(
        text for text in sent if "现在进入逐篇审核" in text
    )


def test_partial_failed_batch_without_ready_article_keeps_result_path() -> None:
    events: list[tuple[str, object]] = []

    class _Sessions:
        def start_review(self, *_args, **_kwargs):
            raise AssertionError("failed-only batch must not start review")

        def update(self, chat_id, **fields):
            events.append(("update", (chat_id, fields)))

    bot = FeishuBot.__new__(FeishuBot)
    bot.sessions = _Sessions()
    bot.service = SimpleNamespace()
    bot._send_text = lambda chat_id, text: events.append(
        ("send", (chat_id, text))
    )

    bot._on_batch_changed(
        {
            "id": "batch-failed",
            "chat_id": "chat-1",
            "status": "partial_failed",
            "jobs": [
                {
                    "id": 12,
                    "account_name": "公众号B",
                    "status": "failed",
                    "error": "模型调用失败",
                }
            ],
        }
    )

    assert ("update", ("chat-1", {"stage": "partial_failed"})) in events
    assert any(
        kind == "send" and "写入完成" in value[1]
        for kind, value in events
    )


def _message_event(
    *,
    event_id: str,
    message_id: str,
    chat_id: str,
    open_id: str,
    text: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id),
        event=SimpleNamespace(
            sender=SimpleNamespace(
                sender_id=SimpleNamespace(open_id=open_id)
            ),
            message=SimpleNamespace(
                message_id=message_id,
                chat_id=chat_id,
                message_type="text",
                content=json.dumps({"text": text}, ensure_ascii=False),
            ),
        ),
    )
