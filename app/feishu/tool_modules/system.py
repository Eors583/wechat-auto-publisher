from __future__ import annotations

from typing import Any

from app.feishu.runtime import get_runtime
from app.feishu.tool_modules.common import compact


class SystemToolMixin:
    """Read-only operational telemetry shared with the desktop data pages."""

    def _tool_get_feishu_runtime_status(
        self, _args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        runtime = get_runtime(self.service.db)
        lines = [
            "飞书机器人运行状态：",
            f'状态：{runtime.get("status") or "unknown"}',
            f'服务启动：{runtime.get("started_at") or "尚无记录"}',
            f'最近收到消息：{runtime.get("last_message_at") or "尚无记录"}',
            f'最近成功回复：{runtime.get("last_reply_at") or "尚无记录"}',
        ]
        if runtime.get("last_error"):
            lines.append(f'最近失败原因：{compact(runtime.get("last_error"), 500)}')
        self.reply_text(message_id, "\n".join(lines))

    def _tool_get_operational_overview(
        self, args: dict[str, Any], *, message_id: str, **_: Any
    ) -> None:
        if self.analytics is None:
            raise ValueError("当前运行环境未启用运营数据服务")
        overview = self.analytics.get_overview(
            today=str(args.get("date") or "").strip() or None
        )
        self.reply_text(
            message_id,
            "运营数据概览：\n"
            f'日期：{overview["date"]}\n'
            f'今日批次：{overview["today_batches"]}\n'
            f'全部批次：{overview["total_batches"]}'
            f'（已归档 {overview["archived_batches"]}）\n'
            f'文章总数：{overview["total_articles"]}\n'
            f'待审核：{overview["pending_review_articles"]}\n'
            f'已入草稿/已发布：{overview["drafted_or_published_articles"]}\n'
            f'失败：{overview["failed_articles"]}'
            f' · 已停止：{overview["cancelled_articles"]}'
            f' · 处理中：{overview["processing_articles"]}',
        )
