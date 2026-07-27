from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from nicegui import run, ui

from app.config import load_config
from app.render.preview import prepare_preview_html
from app.services.batches import BatchService
from app.ui.state import (
    AppState,
    STATUS_LABEL,
    clean_subtitles,
    clean_titles,
    set_button_loading,
)
from app.ai import clean_candidate_text
from app.ui.image_proxy import wechat_image_proxy_url
from app.ui.lifecycle import client_timer
from app.ui.panels.review_jury import build_review_jury_panel
from app.ui.workflow import next_review_job, render_workflow_guide


REVIEW_LABELS = {
    "unviewed": "未查看",
    "viewed": "已查看，未确认",
    "confirmed": "已确认",
    "needs_changes": "需要修改",
    "drafted": "已写入草稿箱",
    "write_failed": "写入失败",
}

REVIEW_COLORS = {
    "unviewed": "orange-8",
    "viewed": "orange-7",
    "confirmed": "teal-7",
    "needs_changes": "deep-orange-7",
    "drafted": "green-7",
    "write_failed": "red-7",
}


def build_tasks_panel(state: AppState) -> None:
    """Batch-oriented task center shared with the API/Feishu domain rules."""
    service = BatchService(load_config())
    render_workflow_guide(
        "review",
        note="生成完成后在这里逐篇审核，全部确认后再一次写入草稿箱",
        compact=True,
    )
    search_in = ui.input("搜索话题、标题或公众号").props(
        "outlined dense clearable debounce=300"
    ).classes("col")
    status_in = ui.select(
        options={
            "": "全部状态",
            "attention": "待我审核 / 失败",
            "ready_for_review": "待审核",
            "drafted": "已写入草稿箱",
            "failed": "失败",
            "cancelled": "已停止",
        },
        value="",
        label="状态",
    ).props("outlined dense options-dense").style("min-width:180px")
    account_options = {"": "全部公众号", **{
        item["id"]: item["name"] for item in service.list_accounts()
    }}
    account_in = ui.select(
        options=account_options, value="", label="公众号"
    ).props("outlined dense options-dense").style("min-width:190px")
    today_only = ui.switch("只看今天", value=False)
    archived_in = ui.switch("显示已归档", value=False)
    host = ui.column().classes("w-full gap-3 q-mt-md")
    runtime = {
        "has_active_batch": False,
        "review_open": False,
        "focus_batch_id": "",
        "visible_limit": 30,
    }

    with ui.row().classes("w-full items-center q-col-gutter-sm"):
        search_in
        status_in
        account_in
        today_only
        archived_in
        refresh_btn = ui.button("刷新").props("outline dense color=teal-9 icon=refresh")

    def render() -> None:
        host.clear()
        batches = service.list_batches(
            limit=300, include_archived=bool(archived_in.value)
        )
        runtime["has_active_batch"] = any(
            str(batch.get("status") or "") in {"pending", "processing", "injecting"}
            for batch in batches
        )
        batches = [batch for batch in batches if _matches_filters(
            batch,
            search=str(search_in.value or ""),
            status=str(status_in.value or ""),
            account_id=str(account_in.value or ""),
            today=bool(today_only.value),
        )]
        filtered_total = len(batches)
        visible_batches = batches[: int(runtime["visible_limit"])]
        with host:
            if not visible_batches:
                with ui.element("div").classes("card w-full"):
                    ui.label("没有符合条件的批次").classes("text-weight-medium")
                    ui.label("可取消筛选或显示已归档批次。").classes("muted")
                return
            focused_expansion = None
            auto_expanded = 0
            for batch in visible_batches:
                batch_progress = batch.get("progress") or {}
                needs_attention = bool(
                    int(batch_progress.get("unconfirmed") or 0)
                    or int(batch_progress.get("failed") or 0)
                )
                auto_expand = needs_attention and auto_expanded < 3
                if auto_expand:
                    auto_expanded += 1
                expansion = _render_batch_card(
                    state,
                    service,
                    batch,
                    render,
                    review_runtime=runtime,
                    focused=(
                        str(batch.get("id") or "")
                        == str(runtime.get("focus_batch_id") or "")
                    ),
                    auto_expand=auto_expand,
                )
                if (
                    str(batch.get("id") or "")
                    == str(runtime.get("focus_batch_id") or "")
                ):
                    focused_expansion = expansion
            if focused_expansion is not None:
                focused_expansion.run_method(
                    "scrollIntoView",
                    {"behavior": "smooth", "block": "start"},
                )
                runtime["focus_batch_id"] = ""
            if filtered_total > len(visible_batches):
                remaining = filtered_total - len(visible_batches)
                ui.button(
                    f"加载更多批次（剩余 {remaining} 个）",
                    on_click=lambda: (
                        runtime.__setitem__(
                            "visible_limit",
                            int(runtime["visible_limit"]) + 30,
                        ),
                        render(),
                    ),
                ).props("outline color=teal-9 no-caps icon=expand_more").classes(
                    "self-center q-my-md"
                )

    def refresh_and_focus(
        batch_id: str | None = None,
        *,
        status_filter: str | None = None,
        today: bool | None = None,
    ) -> None:
        """Show a newly created batch even when stale filters were active."""
        if batch_id:
            search_in.value = ""
            status_in.value = ""
            account_in.value = ""
            today_only.value = False
            archived_in.value = False
            runtime["focus_batch_id"] = str(batch_id)
        elif status_filter is not None or today is not None:
            search_in.value = ""
            account_in.value = ""
            archived_in.value = False
            status_in.value = str(status_filter or "")
            today_only.value = bool(today)
        render()

    def reset_and_render(_: Any = None) -> None:
        runtime["visible_limit"] = 30
        render()

    for element in (search_in, status_in, account_in, today_only, archived_in):
        element.on_value_change(reset_and_render)
    refresh_btn.on_click(render)
    render()
    state.task_center_refresh = refresh_and_focus

    def refresh_running_batches() -> None:
        if runtime["has_active_batch"] and not runtime["review_open"]:
            render()

    client_timer(3.0, refresh_running_batches)


def open_review_workbench(
    state: AppState,
    service: BatchService,
    batch_id: str,
    job_id: int,
    on_change: Callable[[], None],
    *,
    review_runtime: dict[str, bool] | None = None,
) -> None:
    service.mark_job_viewed(batch_id, job_id)
    batch = service.get_batch(batch_id, include_content=True)
    job = next(item for item in batch["jobs"] if int(item["id"]) == int(job_id))
    article_position = next(
        (
            index
            for index, item in enumerate(batch["jobs"], 1)
            if int(item["id"]) == int(job_id)
        ),
        1,
    )
    if review_runtime is not None:
        review_runtime["review_open"] = True
    with ui.dialog() as dialog, ui.card().classes("w-full").style(
        "max-width:1180px;max-height:94vh;overflow-y:auto"
    ):
        def close_workbench() -> None:
            if review_runtime is not None:
                review_runtime["review_open"] = False
            dialog.close()
            on_change()

        with ui.row().classes("w-full items-center justify-between"):
            with ui.column().classes("gap-0"):
                ui.label(f'文章审核工作台 · {job["account_name"]}').classes(
                    "text-h6 text-weight-bold"
                )
                ui.label(
                    f'批次 #{batch["display_id"]} · '
                    f'第 {article_position}/{len(batch["jobs"])} 篇'
                ).classes("muted")
            ui.button("关闭", on_click=close_workbench).props("flat icon=close")

        render_workflow_guide(
            "review",
            note=f'正在审核：{job["account_name"]}',
            compact=True,
        )

        title_options = clean_titles(job)
        selected_title = clean_candidate_text(
            str(job.get("selected_title") or "")
        ) or (title_options[0] if title_options else "")
        if selected_title and selected_title not in title_options:
            title_options = [selected_title, *title_options[:9]]
        if title_options:
            title_choice = ui.radio(
                {title: title for title in title_options}, value=selected_title
            ).classes("w-full")
        else:
            title_choice = None
        title_in = ui.input("文章标题（可直接修改）", value=selected_title).classes(
            "w-full"
        ).props("outlined stack-label")
        if title_choice:
            title_choice.on_value_change(
                lambda event: setattr(title_in, "value", str(event.value or ""))
            )
        subtitle_options = clean_subtitles(job)
        selected_subtitle = clean_candidate_text(
            str(job.get("selected_subtitle") or "")
        )
        if selected_subtitle and selected_subtitle not in subtitle_options:
            subtitle_options = [selected_subtitle, *subtitle_options[:9]]
        with ui.expansion(
            f"更多优化：副标题与摘要（{len(subtitle_options)} 个副标题候选）",
            icon="tune",
            value=False,
        ).classes("w-full"):
            if subtitle_options:
                ui.label("副标题候选（单选，也可以在下方直接修改）").classes(
                    "text-weight-medium"
                )
                subtitle_choice = ui.radio(
                    {subtitle: subtitle for subtitle in subtitle_options},
                    value=(
                        selected_subtitle
                        if selected_subtitle in subtitle_options
                        else None
                    ),
                ).classes("w-full")
            else:
                subtitle_choice = None
                ui.label("当前没有可用副标题候选").classes("muted")
            subtitle_in = ui.input(
                "副标题（可留空）", value=selected_subtitle
            ).classes("w-full").props("outlined stack-label")
            if subtitle_choice:
                subtitle_choice.on_value_change(
                    lambda event: setattr(
                        subtitle_in,
                        "value",
                        str(event.value or ""),
                    )
                )
                ui.button(
                    "不使用副标题",
                    on_click=lambda: (
                        setattr(subtitle_choice, "value", None),
                        setattr(subtitle_in, "value", ""),
                    ),
                ).props("flat dense color=grey-8 no-caps icon=close")
            digest_in = ui.textarea(
                "摘要", value=str(job.get("digest") or "")
            ).classes("w-full").props("outlined rows=3 stack-label")
        body_in = ui.textarea(
            "正文纯文本", value=str(job.get("body") or "")
        ).classes("w-full").props("outlined rows=18 stack-label")

        def editor_has_unsaved_changes(*, include_body: bool = True) -> bool:
            current_subtitle = str(job.get("selected_subtitle") or "").strip()
            changed = any(
                (
                    str(title_in.value or "").strip()
                    != str(job.get("selected_title") or "").strip(),
                    str(subtitle_in.value or "").strip() != current_subtitle,
                    str(digest_in.value or "").strip()
                    != str(job.get("digest") or "").strip(),
                )
            )
            if include_body:
                changed = changed or (
                    str(body_in.value or "").strip()
                    != str(job.get("body") or "").strip()
                )
            return changed

        def require_saved_editor() -> bool:
            if not editor_has_unsaved_changes():
                return True
            ui.notify(
                "检测到标题、摘要或正文有尚未保存的修改。请先点击“保存并刷新排版预览”，"
                "再进行 AI 评审或定点二次修改，避免覆盖人工编辑。",
                type="warning",
                timeout=10000,
            )
            return False

        build_review_jury_panel(
            service=service,
            batch_id=batch_id,
            job_id=job_id,
            job=job,
            require_saved_editor=require_saved_editor,
            on_job_updated=lambda updated: apply_updated_job(
                updated,
                refresh_images=True,
                refresh_cover=True,
            ),
        )

        def current_paragraphs() -> list[str]:
            return [
                item.strip()
                for item in str(body_in.value or "").replace("\r\n", "\n").split("\n\n")
                if item.strip()
            ]

        def paragraph_options() -> dict[int, str]:
            return {
                index: f"第 {index + 1} 段 · {text[:38]}"
                for index, text in enumerate(current_paragraphs())
            }

        with ui.expansion("AI 二次修改正文（按段）", value=False).classes("w-full"):
            ui.label(
                "选择不满意的段落并说明修改要求。系统只替换这一段，其他正文和已审核图片保持不变。"
            ).classes("muted")
            paragraph_in = ui.select(
                options=paragraph_options(), value=0, label="选择段落"
            ).classes("w-full").props("outlined stack-label options-dense")
            selected_paragraph_preview = ui.textarea(
                "当前段落",
                value=(current_paragraphs()[0] if current_paragraphs() else ""),
            ).classes("w-full").props("outlined readonly rows=4 stack-label")
            paragraph_instruction = ui.textarea(
                "你希望怎样修改这段正文",
                placeholder="例如：压缩到 120 字，突出经营风险；语气更克制，并保留原有数据",
            ).classes("w-full").props("outlined rows=3 stack-label counter maxlength=2000")

            def refresh_selected_paragraph() -> None:
                items = current_paragraphs()
                index = int(paragraph_in.value or 0)
                selected_paragraph_preview.value = (
                    items[index] if 0 <= index < len(items) else ""
                )

            paragraph_in.on_value_change(lambda _: refresh_selected_paragraph())

            def apply_paragraphs(items: list[str], selected: int) -> None:
                body_in.value = "\n\n".join(items)
                options = paragraph_options()
                paragraph_in.set_options(
                    options,
                    value=max(0, min(selected, len(options) - 1)) if options else None,
                )
                refresh_selected_paragraph()

            def move_paragraph(offset: int) -> None:
                items = current_paragraphs()
                index = int(paragraph_in.value or 0)
                target = index + offset
                if 0 <= index < len(items) and 0 <= target < len(items):
                    items[index], items[target] = items[target], items[index]
                    apply_paragraphs(items, target)

            def delete_paragraph() -> None:
                items = current_paragraphs()
                index = int(paragraph_in.value or 0)
                if 0 <= index < len(items):
                    items.pop(index)
                    apply_paragraphs(items, max(0, index - 1))

            async def regenerate_paragraph() -> None:
                if not str(paragraph_instruction.value or "").strip():
                    ui.notify("请先填写这段正文的修改要求", type="warning")
                    return
                if not require_saved_editor():
                    return
                set_button_loading(regenerate_btn, True)
                try:
                    updated = await run.io_bound(
                        lambda: service.regenerate_paragraph(
                            batch_id,
                            job_id,
                            int(paragraph_in.value or 0),
                            instruction=str(paragraph_instruction.value or ""),
                        )
                    )
                    apply_updated_job(updated, refresh_images=False)
                    paragraph_instruction.value = ""
                    ui.notify("所选段落已按要求二次改写，文章需要重新确认", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"段落重新生成失败：{exc}", type="negative", timeout=10000)
                finally:
                    set_button_loading(regenerate_btn, False)

            with ui.row().classes("items-center"):
                ui.button("上移", on_click=lambda: move_paragraph(-1)).props("outline dense no-caps")
                ui.button("下移", on_click=lambda: move_paragraph(1)).props("outline dense no-caps")
                ui.button("删除此段", on_click=delete_paragraph).props(
                    "outline dense color=red-7 no-caps"
                )
                regenerate_btn = ui.button(
                    "按要求二次改写此段", on_click=regenerate_paragraph
                ).props("unelevated dense color=indigo-7 no-caps")
            ui.label(
                "模型会同时参考标题、前后文和原段落；修改前版本会自动保存，可在历史版本中恢复。"
            ).classes("muted")

        inline_assets = list((job.get("meta") or {}).get("inline_images") or [])
        inline_warnings = list(
            (job.get("meta") or {}).get("inline_image_warnings") or []
        )
        with ui.expansion(
            f"正文生图 · 已生成 {len(inline_assets)} 张",
            icon="auto_awesome",
            value=False,
        ).classes("w-full") as inline_expansion:
            ui.label(
                "系统按正文小标题识别论点，将图片插在每个论点最后一个段落之后。"
            ).classes("muted")
            inline_content = ui.column().classes("w-full gap-2")

            def render_inline_assets() -> None:
                assets = list((job.get("meta") or {}).get("inline_images") or [])
                warnings = list(
                    (job.get("meta") or {}).get("inline_image_warnings") or []
                )
                inline_expansion.set_text(f"正文生图 · 已生成 {len(assets)} 张")
                inline_content.clear()
                with inline_content:
                    for warning in warnings:
                        ui.label(f"生图提示：{warning}").classes(
                            "text-warning text-caption"
                        )
                    if not assets:
                        ui.label(
                            "尚未生成智能配图。配置生图智能体后，可在这里对当前文章直接测试。"
                        ).classes("text-warning text-caption q-mt-sm")
                        return
                    with ui.grid(columns=3).classes("w-full gap-3 q-mt-sm"):
                        for asset in assets:
                            with ui.card().classes("w-full q-pa-sm").style(
                                "min-width:0"
                            ):
                                image_index = int(
                                    asset.get("index")
                                    or asset.get("image_index")
                                    or 0
                                )
                                image_url = str(asset.get("url") or "")
                                if image_url:
                                    ui.image(
                                        wechat_image_proxy_url(image_url)
                                    ).classes("w-full rounded-borders").props(
                                        "fit=cover no-spinner"
                                    ).style(
                                        "height:130px;background:#f1f5f3"
                                    )
                                ui.label(
                                    f'论点 {asset.get("index")} · '
                                    f'{asset.get("caption") or "正文配图"}'
                                ).classes(
                                    "text-caption ellipsis w-full"
                                ).tooltip(
                                    str(asset.get("caption") or "正文配图")
                                )
                                if asset.get("model_name"):
                                    ui.label(
                                        f'智能体：{asset.get("model_name")}'
                                    ).classes("muted text-caption")
                                if int(asset.get("revision_count") or 0):
                                    ui.label(
                                        f'已定向修改 {int(asset.get("revision_count") or 0)} 次'
                                    ).classes("text-positive text-caption")
                                image_instruction = ui.textarea(
                                    "这张图的修改要求",
                                    placeholder="例如：不要会议室，改成供应链仓库现场，突出库存积压",
                                ).classes("w-full").props(
                                    "outlined dense rows=2 stack-label counter maxlength=2000"
                                )
                                image_revision_btn = ui.button(
                                    "按要求重新生成此图"
                                ).props(
                                    "unelevated dense color=indigo-7 no-caps icon=auto_fix_high"
                                ).classes("w-full")
                                remove_image_btn = ui.button("移除此图").props(
                                    "flat dense color=red-7 no-caps icon=delete_outline"
                                ).classes("w-full")

                                async def regenerate_one_image(
                                    _=None,
                                    *,
                                    selected_index: int = image_index,
                                    request_field: Any = image_instruction,
                                    action_button: Any = image_revision_btn,
                                ) -> None:
                                    request = str(
                                        request_field.value or ""
                                    ).strip()
                                    if not request:
                                        ui.notify(
                                            "请先填写这张图片的修改要求",
                                            type="warning",
                                        )
                                        return
                                    if not require_saved_editor():
                                        return
                                    set_button_loading(
                                        action_button,
                                        True,
                                        "生图智能体正在只重做这张图片并上传，请稍候…",
                                    )
                                    updated_job: dict[str, Any] | None = None
                                    try:
                                        updated_job = await run.io_bound(
                                            lambda: service.regenerate_inline_image(
                                                batch_id,
                                                job_id,
                                                selected_index,
                                                instruction=request,
                                            )
                                        )
                                        ui.notify(
                                            f"正文配图 {selected_index} 已按要求重新生成，其他图片保持不变",
                                            type="positive",
                                            timeout=10000,
                                        )
                                    except Exception as exc:  # noqa: BLE001
                                        ui.notify(
                                            f"单图重新生成失败，原图片已保留：{exc}",
                                            type="negative",
                                            timeout=15000,
                                        )
                                    finally:
                                        set_button_loading(action_button, False)
                                    if updated_job is not None:
                                        apply_updated_job(
                                            updated_job, refresh_images=True
                                        )

                                async def remove_one_image(
                                    _=None,
                                    *,
                                    selected_index: int = image_index,
                                    action_button: Any = remove_image_btn,
                                ) -> None:
                                    if not require_saved_editor():
                                        return
                                    set_button_loading(action_button, True)
                                    updated_job: dict[str, Any] | None = None
                                    try:
                                        updated_job = await run.io_bound(
                                            lambda: service.remove_inline_image(
                                                batch_id, job_id, selected_index
                                            )
                                        )
                                        ui.notify(
                                            "已移除所选正文配图",
                                            type="positive",
                                        )
                                    except Exception as exc:  # noqa: BLE001
                                        ui.notify(
                                            f"移除失败：{exc}", type="negative"
                                        )
                                    finally:
                                        set_button_loading(action_button, False)
                                    if updated_job is not None:
                                        apply_updated_job(
                                            updated_job, refresh_images=True
                                        )

                                image_revision_btn.on_click(regenerate_one_image)
                                remove_image_btn.on_click(remove_one_image)

            render_inline_assets()

            async def regenerate_inline_images() -> None:
                set_button_loading(
                    inline_image_btn,
                    True,
                    "生图智能体正在按每个论点生成并上传图片，请稍候…",
                )
                try:
                    await run.io_bound(
                        lambda: service.update_job_content(
                            batch_id,
                            job_id,
                            title=str(title_in.value or ""),
                            subtitle=str(subtitle_in.value or ""),
                            digest=str(digest_in.value or ""),
                            body=str(body_in.value or ""),
                        )
                    )
                    updated = await run.io_bound(
                        lambda: service.regenerate_inline_images(batch_id, job_id)
                    )
                    generated = list((updated.get("meta") or {}).get("inline_images") or [])
                    warnings = list(
                        (updated.get("meta") or {}).get("inline_image_warnings") or []
                    )
                    ui.notify(
                        f"已生成并插入 {len(generated)} 张论点配图"
                        + (f"；{len(warnings)} 项提示请检查" if warnings else ""),
                        type="warning" if warnings else "positive",
                        timeout=12000,
                    )
                    apply_updated_job(
                        updated,
                        refresh_images=True,
                        refresh_cover=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"正文生图失败：{exc}", type="negative", timeout=15000)
                finally:
                    set_button_loading(inline_image_btn, False)

            inline_image_btn = ui.button(
                "生成 / 重新生成正文配图",
                on_click=regenerate_inline_images,
            ).props("unelevated color=indigo-7 no-caps icon=auto_awesome").classes(
                "q-mt-sm"
            )
            ui.label(
                "整批重新生成会调用多次生图接口；单张修改只调用一次。这里是按描述重新生成，"
                "不是在原图像素上局部修图。"
            ).classes("muted")

        with ui.expansion(
            "封面主图",
            icon="panorama",
            value=False,
        ).classes("w-full"):
            ui.label(
                "AI 封面同时参考当前标题、正文主题和核心论点，并作为该公众号的永久图片素材上传。"
            ).classes("muted")
            cover_preview_container = ui.column().classes("w-full gap-2")

            def render_cover_preview() -> None:
                cover_meta = dict(
                    (job.get("meta") or {}).get("generated_cover") or {}
                )
                cover_warning = str(
                    (job.get("meta") or {}).get("cover_image_warning") or ""
                )
                generated_cover_active = bool(
                    (job.get("meta") or {}).get("generated_cover_active")
                    and cover_meta
                )
                cover_preview_container.clear()
                with cover_preview_container:
                    if cover_warning:
                        ui.label(cover_warning).classes(
                            "text-warning text-caption"
                        )
                    if not generated_cover_active:
                        return
                    with ui.card().classes("w-full q-pa-sm").style(
                        "max-width:680px"
                    ):
                        preview_url = str(cover_meta.get("url") or "")
                        local_path = Path(
                            str(cover_meta.get("local_path") or "")
                        )
                        preview_source: Any = None
                        if preview_url:
                            preview_source = wechat_image_proxy_url(preview_url)
                        elif local_path.is_file():
                            preview_source = local_path
                        if preview_source is not None:
                            ui.image(preview_source).classes(
                                "w-full rounded-borders"
                            ).props("fit=cover no-spinner").style(
                                "aspect-ratio:2.35/1;background:#f1f5f3"
                            )
                        ui.label(
                            f'当前 AI 封面 · {cover_meta.get("model_name") or "生图智能体"}'
                        ).classes("text-caption text-weight-medium")

            render_cover_preview()

            cover_instruction = ui.textarea(
                "封面修改要求（可留空）",
                value=str((job.get("meta") or {}).get("cover_revision_instruction") or ""),
                placeholder="例如：改成现代制造现场，主体靠中间，不要会议室",
            ).classes("w-full").props(
                "outlined rows=2 stack-label counter maxlength=2000"
            )

            async def regenerate_cover() -> None:
                if (
                    str(body_in.value or "").strip()
                    != str(job.get("body") or "").strip()
                ):
                    ui.notify(
                        "正文有尚未保存的修改。请先保存并刷新排版预览，再重新生成封面。",
                        type="warning",
                        timeout=10000,
                    )
                    return
                set_button_loading(
                    generate_cover_btn,
                    True,
                    "正在根据标题、正文和核心论点生成封面并上传公众号，请稍候…",
                )
                try:
                    update_kwargs: dict[str, Any] = {
                        "title": str(title_in.value or ""),
                        "subtitle": str(subtitle_in.value or ""),
                        "digest": str(digest_in.value or ""),
                    }
                    await run.io_bound(
                        lambda: service.update_job_content(
                            batch_id,
                            job_id,
                            **update_kwargs,
                        )
                    )
                    updated = await run.io_bound(
                        lambda: service.regenerate_cover(
                            batch_id,
                            job_id,
                            instruction=str(cover_instruction.value or ""),
                        )
                    )
                    generated = dict((updated.get("meta") or {}).get("generated_cover") or {})
                    if not generated:
                        warning = str((updated.get("meta") or {}).get("cover_image_warning") or "")
                        raise RuntimeError(warning or "生图智能体没有返回可用封面")
                    apply_updated_job(updated, refresh_cover=True)
                    ui.notify("AI 封面已生成并设为当前封面", type="positive", timeout=10000)
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"封面生成失败：{exc}", type="negative", timeout=15000)
                finally:
                    set_button_loading(generate_cover_btn, False)

            generate_cover_btn = ui.button(
                "生成 / 重新生成封面主图",
                on_click=regenerate_cover,
            ).props("unelevated color=indigo-7 no-caps icon=auto_awesome")
            ui.label(
                "生成会调用一次生图接口并可能产生费用；更换最终标题后建议重新生成。"
            ).classes("muted")

        current_cover = str(job.get("thumb_media_id") or "")
        cover_in = ui.select(
            options=({current_cover: "当前封面"} if current_cover else {}),
            value=current_cover or None,
            label="封面素材",
        ).classes("w-full").props("outlined stack-label options-dense")
        selected_cover_label = ui.label(
            "已选择当前封面" if current_cover else "尚未选择封面"
        ).classes("muted")
        cover_gallery = ui.grid(columns=4).classes("w-full gap-3")
        cover_items: list[dict[str, str]] = []
        cover_page_size = 24

        def select_cover(media_id: str, name: str) -> None:
            cover_in.value = media_id
            selected_cover_label.text = f"已选择：{name}"

        def render_cover_gallery() -> None:
            options = {
                item["media_id"]: item["name"] or f'封面 {index}'
                for index, item in enumerate(cover_items, 1)
            }
            active_cover = str(job.get("thumb_media_id") or "")
            if active_cover and active_cover not in options:
                options = {active_cover: "当前封面", **options}
            cover_in.set_options(
                options,
                value=cover_in.value if cover_in.value in options else None,
            )
            cover_gallery.clear()
            with cover_gallery:
                for index, item in enumerate(cover_items, 1):
                    media_id = str(item["media_id"])
                    name = str(item.get("name") or f"封面 {index}")
                    image_url = str(item.get("url") or "").replace(
                        "http://mmbiz.qpic.cn/", "https://mmbiz.qpic.cn/"
                    )
                    with ui.card().classes("w-full q-pa-sm").style("min-width:0"):
                        if image_url:
                            ui.image(wechat_image_proxy_url(image_url)).classes(
                                "w-full rounded-borders"
                            ).props("fit=cover no-spinner").style(
                                "height:120px;background:#f1f5f3"
                            )
                        else:
                            with ui.element("div").classes(
                                "w-full flex items-center justify-center bg-grey-2 rounded-borders"
                            ).style("height:120px"):
                                ui.icon("broken_image", size="36px").classes("text-grey-6")
                        ui.label(name).classes("text-caption ellipsis w-full").tooltip(name)
                        ui.button(
                            "选择此封面",
                            on_click=lambda _=None, mid=media_id, label=name: select_cover(
                                mid, label
                            ),
                        ).props("flat dense color=teal-9 no-caps icon=check_circle")

        async def load_covers(*, reset: bool) -> None:
            active_button = cover_btn if reset else more_covers_btn
            set_button_loading(active_button, True)
            try:
                start = 0 if reset else len(cover_items)
                page = await run.io_bound(
                    lambda: service.list_cover_options(
                        batch_id,
                        job_id,
                        limit=cover_page_size,
                        offset=start,
                    )
                )
                if reset:
                    cover_items.clear()
                known = {str(item["media_id"]) for item in cover_items}
                cover_items.extend(
                    item for item in page if str(item["media_id"]) not in known
                )
                render_cover_gallery()
                more_covers_btn.set_visibility(len(page) == cover_page_size)
                ui.notify(
                    f"已显示 {len(cover_items)} 张该公众号封面素材", type="positive"
                )
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"读取封面失败：{exc}", type="negative")
            finally:
                set_button_loading(active_button, False)

        async def reload_covers() -> None:
            await load_covers(reset=True)

        async def load_more_covers() -> None:
            await load_covers(reset=False)

        with ui.row().classes("items-center"):
            cover_btn = ui.button(
                "读取该公众号封面素材", on_click=reload_covers
            ).props("outline dense color=teal-9 no-caps icon=image")
            more_covers_btn = ui.button(
                "加载更多封面", on_click=load_more_covers
            ).props("flat dense color=teal-9 no-caps icon=expand_more")
            more_covers_btn.set_visibility(False)

        with ui.expansion("历史版本", value=False).classes("w-full"):
            version_in = ui.select(
                options={},
                value=None,
                label="选择要恢复的版本",
            ).classes("w-full").props("outlined options-dense")

            async def restore_version() -> None:
                if version_in.value is None:
                    ui.notify("当前还没有可恢复的历史版本", type="warning")
                    return
                set_button_loading(restore_btn, True)
                try:
                    updated = await run.io_bound(
                        lambda: service.restore_job_version(
                            batch_id, job_id, int(version_in.value)
                        )
                    )
                    apply_updated_job(
                        updated,
                        refresh_images=True,
                        refresh_cover=True,
                    )
                    ui.notify("已恢复历史版本，文章需要重新确认", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"恢复失败：{exc}", type="negative")
                finally:
                    set_button_loading(restore_btn, False)

            restore_btn = ui.button("恢复此版本", on_click=restore_version).props(
                "outline color=teal-9 no-caps icon=history"
            )

            def refresh_version_options() -> None:
                versions = service.list_job_versions(batch_id, job_id)
                options = {
                    int(item["id"]): (
                        f'{str(item.get("created_at") or "").replace("T", " ")[:19]}'
                        f' · {item.get("reason") or "自动保存"}'
                    )
                    for item in versions
                }
                version_in.set_options(
                    options,
                    value=int(versions[0]["id"]) if versions else None,
                )
                if versions:
                    restore_btn.enable()
                else:
                    restore_btn.disable()

            refresh_version_options()

        with ui.expansion("排版质检与最终 HTML 预览", value=True).classes("w-full"):
            quality_summary = ui.label().classes("muted")
            quality_messages = ui.column().classes("w-full gap-1")
            preview_container = ui.element("div").classes("preview-frame w-full")

            def render_quality_preview() -> None:
                quality = (job.get("meta") or {}).get("layout_quality") or {}
                quality_summary.set_text(
                    f'段落 {quality.get("paragraph_count", 0)} · '
                    f'图片 {quality.get("image_count", 0)}'
                )
                quality_messages.clear()
                with quality_messages:
                    for message in list(quality.get("errors") or []):
                        ui.label(f"错误：{message}").classes("text-negative")
                    for message in list(quality.get("warnings") or []):
                        ui.label(f"提示：{message}").classes("text-warning")
                preview_container.clear()
                with preview_container:
                    if job.get("html_content"):
                        ui.html(
                            prepare_preview_html(str(job["html_content"])),
                            sanitize=False,
                        )
                    else:
                        ui.label(
                            "正文修改后请点击“保存并刷新排版预览”。"
                        ).classes("muted")

            render_quality_preview()

        def apply_updated_job(
            updated: dict[str, Any],
            *,
            refresh_images: bool = False,
            refresh_cover: bool = False,
        ) -> None:
            selected_paragraph = int(paragraph_in.value or 0)
            job.clear()
            job.update(updated)
            title_in.value = clean_candidate_text(
                str(job.get("selected_title") or "")
            )
            subtitle_in.value = clean_candidate_text(
                str(job.get("selected_subtitle") or "")
            )
            digest_in.value = str(job.get("digest") or "")
            body_in.value = str(job.get("body") or "")
            options = paragraph_options()
            paragraph_in.set_options(
                options,
                value=(
                    max(0, min(selected_paragraph, len(options) - 1))
                    if options
                    else None
                ),
            )
            refresh_selected_paragraph()
            active_cover = str(job.get("thumb_media_id") or "")
            if active_cover:
                cover_in.value = active_cover
                selected_cover_label.set_text("已选择当前封面")
            else:
                cover_in.value = None
                selected_cover_label.set_text("尚未选择封面")
            cover_instruction.value = str(
                (job.get("meta") or {}).get("cover_revision_instruction") or ""
            )
            render_cover_gallery()
            if refresh_images:
                render_inline_assets()
            if refresh_cover:
                render_cover_preview()
            render_quality_preview()
            refresh_version_options()

        async def save_and_render() -> None:
            set_button_loading(save_btn, True)
            try:
                await run.io_bound(
                    lambda: service.update_job_content(
                        batch_id,
                        job_id,
                        title=str(title_in.value or ""),
                        subtitle=str(subtitle_in.value or ""),
                        digest=str(digest_in.value or ""),
                        body=str(body_in.value or ""),
                    )
                )
                if cover_in.value:
                    await run.io_bound(
                        lambda: service.select_job_cover(
                            batch_id, job_id, str(cover_in.value)
                        )
                    )
                updated = await run.io_bound(
                    lambda: service.rerender_job(batch_id, job_id)
                )
                apply_updated_job(
                    updated,
                    refresh_images=True,
                    refresh_cover=True,
                )
                ui.notify("修改已保存并重新排版", type="positive")
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"保存失败：{exc}", type="negative", timeout=10000)
            finally:
                set_button_loading(save_btn, False)

        async def confirm() -> None:
            set_button_loading(confirm_btn, True)
            try:
                await run.io_bound(
                    lambda: service.update_job_content(
                        batch_id,
                        job_id,
                        title=str(title_in.value or ""),
                        subtitle=str(subtitle_in.value or ""),
                        digest=str(digest_in.value or ""),
                        body=str(body_in.value or ""),
                    )
                )
                if cover_in.value:
                    await run.io_bound(
                        lambda: service.select_job_cover(
                            batch_id, job_id, str(cover_in.value)
                        )
                    )
                await run.io_bound(lambda: service.rerender_job(batch_id, job_id))
                await run.io_bound(lambda: service.confirm_job(batch_id, job_id))
                latest = await run.io_bound(
                    lambda: service.get_batch(batch_id, include_content=False)
                )
                following = next_review_job(
                    list(latest.get("jobs") or []), current_job_id=job_id
                )
                if following is None and review_runtime is not None:
                    review_runtime["review_open"] = False
                dialog.close()
                on_change()
                if following is not None:
                    ui.notify(
                        f'已确认，继续审核 {following.get("account_name") or "下一篇"}',
                        type="positive",
                    )
                    client_timer(
                        0.05,
                        lambda: open_review_workbench(
                            state,
                            service,
                            batch_id,
                            int(following["id"]),
                            on_change,
                            review_runtime=review_runtime,
                        ),
                        once=True,
                    )
                else:
                    ui.notify("全部文章已确认，可以写入草稿箱", type="positive")
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"确认失败：{exc}", type="negative", timeout=10000)
            finally:
                set_button_loading(confirm_btn, False)

        def needs_changes() -> None:
            service.request_job_changes(batch_id, job_id)
            ui.notify("已标记为需要修改", type="warning")
            if review_runtime is not None:
                review_runtime["review_open"] = False
            dialog.close()
            on_change()

        following_at_open = next_review_job(
            list(batch.get("jobs") or []), current_job_id=job_id
        )
        with ui.row().classes("review-action-bar w-full justify-end q-mt-md"):
            with ui.button("更多", icon="more_horiz").props(
                "flat color=grey-8 no-caps"
            ):
                with ui.menu():
                    ui.menu_item("标记为需要修改", on_click=needs_changes)
            save_btn = ui.button("保存文章修改", on_click=save_and_render).props(
                "outline color=teal-9 no-caps"
            )
            confirm_btn = ui.button(
                "确认此文章并继续下一篇"
                if following_at_open is not None
                else "确认此文章",
                on_click=confirm,
            ).props(
                "unelevated color=teal-9 no-caps icon=check"
            )
    if review_runtime is not None:
        dialog.on_value_change(
            lambda event: review_runtime.__setitem__(
                "review_open", bool(event.value)
            )
        )
    dialog.open()


def _render_batch_card(
    state: AppState,
    service: BatchService,
    batch: dict[str, Any],
    refresh: Callable[[], None],
    *,
    review_runtime: dict[str, bool] | None = None,
    focused: bool = False,
    auto_expand: bool = False,
) -> Any:
    progress = batch.get("progress") or {}
    jobs = list(batch.get("jobs") or [])
    topic = str(batch.get("topic") or "").strip() or _batch_topic(jobs)
    with ui.expansion(value=focused or auto_expand).classes("card w-full") as expansion:
        with expansion.add_slot("header"):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.column().classes("gap-0").style("min-width:0;flex:1"):
                    ui.label(f'批次 #{batch["display_id"]}').classes("text-weight-bold")
                    ui.label(topic or "未命名批次").classes("text-weight-medium")
                    ui.label(
                        f'公众号 {len(jobs)} 个 · 已审核 {progress.get("reviewed", 0)}/'
                        f'{progress.get("review_total", 0)} · '
                        f'已写入草稿箱 {progress.get("drafted", 0)} · 失败 {progress.get("failed", 0)}'
                    ).classes("muted")
                ui.badge(_batch_status_text(batch)).props(
                    f'color={_batch_color(str(batch.get("status") or ""))}'
                )

        ui.label(
            f'创建：{_format_time(batch.get("created_at"))} · '
            f'更新：{_format_time(batch.get("updated_at"))} · '
            f'耗时：{_duration(batch.get("created_at"), batch.get("updated_at"))}'
        ).classes("muted")
        if batch.get("source_url"):
            ui.link("查看来源链接", str(batch["source_url"]), new_tab=True).classes("text-teal-9")

        for job in jobs:
            review_status = str(job.get("review_status") or "unviewed")
            with ui.row().classes("w-full items-center justify-between job-row q-pa-sm"):
                with ui.column().classes("gap-0").style("min-width:0;flex:1"):
                    ui.label(str(job.get("account_name") or "公众号")).classes("text-weight-medium")
                    ui.label(
                        str(job.get("selected_title") or "尚未选择标题")
                    ).classes("muted")
                    if job.get("error"):
                        with ui.row().classes("items-center gap-1"):
                            ui.label(
                                _friendly_error(str(job["error"]))
                            ).classes("text-negative")
                            ui.button(
                                "复制错误",
                                on_click=lambda _=None, error=str(job["error"]): ui.clipboard.write(
                                    error
                                ),
                            ).props(
                                "flat dense color=red-7 no-caps icon=content_copy"
                            )
                if job.get("status") == "ready_for_review":
                    ui.badge(REVIEW_LABELS.get(review_status, review_status)).props(
                        f'color={REVIEW_COLORS.get(review_status, "grey-7")}'
                    )
                    ui.button(
                        "打开审核",
                        on_click=lambda _=None, jid=int(job["id"]): open_review_workbench(
                            state,
                            service,
                            str(batch["id"]),
                            jid,
                            refresh,
                            review_runtime=review_runtime,
                        ),
                    ).props("outline dense color=teal-9 no-caps")
                else:
                    status = str(job.get("status") or "")
                    ui.badge(STATUS_LABEL.get(status, status)).props(
                        f'color={_job_color(status)}'
                    )

        with ui.row().classes("w-full items-center justify-between q-mt-sm"):
            unconfirmed = int(progress.get("unconfirmed") or 0)
            ready_count = int(progress.get("ready_for_review") or 0)
            failed_count = int(progress.get("failed") or 0)
            if unconfirmed:
                review_message = (
                    f'已审核 {progress.get("reviewed", 0)}/{progress.get("review_total", 0)}，'
                    f"尚有 {unconfirmed} 篇未确认"
                )
                review_class = "text-warning"
            elif ready_count:
                review_message = f"{ready_count} 篇已确认，可以写入草稿箱"
                review_class = "text-positive"
            elif failed_count:
                review_message = "当前没有可写入文章，请先重试失败任务"
                review_class = "text-negative"
            else:
                review_message = "本批次已处理完成"
                review_class = "text-positive"
            ui.label(review_message).classes(review_class)
            with ui.row().classes("items-center"):
                if failed_count > 0:
                    ui.button("仅重试失败公众号", on_click=lambda: _run_action(
                        lambda: service.retry_failed(str(batch["id"])), refresh, "已创建失败重试批次"
                    )).props("outline dense color=orange-8 no-caps")
                ui.button("按原设置重新生成", on_click=lambda: _run_action(
                    lambda: service.copy_batch(str(batch["id"])), refresh, "已复制并开始新批次"
                )).props("flat dense color=teal-9 no-caps")
                if str(batch.get("status")) in {"processing", "pending", "injecting"}:
                    ui.button("停止生成", on_click=lambda: _run_action(
                        lambda: service.cancel_batch(str(batch["id"])), refresh, "已请求停止生成"
                    )).props("flat dense color=grey-8 no-caps")
                pending_job = next_review_job(jobs)
                if unconfirmed and pending_job is not None:
                    ui.button(
                        f"审核下一篇（剩余 {unconfirmed} 篇）",
                        on_click=lambda _=None, jid=int(pending_job["id"]): open_review_workbench(
                            state,
                            service,
                            str(batch["id"]),
                            jid,
                            refresh,
                            review_runtime=review_runtime,
                        ),
                    ).props("unelevated dense color=teal-9 no-caps icon=rate_review")
                else:
                    write_btn = ui.button(
                        f"写入已确认的 {ready_count} 篇",
                        on_click=lambda: confirm_batch_write(service, batch, refresh),
                    ).props("unelevated dense color=teal-9 no-caps")
                    if not ready_count:
                        write_btn.disable()
                ui.button("归档", on_click=lambda: _run_action(
                    lambda: service.archive_batch(str(batch["id"])), refresh, "批次已归档"
                )).props("flat dense color=grey-7 no-caps")
    return expansion


def confirm_batch_write(
    service: BatchService, batch: dict[str, Any], refresh: Callable[[], None]
) -> None:
    names = [
        str(job.get("account_name") or "")
        for job in batch.get("jobs") or []
        if job.get("status") == "ready_for_review"
        and job.get("review_status") == "confirmed"
    ]
    with ui.dialog() as dialog, ui.card():
        ui.label(f"确认写入 {len(names)} 篇文章？").classes("text-h6 text-weight-bold")
        ui.label(f"将写入 {len(names)} 个公众号：{'、'.join(names)}")
        ui.label("仅写入草稿箱，不会直接群发。").classes("text-warning")

        async def submit() -> None:
            set_button_loading(
                button,
                True,
                f"正在同时写入 {len(names)} 个公众号草稿箱，请稍候…",
            )
            try:
                result = await run.io_bound(
                    lambda: service.inject_batch(str(batch["id"]))
                )
                result_jobs = list(result.get("jobs") or [])
                written = sum(
                    1
                    for job in result_jobs
                    if str(job.get("status") or "") in {"drafted", "published"}
                )
                failed = sum(
                    1 for job in result_jobs if str(job.get("status") or "") == "failed"
                )
                if failed:
                    ui.notify(
                        f"草稿写入完成：成功 {written} 篇，失败 {failed} 篇，可在本批次重试",
                        type="warning",
                        timeout=12000,
                    )
                else:
                    ui.notify(f"已写入 {written} 个公众号草稿箱", type="positive")
                dialog.close()
                refresh()
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"写入失败：{exc}", type="negative", timeout=10000)
            finally:
                set_button_loading(button, False)

        with ui.row().classes("w-full justify-end"):
            ui.button("取消", on_click=dialog.close).props("flat no-caps")
            button = ui.button("确认写入", on_click=submit).props(
                "unelevated color=teal-9 no-caps"
            )
    dialog.open()


def _run_action(action: Callable[[], Any], refresh: Callable[[], None], message: str) -> None:
    try:
        action()
        ui.notify(message, type="positive")
        refresh()
    except Exception as exc:  # noqa: BLE001
        ui.notify(str(exc), type="negative", timeout=10000)


def _matches_filters(
    batch: dict[str, Any], *, search: str, status: str, account_id: str, today: bool
) -> bool:
    jobs = list(batch.get("jobs") or [])
    needle = search.strip().casefold()
    haystack = " ".join(
        [str(batch.get("topic") or ""), str(batch.get("source_url") or "")]
        + [
            f'{job.get("account_name", "")} {job.get("selected_title", "")}'
            for job in jobs
        ]
    ).casefold()
    if needle and needle not in haystack:
        return False
    if account_id and not any(str(job.get("account_id")) == account_id for job in jobs):
        return False
    if today and not str(batch.get("created_at") or "").startswith(datetime.now().date().isoformat()):
        return False
    if status == "attention":
        progress = batch.get("progress") or {}
        return bool(progress.get("unconfirmed") or progress.get("failed"))
    if status and str(batch.get("status") or "") != status:
        return False
    return True


def _batch_topic(jobs: list[dict[str, Any]]) -> str:
    return str(next((job.get("selected_title") or "" for job in jobs if job.get("selected_title")), ""))


def _batch_status_text(batch: dict[str, Any]) -> str:
    status = str(batch.get("status") or "")
    progress = batch.get("progress") or {}
    if progress.get("drafted") and progress.get("failed"):
        return "部分成功"
    return {
        "pending": "等待中",
        "processing": "正在生成",
        "ready_for_review": "待审核",
        "injecting": "写入中",
        "drafted": "已写入草稿箱",
        "partial_failed": "部分失败",
        "failed": "失败",
        "cancelled": "已停止",
    }.get(status, status)


def _batch_color(status: str) -> str:
    return {
        "pending": "grey-7",
        "processing": "blue-7",
        "ready_for_review": "orange-8",
        "injecting": "blue-7",
        "drafted": "green-7",
        "partial_failed": "orange-8",
        "failed": "red-7",
        "cancelled": "grey-7",
    }.get(status, "grey-7")


def _job_color(status: str) -> str:
    return {
        "pending": "grey-7",
        "ingesting": "blue-7",
        "rewriting": "blue-7",
        "title_optimizing": "blue-7",
        "rendering": "blue-7",
        "injecting": "blue-7",
        "drafted": "green-7",
        "published": "green-7",
        "failed": "red-7",
        "cancelled": "grey-7",
    }.get(status, "grey-7")


def _format_time(value: Any) -> str:
    text = str(value or "")
    return text.replace("T", " ")[:19] if text else "-"


def _duration(start: Any, end: Any) -> str:
    try:
        seconds = max(
            0,
            int((datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds()),
        )
        return f"{seconds // 60}分{seconds % 60:02d}秒" if seconds >= 60 else f"{seconds}秒"
    except (TypeError, ValueError):
        return "-"


def _friendly_error(message: str) -> str:
    lower = message.lower()
    if "40125" in message or "invalid appsecret" in lower:
        return "公众号 AppSecret 无效，请到“设置 → 公众号”更新凭证"
    if "10054" in message:
        return "微信服务器临时断开连接，系统已重试；可点击“仅重试失败公众号”"
    if "429" in message or "overload" in lower or "过载" in message:
        return "模型服务繁忙，请稍后重试或更换模型"
    return message
