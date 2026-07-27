from __future__ import annotations

import re
from collections.abc import Callable

from app.feishu.agent import AgentPlan
from app.feishu.constants import HELP_TEXT, SELECTION_PATTERN, URL_PATTERN
from app.feishu.session import FeishuSessionStore
from app.feishu.tool_executor import FeishuToolExecutor


class LegacyCommandHandler:
    """Translate legacy deterministic commands into the same tool executor."""

    def __init__(
        self,
        *,
        sessions: FeishuSessionStore,
        executor: FeishuToolExecutor,
        reply_text: Callable[[str, str], None],
    ) -> None:
        self.sessions = sessions
        self.executor = executor
        self.reply_text = reply_text

    def dispatch(self, text: str, message_id: str, chat_id: str, open_id: str) -> None:
        if not text or text in {"帮助", "help", "?", "？"}:
            self.reply_text(message_id, HELP_TEXT)
            return
        batch_id = self.sessions.current_batch_id(chat_id)
        plan: AgentPlan | None = None
        url = URL_PATTERN.search(text)
        if url and any(word in text for word in ("收藏", "收集", "文章池", "仅保存")):
            plan = AgentPlan("收集文章", "检测到文章收集指令", tool="collect_article_link", arguments={"source_url": url.group(0).rstrip("。；，,;")})
        elif url:
            plan = AgentPlan("链接改写", "检测到文章链接", tool="create_rewrite_batch", arguments={"source_url": url.group(0).rstrip("。；，,;")})
        elif text.startswith("状态"):
            plan = AgentPlan("查询进度", "固定状态指令", tool="get_batch_status")
        elif text.startswith("终止"):
            plan = AgentPlan("终止改写", "固定终止指令", tool="cancel_rewrite_batch")
        elif text.startswith("确认写入") or "写入草稿箱" in text:
            plan = AgentPlan("写入草稿箱", "固定确认指令", tool="write_all_to_drafts")
        else:
            body = re.fullmatch(r"查看正文\s*#?(\d+)", text)
            selection = SELECTION_PATTERN.fullmatch(text)
            if body:
                plan = AgentPlan("查看正文", "固定预览指令", tool="get_article_result", arguments={"job_id": int(body.group(1))})
            elif selection:
                plan = AgentPlan(
                    "选择标题",
                    "固定标题选择指令",
                    tool="select_article_title",
                    arguments={
                        "job_id": int(selection.group(1)),
                        "title_number": int(selection.group(2)),
                        "subtitle_number": int(selection.group(3)) if selection.group(3) else None,
                    },
                )
        if not plan:
            self.reply_text(message_id, "无法识别该指令。\n\n" + HELP_TEXT)
            return
        self.executor.execute(
            plan,
            original_text=text,
            message_id=message_id,
            chat_id=chat_id,
            open_id=open_id,
            current_batch_id=batch_id,
        )
