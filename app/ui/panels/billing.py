from __future__ import annotations

from typing import Any

from nicegui import ui

from app.services.billing import BillingService, MICRO_CNY_PER_CNY


_SCENE_LABELS = {
    "article_generation": "文章生成",
    "editorial_review": "AI 评审",
    "editorial_rewrite": "评审修改稿",
    "paragraph_regeneration": "段落重写",
    "inline_images_regeneration": "正文配图",
    "inline_image_regeneration": "单张配图",
    "cover_regeneration": "封面生成",
}


def _integer(value: Any) -> str:
    return f"{int(value or 0):,}"


def _metric(label: str, value: str, hint: str) -> None:
    with ui.column().classes("ops-billing-metric"):
        ui.label(label).classes("ops-billing-metric-label")
        ui.label(value).classes("ops-billing-metric-value")
        ui.label(hint).classes("ops-billing-metric-hint")


def build_billing_panel(state: Any) -> None:
    """Customer-safe usage view; provider cost and internal pricing stay hidden."""

    service = BillingService(state.db)
    summary = service.summary()
    usage = dict(summary.get("usage") or {})
    with ui.row().classes("ops-billing-notice"):
        ui.icon("visibility", size="18px")
        ui.label(str(summary.get("notice") or "当前为影子计量。"))
    with ui.element("div").classes("ops-billing-metrics"):
        _metric("近 30 天操作", _integer(usage.get("operations")), "按一次可理解的 AI 操作聚合")
        _metric(
            "输入 Token",
            _integer(usage.get("input_tokens")),
            f"其中缓存 {_integer(usage.get('cached_input_tokens'))}",
        )
        _metric("输出 Token", _integer(usage.get("output_tokens")), "推理 Token 已包含在输出中")
        _metric("生成图片", _integer(usage.get("images")), "按成功或失败请求审计")
        _metric(
            "影子积分",
            _integer(usage.get("estimated_points")),
            "仅估算，实际扣除 0",
        )

    rows = service.list_usage(limit=100)
    columns = [
        {"name": "created_at", "label": "时间", "field": "created_at", "align": "left"},
        {"name": "scene_label", "label": "功能", "field": "scene_label", "align": "left"},
        {"name": "status_label", "label": "状态", "field": "status_label", "align": "left"},
        {"name": "input_tokens", "label": "输入", "field": "input_tokens", "align": "right"},
        {"name": "output_tokens", "label": "输出", "field": "output_tokens", "align": "right"},
        {"name": "image_count", "label": "图片", "field": "image_count", "align": "right"},
        {"name": "estimated_points", "label": "影子积分", "field": "estimated_points", "align": "right"},
        {"name": "charged_points", "label": "实际扣除", "field": "charged_points", "align": "right"},
    ]
    public_rows = [
        {
            **row,
            "scene_label": _SCENE_LABELS.get(str(row.get("scene") or ""), str(row.get("scene") or "未知操作")),
            "status_label": "完成" if row.get("status") == "succeeded" else ("失败" if row.get("status") == "failed" else "进行中"),
            "input_tokens": _integer(row.get("input_tokens")),
            "output_tokens": _integer(row.get("output_tokens")),
            "image_count": _integer(row.get("image_count")),
            "estimated_points": _integer(row.get("estimated_points")),
            "charged_points": "0",
        }
        for row in rows
    ]
    with ui.column().classes("ops-billing-section"):
        ui.label("用量明细").classes("ops-billing-section-title")
        ui.label("不会展示提示词、正文、API Key 或平台成本。").classes(
            "ops-billing-section-hint"
        )
        if public_rows:
            ui.table(
                columns=columns,
                rows=public_rows,
                row_key="id",
                pagination={"rowsPerPage": 20},
            ).classes("ops-billing-table")
        else:
            with ui.column().classes("ops-billing-empty"):
                ui.icon("query_stats", size="30px")
                ui.label("还没有影子用量")
                ui.label("下一次生成、评审、改写或生图后会自动出现在这里。")


def build_admin_billing_panel(state: Any) -> None:
    platform_db = state.db.for_user("")
    summary = platform_db.admin_billing_usage_summary()
    with ui.row().classes("w-full gap-4 items-stretch"):
        for label, value in (
            ("近 30 天操作", _integer(summary.get("operations"))),
            ("模型调用", _integer(summary.get("events"))),
            (
                "供应商成本",
                f"¥{int(summary.get('provider_cost_micro_cny') or 0) / MICRO_CNY_PER_CNY:,.2f}",
            ),
            ("缺少价格卡", _integer(summary.get("price_missing_events"))),
        ):
            with ui.column().classes("admin-card admin-stat grow min-w-0"):
                ui.label(label).classes("muted")
                ui.label(value).classes("admin-stat-value")
    events = platform_db.admin_list_ai_usage_events(limit=100)
    with ui.column().classes("admin-card w-full gap-3 min-w-0"):
        ui.label("AI 成本与用量明细").classes("text-h6 text-weight-bold")
        ui.label(
            "失败和回退调用保留成本记录，但只有成功且贡献最终结果的平台调用才标记可计费。"
        ).classes("muted")
        if events:
            ui.table(
                columns=[
                    {"name": "created_at", "label": "时间", "field": "created_at", "align": "left"},
                    {"name": "provider", "label": "供应商", "field": "provider", "align": "left"},
                    {"name": "provider_model", "label": "模型", "field": "provider_model", "align": "left"},
                    {"name": "funding_source", "label": "资金来源", "field": "funding_source", "align": "left"},
                    {"name": "usage_source", "label": "计量来源", "field": "usage_source", "align": "left"},
                    {"name": "tokens", "label": "Token", "field": "tokens", "align": "right"},
                    {"name": "image_count", "label": "图片", "field": "image_count", "align": "right"},
                    {"name": "cost", "label": "成本", "field": "cost", "align": "right"},
                    {"name": "pricing_status", "label": "价格状态", "field": "pricing_status", "align": "left"},
                    {"name": "billable", "label": "可计费", "field": "billable", "align": "left"},
                ],
                rows=[
                    {
                        **row,
                        "tokens": _integer(int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0)),
                        "image_count": _integer(row.get("image_count")),
                        "cost": f"¥{int(row.get('provider_cost_micro_cny') or 0) / MICRO_CNY_PER_CNY:,.4f}",
                        "billable": "是" if row.get("billable") else "否",
                    }
                    for row in events
                ],
                row_key="id",
                pagination={"rowsPerPage": 20},
            ).classes("w-full min-w-0")
        else:
            ui.label("还没有影子计量记录。").classes("muted")

    cards = platform_db.list_model_price_cards()
    with ui.column().classes("admin-card w-full gap-3 min-w-0"):
        ui.label("模型价格卡").classes("text-h6 text-weight-bold")
        ui.label("金额均以微人民币整数保存；价格卡通过管理员 API 版本化维护。")\
            .classes("muted")
        if cards:
            ui.table(
                columns=[
                    {"name": "provider", "label": "供应商", "field": "provider", "align": "left"},
                    {"name": "provider_model", "label": "模型", "field": "provider_model", "align": "left"},
                    {"name": "modality", "label": "模态", "field": "modality", "align": "left"},
                    {"name": "effective_from", "label": "生效时间", "field": "effective_from", "align": "left"},
                    {"name": "enabled", "label": "启用", "field": "enabled", "align": "left"},
                ],
                rows=[{**row, "enabled": "是" if row.get("enabled") else "否"} for row in cards],
                row_key="id",
            ).classes("w-full min-w-0")
        else:
            ui.label("尚未配置价格卡；用量仍会记录并标记为 price_missing。")\
                .classes("muted")


__all__ = ["build_admin_billing_panel", "build_billing_panel"]
