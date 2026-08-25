from __future__ import annotations

from typing import Any

from nicegui import ui

from app.services.billing import (
    MICRO_CNY_PER_CNY,
    BillingService,
    live_configuration_issues,
)

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
    credits = dict(summary.get("credits") or {})
    mode = str(summary.get("mode") or "shadow")
    with ui.row().classes("ops-billing-notice"):
        ui.icon("payments" if mode == "live" else "visibility", size="18px")
        ui.label(str(summary.get("notice") or "当前为积分试算。"))
    with ui.element("div").classes("ops-billing-metrics"):
        _metric(
            "可用积分",
            _integer(credits.get("available")),
            "正式计费时可用于任务冻结",
        )
        _metric(
            "已冻结积分",
            _integer(credits.get("reserved")),
            "任务完成后结算并退回差额",
        )
        _metric(
            "近 30 天已消耗",
            _integer(credits.get("charged")),
            "只统计完成并正式结算的积分",
        )
        _metric(
            "预计积分",
            _integer(usage.get("estimated_points")),
            "影子模式仅试算，正式模式展示结算前估值",
        )
        _metric(
            "近 30 天 AI 操作",
            _integer(usage.get("operations")),
            "生成、评审、改写和生图均按任务聚合",
        )
        _metric(
            "实际 Token",
            _integer(
                int(usage.get("input_tokens") or 0)
                + int(usage.get("output_tokens") or 0)
            ),
            f"缓存输入 {_integer(usage.get('cached_input_tokens'))}",
        )

    rows = service.list_usage(limit=100)
    columns = [
        {"name": "created_at", "label": "时间", "field": "created_at", "align": "left"},
        {"name": "scene_label", "label": "功能", "field": "scene_label", "align": "left"},
        {"name": "status_label", "label": "状态", "field": "status_label", "align": "left"},
        {"name": "input_tokens", "label": "输入", "field": "input_tokens", "align": "right"},
        {"name": "output_tokens", "label": "输出", "field": "output_tokens", "align": "right"},
        {"name": "metering", "label": "Token 计量", "field": "metering", "align": "left"},
        {"name": "provider_credits", "label": "Credits", "field": "provider_credits", "align": "right"},
        {"name": "image_count", "label": "图片", "field": "image_count", "align": "right"},
        {"name": "estimated_points", "label": "预计积分", "field": "estimated_points", "align": "right"},
        {"name": "charged_points", "label": "实际消耗", "field": "charged_points", "align": "right"},
    ]
    public_rows = [
        {
            **row,
            "scene_label": _SCENE_LABELS.get(str(row.get("scene") or ""), str(row.get("scene") or "未知操作")),
            "status_label": {
                "succeeded": "完成",
                "failed": "失败，未扣积分",
                "pricing_incomplete": "计价待核对，未扣积分",
                "rejected": "积分不足或配置未完成",
                "expired": "冻结已退回",
                "running": "进行中",
            }.get(str(row.get("status") or ""), "进行中"),
            "input_tokens": _integer(row.get("input_tokens")),
            "output_tokens": _integer(row.get("output_tokens")),
            "metering": (
                f"实际 {int(row.get('metered_calls') or 0)} 次"
                if not int(row.get("unavailable_calls") or 0)
                and not int(row.get("estimated_calls") or 0)
                else (
                    f"缺失 {int(row.get('unavailable_calls') or 0)} 次"
                    if int(row.get("unavailable_calls") or 0)
                    else f"估算 {int(row.get('estimated_calls') or 0)} 次"
                )
            ),
            "provider_credits": _integer(row.get("provider_credits")),
            "image_count": _integer(row.get("image_count")),
            "estimated_points": _integer(row.get("estimated_points")),
            "charged_points": _integer(row.get("charged_points")),
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
                ui.label("还没有积分用量")
                ui.label("下一次生成、评审、改写或生图后会自动出现在这里。")


def build_admin_billing_panel(state: Any) -> None:
    platform_db = state.db.for_user("")

    @ui.refreshable
    def commercial_controls() -> None:
        policy = platform_db.get_billing_pricing_policy()
        readiness_issues = live_configuration_issues(platform_db)
        with ui.column().classes("admin-card w-full gap-4 min-w-0"):
            with ui.row().classes("w-full items-start justify-between gap-3"):
                with ui.column().classes("gap-1 min-w-0"):
                    ui.label("商业积分政策").classes(
                        "text-h6 text-weight-bold"
                    )
                    ui.label(
                        "100 积分默认标价 1 元；正式模式会先冻结、完成后结算并退回差额。"
                    ).classes("muted")
                ui.badge(
                    {
                        "off": "已暂停",
                        "shadow": "积分试算",
                        "live": "正式计费",
                    }.get(str(policy.get("mode") or "shadow"), "积分试算"),
                    color=(
                        "positive"
                        if str(policy.get("mode")) == "live"
                        else "blue-grey-7"
                    ),
                )
            if readiness_issues:
                ui.label(
                    "正式计费启用前还需：" + "；".join(readiness_issues)
                ).classes("text-caption text-deep-orange-9")

            with ui.element("div").classes(
                "grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 "
                "w-full min-w-0"
            ):
                mode_in = ui.select(
                    {
                        "off": "暂停计量",
                        "shadow": "积分试算（不扣费）",
                        "live": "正式计费",
                    },
                    value=str(policy.get("mode") or "shadow"),
                    label="运行模式",
                ).classes("w-full min-w-0")
                point_value_in = ui.number(
                    "每积分标价（元）",
                    value=int(policy.get("point_retail_micro_cny") or 10_000)
                    / MICRO_CNY_PER_CNY,
                    min=0.000001,
                    step=0.001,
                ).classes("w-full min-w-0")
                discount_in = ui.number(
                    "套餐最大折扣（%）",
                    value=int(
                        policy.get("max_package_discount_basis_points") or 0
                    )
                    / 100,
                    min=0,
                    max=100,
                    step=0.1,
                ).classes("w-full min-w-0")
                margin_in = ui.number(
                    "目标贡献毛利率（%）",
                    value=int(policy.get("target_margin_basis_points") or 0)
                    / 100,
                    min=0,
                    max=100,
                    step=0.1,
                ).classes("w-full min-w-0")
                payment_fee_in = ui.number(
                    "支付费率（%）",
                    value=int(policy.get("payment_fee_basis_points") or 0)
                    / 100,
                    min=0,
                    max=100,
                    step=0.1,
                ).classes("w-full min-w-0")
                tax_in = ui.number(
                    "税费（%）",
                    value=int(policy.get("tax_basis_points") or 0) / 100,
                    min=0,
                    max=100,
                    step=0.1,
                ).classes("w-full min-w-0")
                risk_in = ui.number(
                    "全局风险准备率（%）",
                    value=int(
                        policy.get("provider_risk_reserve_basis_points") or 0
                    )
                    / 100,
                    min=0,
                    max=100,
                    step=0.1,
                ).classes("w-full min-w-0")
                task_cost_in = ui.number(
                    "每任务平台变动成本（元）",
                    value=int(
                        policy.get("platform_task_cost_micro_cny") or 0
                    )
                    / MICRO_CNY_PER_CNY,
                    min=0,
                    step=0.01,
                ).classes("w-full min-w-0")
                rounding_in = ui.number(
                    "积分向上取整单位",
                    value=int(policy.get("rounding_points") or 5),
                    min=1,
                    step=1,
                ).classes("w-full min-w-0")
                byok_in = ui.number(
                    "BYOK 基础设施积分",
                    value=int(policy.get("byok_infrastructure_points") or 0),
                    min=0,
                    step=1,
                ).classes("w-full min-w-0")

            def save_policy() -> None:
                selected_mode = str(mode_in.value or "shadow")
                current_issues = live_configuration_issues(platform_db)
                if selected_mode == "live" and current_issues:
                    ui.notify(
                        "不能启用正式计费：" + "；".join(current_issues),
                        type="negative",
                        timeout=8000,
                    )
                    return
                try:
                    platform_db.upsert_billing_pricing_policy(
                        {
                            "name": str(
                                policy.get("name") or "默认商业积分政策"
                            ),
                            "mode": selected_mode,
                            "point_retail_micro_cny": round(
                                float(point_value_in.value or 0)
                                * MICRO_CNY_PER_CNY
                            ),
                            "max_package_discount_basis_points": round(
                                float(discount_in.value or 0) * 100
                            ),
                            "payment_fee_basis_points": round(
                                float(payment_fee_in.value or 0) * 100
                            ),
                            "tax_basis_points": round(
                                float(tax_in.value or 0) * 100
                            ),
                            "target_margin_basis_points": round(
                                float(margin_in.value or 0) * 100
                            ),
                            "provider_risk_reserve_basis_points": round(
                                float(risk_in.value or 0) * 100
                            ),
                            "platform_task_cost_micro_cny": round(
                                float(task_cost_in.value or 0)
                                * MICRO_CNY_PER_CNY
                            ),
                            "rounding_points": int(rounding_in.value or 1),
                            "byok_infrastructure_points": int(
                                byok_in.value or 0
                            ),
                        }
                    )
                except ValueError as exc:
                    ui.notify(str(exc), type="negative")
                    return
                ui.notify("商业积分政策已保存", type="positive")
                commercial_controls.refresh()

            ui.button(
                "保存积分政策",
                icon="save",
                on_click=save_policy,
            ).props("unelevated color=teal-9 no-caps")

        task_rates = platform_db.list_billing_task_rates()
        with ui.column().classes("admin-card w-full gap-4 min-w-0"):
            ui.label("任务价值积分").classes("text-h6 text-weight-bold")
            ui.label(
                "基础积分体现工作流和产品价值；最高冻结积分用于限制单次大任务风险。"
            ).classes("muted")
            task_options = {
                str(item["task_code"]): str(item.get("label") or item["task_code"])
                for item in task_rates
            }
            selected_code = next(iter(task_options), "")
            selected_rate = next(
                (
                    item
                    for item in task_rates
                    if str(item.get("task_code")) == selected_code
                ),
                {},
            )
            with ui.element("div").classes(
                "grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 "
                "w-full min-w-0"
            ):
                task_in = ui.select(
                    task_options,
                    value=selected_code,
                    label="任务",
                ).classes("w-full min-w-0")
                task_label_in = ui.input(
                    "展示名称",
                    value=str(selected_rate.get("label") or ""),
                ).classes("w-full min-w-0")
                task_base_in = ui.number(
                    "基础积分",
                    value=int(selected_rate.get("base_points") or 0),
                    min=0,
                    step=1,
                ).classes("w-full min-w-0")
                task_reserve_in = ui.number(
                    "最高冻结积分",
                    value=int(selected_rate.get("max_reserve_points") or 0),
                    min=0,
                    step=1,
                ).classes("w-full min-w-0")
                task_enabled_in = ui.switch(
                    "启用任务价卡",
                    value=bool(selected_rate.get("enabled", True)),
                )

            def load_task_rate() -> None:
                rate = next(
                    (
                        item
                        for item in task_rates
                        if str(item.get("task_code"))
                        == str(task_in.value or "")
                    ),
                    {},
                )
                task_label_in.value = str(rate.get("label") or "")
                task_base_in.value = int(rate.get("base_points") or 0)
                task_reserve_in.value = int(
                    rate.get("max_reserve_points") or 0
                )
                task_enabled_in.value = bool(rate.get("enabled", True))

            task_in.on("update:model-value", lambda _=None: load_task_rate())

            def save_task_rate() -> None:
                try:
                    platform_db.upsert_billing_task_rate(
                        {
                            "task_code": str(task_in.value or ""),
                            "label": str(task_label_in.value or ""),
                            "base_points": int(task_base_in.value or 0),
                            "max_reserve_points": int(
                                task_reserve_in.value or 0
                            ),
                            "enabled": bool(task_enabled_in.value),
                        }
                    )
                except ValueError as exc:
                    ui.notify(str(exc), type="negative")
                    return
                ui.notify("任务积分价卡已保存", type="positive")
                commercial_controls.refresh()

            ui.button(
                "保存任务价卡",
                icon="price_check",
                on_click=save_task_rate,
            ).props("outline color=teal-9 no-caps")

        cards = platform_db.list_model_price_cards()
        with ui.column().classes("admin-card w-full gap-4 min-w-0"):
            ui.label("服务商成本价卡").classes("text-h6 text-weight-bold")
            ui.label(
                "Token、按次、按 Credit 和 BYOK 分开录入采购成本；人民币金额会转换为微元整数保存。"
            ).classes("muted")
            card_options = {"": "新建价格卡"}
            card_options.update(
                {
                    str(item["id"]): " / ".join(
                        (
                            str(item.get("provider") or ""),
                            str(item.get("provider_model") or "*"),
                            str(item.get("modality") or "text"),
                        )
                    )
                    for item in cards
                }
            )
            with ui.element("div").classes(
                "grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 "
                "w-full min-w-0"
            ):
                card_in = ui.select(
                    card_options,
                    value="",
                    label="价格卡",
                ).classes("w-full min-w-0")
                provider_in = ui.input("服务商", value="").classes(
                    "w-full min-w-0"
                )
                model_in = ui.input("模型（* 表示通配）", value="*").classes(
                    "w-full min-w-0"
                )
                modality_in = ui.select(
                    {"text": "文本", "image": "图片"},
                    value="text",
                    label="模态",
                ).classes("w-full min-w-0")
                metering_in = ui.select(
                    {
                        "TOKEN": "实际 Token",
                        "FIXED": "按次固定成本",
                        "UNIT": "按 Credit / 单位",
                        "BYOK": "用户自带 API",
                    },
                    value="TOKEN",
                    label="计量模式",
                ).classes("w-full min-w-0")
                input_rate_in = ui.number(
                    "输入价（元/百万 Token）", value=0, min=0, step=0.01
                ).classes("w-full min-w-0")
                cache_rate_in = ui.number(
                    "缓存输入价（元/百万）", value=0, min=0, step=0.01
                ).classes("w-full min-w-0")
                output_rate_in = ui.number(
                    "输出价（元/百万 Token）", value=0, min=0, step=0.01
                ).classes("w-full min-w-0")
                reasoning_rate_in = ui.number(
                    "推理价（元/百万 Token）", value=0, min=0, step=0.01
                ).classes("w-full min-w-0")
                image_rate_in = ui.number(
                    "图片价（元/张）", value=0, min=0, step=0.01
                ).classes("w-full min-w-0")
                fixed_rate_in = ui.number(
                    "固定价（元/次）", value=0, min=0, step=0.01
                ).classes("w-full min-w-0")
                unit_rate_in = ui.number(
                    "单位价（元/Credit）", value=0, min=0, step=0.0001
                ).classes("w-full min-w-0")
                provider_risk_in = ui.number(
                    "服务商风险系数（%）",
                    value=100,
                    min=0.01,
                    step=1,
                ).classes("w-full min-w-0")
                card_enabled_in = ui.switch("启用价格卡", value=True)

            money_fields = {
                "input_micro_cny_per_million": input_rate_in,
                "cached_input_micro_cny_per_million": cache_rate_in,
                "output_micro_cny_per_million": output_rate_in,
                "reasoning_micro_cny_per_million": reasoning_rate_in,
                "image_micro_cny_each": image_rate_in,
                "fixed_request_micro_cny": fixed_rate_in,
                "provider_unit_micro_cny_each": unit_rate_in,
            }

            def load_card() -> None:
                card = next(
                    (
                        item
                        for item in cards
                        if str(item.get("id")) == str(card_in.value or "")
                    ),
                    {},
                )
                provider_in.value = str(card.get("provider") or "")
                model_in.value = str(card.get("provider_model") or "*")
                modality_in.value = str(card.get("modality") or "text")
                metering_in.value = str(card.get("metering_mode") or "TOKEN")
                for field, control in money_fields.items():
                    control.value = int(card.get(field) or 0) / MICRO_CNY_PER_CNY
                provider_risk_in.value = int(
                    card.get("provider_risk_basis_points") or 10_000
                ) / 100
                card_enabled_in.value = bool(card.get("enabled", True))

            card_in.on("update:model-value", lambda _=None: load_card())

            def save_card() -> None:
                payload = {
                    "id": str(card_in.value or "") or None,
                    "provider": str(provider_in.value or "").strip(),
                    "provider_model": str(model_in.value or "*").strip(),
                    "modality": str(modality_in.value or "text"),
                    "metering_mode": str(metering_in.value or "TOKEN"),
                    "provider_risk_basis_points": round(
                        float(provider_risk_in.value or 0) * 100
                    ),
                    "enabled": bool(card_enabled_in.value),
                }
                payload.update(
                    {
                        field: round(
                            float(control.value or 0) * MICRO_CNY_PER_CNY
                        )
                        for field, control in money_fields.items()
                    }
                )
                try:
                    platform_db.upsert_model_price_card(payload)
                except ValueError as exc:
                    ui.notify(str(exc), type="negative")
                    return
                ui.notify("服务商成本价卡已保存", type="positive")
                commercial_controls.refresh()

            ui.button(
                "保存服务商价卡",
                icon="add_card",
                on_click=save_card,
            ).props("outline color=teal-9 no-caps")

        users = state.auth.list_users()
        with ui.column().classes("admin-card w-full gap-4 min-w-0"):
            ui.label("积分发放").classes("text-h6 text-weight-bold")
            ui.label(
                "用于套餐月度发放、活动赠送或人工补偿；每笔发放都会写入不可变积分流水。"
            ).classes("muted")
            user_options = {
                str(item["id"]): str(item.get("username") or item["id"])
                for item in users
            }
            with ui.element("div").classes(
                "grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 "
                "w-full min-w-0"
            ):
                grant_user_in = ui.select(
                    user_options,
                    value=next(iter(user_options), None),
                    label="用户",
                ).classes("w-full min-w-0")
                grant_points_in = ui.number(
                    "发放积分", value=1_000, min=1, step=1
                ).classes("w-full min-w-0")
                grant_expiry_in = ui.input(
                    "到期时间（ISO，可留空）", value=""
                ).classes("w-full min-w-0")
                grant_reason_in = ui.input(
                    "发放原因", value="管理员发放"
                ).classes("w-full min-w-0")

            def grant_points() -> None:
                user_id = str(grant_user_in.value or "")
                if not user_id:
                    ui.notify("请选择用户", type="negative")
                    return
                try:
                    platform_db.for_user(user_id).grant_credit_points(
                        points=int(grant_points_in.value or 0),
                        source_type="admin",
                        expires_at=str(grant_expiry_in.value or "") or None,
                        actor_user_id=str(
                            (getattr(state, "current_user", None) or {}).get("id")
                            or "admin-ui"
                        ),
                        reason=str(grant_reason_in.value or ""),
                    )
                except ValueError as exc:
                    ui.notify(str(exc), type="negative")
                    return
                ui.notify("积分已发放并写入流水", type="positive")

            ui.button(
                "发放积分",
                icon="redeem",
                on_click=grant_points,
            ).props("unelevated color=teal-9 no-caps")

    commercial_controls()
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
                    {"name": "token_usage_status", "label": "Token 状态", "field": "token_usage_status", "align": "left"},
                    {"name": "tokens", "label": "Token", "field": "tokens", "align": "right"},
                    {"name": "provider_credits", "label": "Credits", "field": "provider_credits", "align": "right"},
                    {"name": "image_count", "label": "图片", "field": "image_count", "align": "right"},
                    {"name": "cost", "label": "成本", "field": "cost", "align": "right"},
                    {"name": "pricing_status", "label": "价格状态", "field": "pricing_status", "align": "left"},
                    {"name": "billable", "label": "可计费", "field": "billable", "align": "left"},
                ],
                rows=[
                    {
                        **row,
                        "tokens": (
                            _integer(row.get("total_tokens"))
                            if row.get("token_usage_status") == "RECORDED"
                            else "—"
                        ),
                        "provider_credits": (
                            _integer(row.get("provider_credits"))
                            if row.get("provider_credits") is not None
                            else "—"
                        ),
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
            ui.label("还没有积分计量记录。").classes("muted")

    cards = platform_db.list_model_price_cards()
    with ui.column().classes("admin-card w-full gap-3 min-w-0"):
        ui.label("已保存的服务商价格卡").classes("text-h6 text-weight-bold")
        ui.label("这里只展示非敏感成本参数；可在上方选择对应价格卡继续编辑。")\
            .classes("muted")
        if cards:
            ui.table(
                columns=[
                    {"name": "provider", "label": "供应商", "field": "provider", "align": "left"},
                    {"name": "provider_model", "label": "模型", "field": "provider_model", "align": "left"},
                    {"name": "modality", "label": "模态", "field": "modality", "align": "left"},
                    {"name": "metering_mode", "label": "计量模式", "field": "metering_mode", "align": "left"},
                    {"name": "effective_from", "label": "生效时间", "field": "effective_from", "align": "left"},
                    {"name": "enabled", "label": "启用", "field": "enabled", "align": "left"},
                ],
                rows=[{**row, "enabled": "是" if row.get("enabled") else "否"} for row in cards],
                row_key="id",
            ).classes("w-full min-w-0")
        else:
            ui.label("尚未配置价格卡；正式计费会保持不可启用。")\
                .classes("muted")


__all__ = [
    "build_admin_billing_panel",
    "build_billing_panel",
]
