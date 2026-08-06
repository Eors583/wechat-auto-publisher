from __future__ import annotations

import json
import math
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from nicegui import run, ui

from app.config import load_config
from app.editorial_review import REVIEW_ROLES
from app.services.batches import BatchService
from app.ui.lifecycle import client_timer
from app.ui.state import set_button_loading

SEVERITY_LABELS = {
    "high": "高优先级",
    "medium": "中优先级",
    "low": "低优先级",
}

SEVERITY_COLORS = {
    "high": "red-7",
    "medium": "orange-8",
    "low": "blue-grey-6",
}

RESOLUTION_LABELS = {
    "open": "待核实",
    "resolved": "已核实",
    "waived": "已接受风险",
}


_MISSING_DIMENSION_SCORE_SUMMARIES = {
    "本次评审未单独返回该项判断。",
    "本次评审未单独返回该项判断",
}


def _format_dimension_score(
    value: Any,
    *,
    summary: Any = "",
    score_available: Any = None,
) -> str:
    """Keep a missing model score distinct from an explicit numeric zero."""

    if score_available is False:
        return "—"
    if score_available is not True and str(summary or "").strip() in (
        _MISSING_DIMENSION_SCORE_SUMMARIES
    ):
        return "—"
    if value is None or isinstance(value, bool):
        return "—"
    if isinstance(value, str) and not value.strip():
        return "—"
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(score):
        return "—"
    return str(int(score))


def _comparison_plain_text(value: Any) -> str:
    """Hide authoring markers while keeping comparison text easy to read."""

    text = str(value or "").strip()
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    text = re.sub(r"\[([^\]\n]+)\]\([^)\n]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    return text.strip()


def _add_accessible_removal_chips(select: Any, *, item_name: str) -> None:
    """Give each Quasar chip removal control a specific Chinese name."""

    select.add_slot(
        "selected-item",
        f"""
        <q-chip
          dense
          removable
          :selected="props.selected"
          :tabindex="props.tabindex"
          :remove-aria-label="'移除{item_name}：' + props.opt.label"
          @remove="props.removeAtIndex(props.index)"
        ><span class="ellipsis">{{{{ props.opt.label }}}}</span></q-chip>
        """,
    )


def _review_start_action(review: dict[str, Any] | None) -> tuple[str, bool, bool]:
    """Return label, disabled state and whether starting needs confirmation."""

    if not review:
        return "开始 AI 评审", False, False
    if str(review.get("status") or "") in {"running", "rewriting"}:
        return "AI 评审中", True, False
    return "重新评审", False, True


def editorial_review_progress(
    review: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Map persisted review state to one shared UI progress contract.

    Model providers do not expose token-by-token progress for this operation, so
    an active review advances through conservative, time-based stages and never
    reaches 100% until the persisted state is terminal.  Both the workbench and
    the background activity dock consume this contract to avoid contradictory
    labels or percentages.
    """

    current = dict(review or {})
    status = str(current.get("status") or "")
    created_at = str(current.get("created_at") or "")
    elapsed_seconds = 0
    if created_at:
        try:
            started = datetime.fromisoformat(created_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            clock = now or datetime.now(UTC)
            if clock.tzinfo is None:
                clock = clock.replace(tzinfo=UTC)
            elapsed_seconds = max(0, int((clock - started).total_seconds()))
        except (TypeError, ValueError):
            elapsed_seconds = 0

    if status == "running":
        if elapsed_seconds < 5:
            value, stage = 0.12, "正在创建评审任务"
        elif elapsed_seconds < 20:
            value, stage = 0.28, "正在整理文章与评审规则"
        elif elapsed_seconds < 50:
            value, stage = 0.48, "正在评估标题、开头与完读潜力"
        elif elapsed_seconds < 90:
            value, stage = 0.68, "正在汇总各评审角色意见"
        else:
            value, stage = 0.88, "模型仍在处理，正在等待完整结果"
        return {
            "status": status,
            "value": value,
            "percent": f"{round(value * 100)}%",
            "title": "AI 评审进行中",
            "stage": stage,
            "elapsed_seconds": elapsed_seconds,
            "active": True,
            "failed": False,
        }

    if status == "rewriting":
        return {
            "status": status,
            "value": 0.76,
            "percent": "76%",
            "title": "AI 正在生成修改稿",
            "stage": "正在按已勾选意见改写并重新排版",
            "elapsed_seconds": elapsed_seconds,
            "active": True,
            "failed": False,
        }

    terminal_titles = {
        "completed": "AI 评审已完成",
        "candidate_ready": "AI 修改稿已生成，等待选择版本",
        "applied": "AI 修改稿已应用",
        "source_kept": "已选择保留原文",
    }
    if status in terminal_titles:
        return {
            "status": status,
            "value": 1.0,
            "percent": "100%",
            "title": terminal_titles[status],
            "stage": "评审结论和改进意见已生成",
            "elapsed_seconds": elapsed_seconds,
            "active": False,
            "failed": False,
        }

    failed = status in {"failed", "stale"}
    return {
        "status": status,
        "value": 1.0 if failed else 0.0,
        "percent": "100%" if failed else "0%",
        "title": "AI 评审失败" if status == "failed" else "尚未开始 AI 评审",
        "stage": str(current.get("error") or "尚未生成评审结果"),
        "elapsed_seconds": elapsed_seconds,
        "active": False,
        "failed": failed,
    }


def _issue_can_auto_apply(issue: dict[str, Any]) -> bool:
    """Keep old reviews usable after auto-edit permission became role-owned."""
    role_id = str(issue.get("role_id") or "")
    return (
        role_id not in {"fact_checker", "compliance_expert"}
        and not bool(issue.get("blocks_draft"))
        and (
            bool(issue.get("can_auto_apply"))
            or bool((REVIEW_ROLES.get(role_id) or {}).get("may_rewrite"))
        )
    )


def build_review_jury_panel(
    *,
    service: BatchService,
    batch_id: str,
    job_id: int,
    job: dict[str, Any],
    require_saved_editor: Callable[[], bool],
    on_job_updated: Callable[[dict[str, Any]], None],
    on_article_updated: Callable[[], Awaitable[None]] | None = None,
    is_workbench_alive: Callable[[], bool] | None = None,
    on_background_review: Callable[..., bool] | None = None,
    on_enter_background: Callable[[], None] | None = None,
    on_review_updated: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Render the manual AI editorial jury inside the shared review workbench.

    The component never closes or rebuilds the parent workbench. Applying a
    candidate delegates the returned job to ``on_job_updated`` so the existing
    title/body/image/cover/preview controls can update in place.
    """

    owner_client = ui.context.client
    result_anchor_id = f"editorial-review-result-{job_id}"
    settings_anchor_id = f"editorial-review-settings-{job_id}"
    comparison_anchor_id = f"editorial-review-comparison-{job_id}"

    def ui_alive() -> bool:
        if bool(getattr(owner_client, "is_deleted", False)):
            return False
        if is_workbench_alive is None:
            return True
        try:
            return bool(is_workbench_alive())
        except RuntimeError:
            return False

    def scroll_dom_target(target_id: str) -> None:
        """Scroll after Vue applies visibility changes, without a UI timer."""

        if not ui_alive():
            return
        target_literal = json.dumps(target_id)
        try:
            owner_client.run_javascript(
                "(() => {"
                f"const targetId={target_literal};"
                "const scroll=()=>{"
                "const target=document.getElementById(targetId);"
                "if(!target){return false;}"
                "target.scrollIntoView({behavior:'auto',block:'start',"
                "inline:'nearest'});"
                "return true;"
                "};"
                "requestAnimationFrame(()=>requestAnimationFrame(scroll));"
                "setTimeout(scroll,80);"
                "setTimeout(scroll,180);"
                "})();"
            )
        except RuntimeError:
            return

    def notify_review_updated(review: dict[str, Any]) -> None:
        if on_review_updated is not None:
            on_review_updated(dict(review))

    options = service.get_editorial_review_options()
    profiles = [
        item
        for item in service.list_editorial_review_profiles(include_builtin=True)
        if bool(item.get("builtin")) or bool(item.get("enabled"))
    ]
    profile_map = {str(item["id"]): item for item in profiles}
    profile_options = {
        str(item["id"]): (
            f'{item["name"]}（内置）' if item.get("builtin") else str(item["name"])
        )
        for item in profiles
    }
    role_options = {
        str(item["id"]): str(item["name"]) for item in options.get("roles") or []
    }
    style_options = {
        str(item["id"]): str(item["name"]) for item in options.get("styles") or []
    }
    strictness_options = {
        str(item["id"]): str(item["name"])
        for item in options.get("strictness_levels") or []
    }
    account_id = str(
        job.get("account_id")
        or (job.get("meta") or {}).get("official_account_id")
        or ""
    )
    try:
        account_default = service.get_account_editorial_review_default(account_id)
    except Exception:  # noqa: BLE001 - legacy/config.yaml accounts have no DB binding
        account_default = {}
    initial_profile_id = str(account_default.get("profile_id") or "")
    if initial_profile_id not in profile_map:
        initial_profile_id = next(iter(profile_options), "")
    initial_config = dict(
        (account_default.get("config") or {})
        or (profile_map.get(initial_profile_id) or {}).get("config")
        or {}
    )
    latest_reviews = service.list_editorial_reviews(job_id=job_id, limit=1)
    runtime: dict[str, Any] = {
        "review": latest_reviews[0] if latest_reviews else None,
        "application": None,
        "selected_issue_ids": set(),
        "rewrite_running": False,
        "rewrite_in_background": False,
        "version_choice_running": False,
    }

    review_expansion = ui.expansion(
        "AI 评审团（手动触发）",
        icon="groups",
        value=False,
    ).classes("review-jury w-full").props(
        "header-class=review-jury__header"
    )
    with review_expansion:
        with ui.card().classes("review-jury-intro w-full").props("flat"):
            with ui.row().classes("w-full items-start no-wrap q-gutter-md"):
                with ui.row().classes(
                    "review-jury-intro__icon items-center justify-center"
                ):
                    ui.icon("auto_awesome", size="22px")
                with ui.column().classes("gap-1"):
                    ui.label("先评审，再决定是否改写").classes(
                        "text-subtitle2 text-weight-bold"
                    )
                    ui.label(
                        "AI 从整篇运营效果出发，重点评估标题、开头、完读潜力、"
                        "点赞潜力和转发潜力，不做逐句挑字；只有点击后才会运行，"
                        "候选稿也不会自动覆盖当前文章。"
                    ).classes("muted")
                    ui.label(
                        "事实与合规底线 ＞ 公众号品牌规则 ＞ 用户选择的目标风格"
                    ).classes("text-warning text-caption text-weight-medium")

        result_section = ui.column().classes(
            "editorial-review-result-anchor w-full gap-3 q-mt-md"
        )
        with result_section:
            result_card = ui.card().classes(
                "review-surface w-full"
            ).props(f"flat id={result_anchor_id}")
            with result_card:
                result_host = ui.column().classes("w-full gap-3")
                comparison_section = ui.column().classes(
                    "review-comparison w-full gap-3 q-mt-md"
                ).props(f"id={comparison_anchor_id}")
                with comparison_section:
                    comparison_host = ui.column().classes("w-full gap-3")
                comparison_section.set_visibility(False)
        result_section.set_visibility(False)
        rewrite_progress_host = ui.column().classes("w-full q-mt-sm")
        result_summary_host = ui.column().classes("w-full gap-2 q-mt-sm")
        start_action_host = ui.column().classes("w-full items-start q-mt-sm")

        def settings_summary_text(
            profile_id: str,
            strictness: str,
            role_ids: list[str],
        ) -> str:
            profile_name = str(
                (profile_map.get(profile_id) or {}).get("name")
                or profile_options.get(profile_id)
                or "默认评审方案"
            ).replace("（内置）", "")
            strictness_name = strictness_options.get(strictness, strictness)
            return f"{profile_name} · {strictness_name} · {len(role_ids)} 个角色"

        with ui.column().classes(
            "editorial-review-settings-anchor w-full gap-1 q-mt-sm"
        ).props(f"id={settings_anchor_id}"):
            settings_summary_label = ui.label(
                settings_summary_text(
                    initial_profile_id,
                    str(initial_config.get("strictness") or "standard"),
                    list(initial_config.get("role_ids") or []),
                )
            ).classes("muted text-caption")
            settings_expansion = ui.expansion(
                "调整本次评审设置",
                icon="tune",
                value=False,
            ).classes("review-settings w-full").props(
                "dense header-class=text-blue-grey-8"
            )
            with settings_expansion:
                profile_in = ui.select(
                    profile_options,
                    value=initial_profile_id or None,
                    label="评审方案",
                ).classes("w-full q-mt-sm").props(
                    "outlined dense stack-label options-dense"
                )
                with ui.grid(columns=2).classes("w-full gap-3"):
                    roles_in = ui.select(
                        role_options,
                        value=list(initial_config.get("role_ids") or []),
                        label="评审角色（可多选）",
                        multiple=True,
                    ).classes("w-full").props(
                        "outlined dense stack-label options-dense use-chips"
                    )
                    _add_accessible_removal_chips(
                        roles_in,
                        item_name="评审角色",
                    )
                    styles_in = ui.select(
                        style_options,
                        value=list(initial_config.get("style_ids") or []),
                        label="目标风格（可多选）",
                        multiple=True,
                    ).classes("w-full").props(
                        "outlined dense stack-label options-dense use-chips"
                    )
                    _add_accessible_removal_chips(
                        styles_in,
                        item_name="目标风格",
                    )
                strictness_in = ui.toggle(
                    strictness_options,
                    value=str(initial_config.get("strictness") or "standard"),
                ).classes("q-mt-xs")

                with ui.expansion(
                    "自定义本次评审规则",
                    icon="settings_suggest",
                    value=False,
                ).classes("w-full"):
                    focus_in = ui.textarea(
                        "评审重点",
                        value=str(initial_config.get("focus") or ""),
                        placeholder="例如：强化标题点击力、开头留存和转发价值，保留专业克制的品牌语气",
                    ).classes("w-full").props(
                        "outlined rows=3 stack-label maxlength=4000 counter"
                    )
                    audience_in = ui.input(
                        "期望读者",
                        value=str(initial_config.get("target_audience") or ""),
                        placeholder="例如：企业经营者、中高层管理者",
                    ).classes("w-full").props(
                        "outlined stack-label maxlength=1000"
                    )
                    with ui.grid(columns=2).classes("w-full gap-3"):
                        required_in = ui.textarea(
                            "必须检查项（每行一项）",
                            value=_join_lines(initial_config.get("required_checks")),
                        ).classes("w-full").props("outlined rows=4 stack-label")
                        ignored_in = ui.textarea(
                            "忽略项（每行一项）",
                            value=_join_lines(initial_config.get("ignored_items")),
                        ).classes("w-full").props("outlined rows=4 stack-label")
                        banned_in = ui.textarea(
                            "禁用表达（每行一项）",
                            value=_join_lines(initial_config.get("banned_expressions")),
                        ).classes("w-full").props("outlined rows=4 stack-label")
                        must_keep_in = ui.textarea(
                            "必须保留内容（每行一项）",
                            value=_join_lines(initial_config.get("must_keep")),
                        ).classes("w-full").props("outlined rows=4 stack-label")
                    advanced_in = ui.textarea(
                        "高级业务评审规则",
                        value=str(initial_config.get("advanced_rules") or ""),
                        placeholder=(
                            "只填写业务规则。JSON 输出、评分字段和阶段协议由系统管理，"
                            "不能在这里修改。"
                        ),
                    ).classes("w-full").props(
                        "outlined rows=5 stack-label maxlength=8000 counter"
                    )

        def current_settings_summary() -> str:
            return settings_summary_text(
                str(profile_in.value or ""),
                str(strictness_in.value or "standard"),
                list(roles_in.value or []),
            )

        def refresh_settings_summary() -> None:
            settings_summary_label.set_text(current_settings_summary())

        def load_profile_config(profile_id: str) -> None:
            config = dict((profile_map.get(str(profile_id)) or {}).get("config") or {})
            if not config:
                return
            roles_in.value = list(config.get("role_ids") or [])
            styles_in.value = list(config.get("style_ids") or [])
            strictness_in.value = str(config.get("strictness") or "standard")
            focus_in.value = str(config.get("focus") or "")
            audience_in.value = str(config.get("target_audience") or "")
            required_in.value = _join_lines(config.get("required_checks"))
            ignored_in.value = _join_lines(config.get("ignored_items"))
            banned_in.value = _join_lines(config.get("banned_expressions"))
            must_keep_in.value = _join_lines(config.get("must_keep"))
            advanced_in.value = str(config.get("advanced_rules") or "")
            refresh_settings_summary()

        profile_in.on_value_change(
            lambda event: load_profile_config(str(event.value or ""))
        )
        roles_in.on_value_change(lambda _: refresh_settings_summary())
        strictness_in.on_value_change(lambda _: refresh_settings_summary())

        def collect_config() -> dict[str, Any]:
            profile = profile_map.get(str(profile_in.value or "")) or {}
            config = dict(profile.get("config") or {})
            config.update(
                {
                    "role_ids": list(roles_in.value or []),
                    "style_ids": list(styles_in.value or []),
                    "strictness": str(strictness_in.value or "standard"),
                    "focus": str(focus_in.value or ""),
                    "target_audience": str(audience_in.value or ""),
                    "required_checks": _split_lines(required_in.value),
                    "ignored_items": _split_lines(ignored_in.value),
                    "banned_expressions": _split_lines(banned_in.value),
                    "must_keep": _split_lines(must_keep_in.value),
                    "advanced_rules": str(advanced_in.value or ""),
                }
            )
            return config

        async def scroll_to_review_result() -> None:
            """Reveal the inline conclusion and keep the current workbench open."""

            if not ui_alive():
                return
            review_expansion.value = True
            settings_expansion.value = False
            result_section.set_visibility(True)

            scroll_dom_target(result_anchor_id)

        async def scroll_to_review_settings() -> None:
            """Expand only the settings requested by the operator and focus them."""

            if not ui_alive():
                return
            review_expansion.value = True
            settings_expansion.value = True

            scroll_dom_target(settings_anchor_id)

        async def scroll_to_article_comparison() -> None:
            """Reveal and scroll to the stable before/after comparison anchor."""

            if not ui_alive() or not bool(comparison_section.visible):
                return
            review_expansion.value = True
            result_section.set_visibility(True)
            comparison_section.set_visibility(True)

            scroll_dom_target(comparison_anchor_id)

        def render_article_comparison(
            review: dict[str, Any] | None,
            application: dict[str, Any] | None,
        ) -> bool:
            """Render the persisted review source and AI candidate side by side."""

            comparison_host.clear()
            comparison_section.set_visibility(False)
            review_status = str((review or {}).get("status") or "")
            if not review or review_status not in {
                "candidate_ready",
                "applied",
                "source_kept",
            }:
                return False

            before = dict(review.get("source_snapshot") or {})
            # The review snapshot is the canonical candidate for historical
            # reopen. The application snapshot keeps compatibility with older
            # rows and the just-generated in-memory application.
            after = dict(review.get("rewritten_snapshot") or {})
            if not after:
                after = dict((application or {}).get("candidate_snapshot") or {})
            risk_warnings = [
                dict(item)
                for item in (after.get("risk_warnings") or [])
                if isinstance(item, dict)
            ]
            if not str(before.get("body") or "").strip() or not str(
                after.get("body") or ""
            ).strip():
                return False
            def snapshot_matches_editor(snapshot: dict[str, Any]) -> bool:
                return all(
                    str(job.get(job_key) or "").strip()
                    == str(snapshot.get(snapshot_key) or "").strip()
                    for job_key, snapshot_key in (
                        ("selected_title", "title"),
                        ("selected_subtitle", "subtitle"),
                        ("digest", "digest"),
                        ("body", "body"),
                    )
                )

            source_matches_editor = snapshot_matches_editor(before)
            comparison_matches_editor = snapshot_matches_editor(after)

            async def select_version(
                *,
                use_rewrite: bool,
                button: Any,
                other_button: Any,
            ) -> None:
                if bool(runtime.get("version_choice_running")):
                    ui.notify("正在保存版本选择，请勿重复提交", type="warning")
                    return
                application_id = str((application or {}).get("id") or "")
                if not application_id:
                    ui.notify("未找到可选择的 AI 修改稿，请重新生成", type="negative")
                    return
                runtime["version_choice_running"] = True
                other_button.disable()
                set_button_loading(
                    button,
                    True,
                    (
                        "正在采用 AI 改写稿并重新排版，请稍候…"
                        if use_rewrite
                        else "正在保存“保留改写前原文”的选择…"
                    ),
                )
                updated: dict[str, Any] | None = None
                refreshed_review: dict[str, Any] | None = None
                refreshed_application: dict[str, Any] | None = None
                try:
                    if use_rewrite:
                        updated = await run.io_bound(
                            lambda: service.apply_editorial_review_application(
                                batch_id,
                                job_id,
                                application_id,
                            )
                        )
                    else:
                        updated = await run.io_bound(
                            lambda: service.keep_editorial_review_source(
                                batch_id,
                                job_id,
                                application_id,
                            )
                        )
                    refreshed_review = await run.io_bound(
                        lambda: service.get_editorial_review(str(review["id"]))
                    )
                    refreshed_application = await run.io_bound(
                        lambda: service.get_editorial_review_application(
                            application_id
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    if ui_alive():
                        ui.notify(
                            f"保存版本选择失败：{exc}",
                            type="negative",
                            timeout=12000,
                        )
                finally:
                    runtime["version_choice_running"] = False
                    if ui_alive():
                        set_button_loading(button, False)
                        other_button.enable()
                if (
                    updated is None
                    or refreshed_review is None
                    or refreshed_application is None
                    or not ui_alive()
                ):
                    return
                on_job_updated(updated)
                runtime["review"] = refreshed_review
                runtime["application"] = refreshed_application
                notify_review_updated(refreshed_review)
                render_review_result(refreshed_review)
                render_review_summary(refreshed_review)
                render_rewrite_progress(
                    "completed",
                    (
                        "已采用 AI 改写稿，正文和排版已更新。"
                        if use_rewrite
                        else "已保留改写前原文，AI 候选稿没有覆盖当前正文。"
                    ),
                )
                ui.notify(
                    (
                        "已采用 AI 改写稿并完成重新排版"
                        if use_rewrite
                        else "已选择保留改写前原文"
                    ),
                    type="positive",
                )
                await scroll_to_article_comparison()

            def render_snapshot(
                *,
                label: str,
                snapshot: dict[str, Any],
                badge_color: str,
                card_class: str = "",
            ) -> None:
                body = _comparison_plain_text(snapshot.get("body"))
                with ui.column().classes("col-12 col-md-6"):
                    with ui.card().classes(
                        f"review-comparison-card {card_class} w-full"
                    ).props("flat"):
                        with ui.row().classes(
                            "w-full items-center justify-between no-wrap"
                        ):
                            ui.label(label).classes(
                                "text-subtitle1 text-weight-bold"
                            )
                            ui.badge(f"{len(body)} 字").props(
                                f"outline color={badge_color}"
                            )
                        title = str(snapshot.get("title") or "").strip()
                        ui.label(title or "（未设置标题）").classes(
                            "text-h6 text-weight-bold"
                        )
                        subtitle = str(snapshot.get("subtitle") or "").strip()
                        if subtitle:
                            ui.label(f"副标题：{subtitle}").classes(
                                "text-body2 text-blue-grey-8"
                            )
                        digest = str(snapshot.get("digest") or "").strip()
                        if digest:
                            ui.label(f"摘要：{digest}").classes(
                                "text-caption text-blue-grey-7"
                            )
                        ui.separator()
                        with ui.column().classes(
                            "review-comparison-body w-full"
                        ):
                            ui.label(body).classes("text-body1").style(
                                "white-space:pre-wrap;line-height:1.9;"
                                "overflow-wrap:anywhere"
                            )

            with comparison_host:
                ui.separator()
                with ui.row().classes(
                    "w-full items-start justify-between q-gutter-sm"
                ):
                    with ui.column().classes("gap-0"):
                        ui.label("改写前后文章对比").classes(
                            "text-h6 text-weight-bold"
                        )
                        ui.label(
                            (
                                "AI 改写候选稿已经生成，但尚未覆盖正文。当前正文仍使用"
                                "左侧原文，请在对比后明确选择最终版本。"
                                if review_status == "candidate_ready"
                                and source_matches_editor
                                else (
                                    "候选稿生成后当前正文又发生了变化，此候选稿已不能"
                                    "直接采用，请重新进行 AI 评审。"
                                    if review_status == "candidate_ready"
                                    else (
                                        "已选择保留左侧原文，右侧 AI 改写稿仅作为"
                                        "本次对比记录。"
                                        if review_status == "source_kept"
                                        else (
                                            "已选择采用右侧 AI 改写稿；当前正文编辑区"
                                            "使用右侧内容。"
                                            if comparison_matches_editor
                                            else "已采用过右侧 AI 改写稿，但此后正文又"
                                            "经过人工修改，右侧仅保留本次 AI 修改记录。"
                                        )
                                    )
                                )
                            )
                        ).classes("muted")
                    ui.badge("内容对比").props("outline color=indigo-7")
                if risk_warnings:
                    with ui.card().classes(
                        "review-risk-card w-full"
                    ).props("flat"):
                        with ui.row().classes("items-center q-gutter-sm"):
                            ui.icon("warning").classes("text-deep-orange-8 text-h5")
                            ui.label("候选稿包含关键数字变化，请人工核对").classes(
                                "text-subtitle1 text-weight-bold text-deep-orange-10"
                            )
                        ui.label(
                            "这不是系统错误，候选稿已正常生成且尚未覆盖原文。"
                            "数字可能是格式调整，也可能是 AI 新增或删除的事实；"
                            "采用前请逐项确认。"
                        ).classes("text-body2 text-deep-orange-10")
                        for warning in risk_warnings:
                            title = str(warning.get("title") or "关键数字变化")
                            message = str(warning.get("message") or "").strip()
                            ui.label(
                                f"{title}：{message}" if message else title
                            ).classes("text-body2 text-weight-medium")
                change_summary = str(after.get("change_summary") or "").strip()
                if change_summary:
                    with ui.card().classes(
                        "w-full q-pa-sm bg-indigo-1 ui-info-outline"
                    ):
                        ui.label(f"AI 修改摘要：{change_summary}").classes(
                            "text-body2 text-indigo-10"
                        )
                with ui.row().classes(
                    "w-full q-col-gutter-md items-stretch ui-gap-zero"
                ):
                    render_snapshot(
                        label=(
                            "改写前原文（当前版本）"
                            if source_matches_editor
                            else "改写前原文"
                        ),
                        snapshot=before,
                        badge_color="blue-grey-7",
                    )
                    render_snapshot(
                        label=(
                            "AI 改写候选稿（待选择）"
                            if review_status == "candidate_ready"
                            else (
                                "AI 改写稿（未采用）"
                                if review_status == "source_kept"
                                else (
                                    "改写后文章（当前版本）"
                                    if comparison_matches_editor
                                    else "AI 改写后版本（历史记录）"
                                )
                            )
                        ),
                        snapshot=after,
                        badge_color="indigo-7",
                        card_class="review-comparison-card--candidate",
                    )
                if review_status == "candidate_ready":
                    with ui.card().classes(
                        "review-choice-card w-full"
                    ).props("flat"):
                        ui.label("请选择最终使用的文章版本").classes(
                            "text-subtitle1 text-weight-bold"
                        )
                        ui.label(
                            "选择前不会修改当前正文。保留原文不会触发重新排版；"
                            "只有选择 AI 改写稿后，系统才会替换正文并刷新排版。"
                        ).classes("text-body2 text-blue-grey-8")
                        if source_matches_editor:
                            with ui.row().classes(
                                "w-full justify-end q-gutter-sm q-mt-sm"
                            ):
                                keep_source_btn = ui.button(
                                    "保留改写前原文",
                                ).props(
                                    "outline color=blue-grey-8 no-caps icon=history"
                                )
                                use_rewrite_btn = ui.button(
                                    (
                                        "确认数字变化并采用 AI 改写稿"
                                        if risk_warnings
                                        else "采用 AI 改写稿"
                                    ),
                                ).props(
                                    (
                                        "unelevated color=deep-orange-8 no-caps icon=warning"
                                        if risk_warnings
                                        else "unelevated color=indigo-7 no-caps icon=auto_fix_high"
                                    )
                                )
                                keep_source_btn.on_click(
                                    lambda event: select_version(
                                        use_rewrite=False,
                                        button=event.sender,
                                        other_button=use_rewrite_btn,
                                    )
                                )
                                use_rewrite_btn.on_click(
                                    lambda event: select_version(
                                        use_rewrite=True,
                                        button=event.sender,
                                        other_button=keep_source_btn,
                                    )
                                )
                        else:
                            ui.label(
                                "当前正文与生成候选稿时的原文不一致，请重新评审后再生成修改稿。"
                            ).classes("text-negative text-weight-medium q-mt-sm")
            comparison_section.set_visibility(True)
            return True

        def render_rewrite_progress(status: str, detail: str) -> None:
            rewrite_progress_host.clear()
            presentation = {
                "running": (
                    "AI 后台改写中",
                    "info",
                    "blue-8",
                ),
                "completed": (
                    "AI 改写已完成",
                    "check_circle",
                    "green-8",
                ),
                "failed": (
                    "AI 改写失败",
                    "error_outline",
                    "red-8",
                ),
            }
            title, indicator, color = presentation.get(
                status,
                presentation["running"],
            )
            with rewrite_progress_host:
                with ui.card().classes(
                    f"review-progress-card review-progress-card--{status} w-full"
                ).props("flat"):
                    with ui.row().classes("w-full items-center no-wrap q-gutter-sm"):
                        if status == "running":
                            ui.spinner("dots", size="34px", color=color)
                        else:
                            ui.icon(indicator, size="30px", color=color)
                        with ui.column().classes("gap-0"):
                            ui.label(title).classes(
                                f"text-weight-bold text-{color}"
                            )
                            ui.label(detail).classes("text-body2")

        def render_rewrite_controls(review: dict[str, Any]) -> None:
            review_status = str(review.get("status") or "")
            if review_status == "applied":
                ui.label(
                    "已按所选建议完成智能修改，原稿已保存到历史版本。"
                ).classes("text-positive")
                return
            if review_status == "source_kept":
                ui.label(
                    "已选择保留改写前原文，AI 修改稿仅保留为对比记录。"
                ).classes("text-positive")
                return
            if review_status == "candidate_ready":
                ui.label(
                    "AI 修改候选稿已生成，请先在改写前后对比区选择最终版本。"
                ).classes("text-warning text-weight-medium")
                return
            if review_status != "completed":
                return
            ui.separator()
            ui.label(
                "请选择要采纳的整体改进方向。智能修改会围绕所选方向优化"
                "标题、开头、阅读节奏和互动价值，原稿事实与核心观点保持不变。"
            ).classes("muted")

            async def smart_rewrite() -> None:
                if not require_saved_editor():
                    return
                if bool(runtime.get("rewrite_running")):
                    ui.notify("AI 正在改写中，请勿重复提交", type="warning")
                    return
                selected_ids = sorted(runtime["selected_issue_ids"])
                if not selected_ids:
                    ui.notify("请至少勾选一条要采纳的改进意见", type="warning")
                    return
                runtime["rewrite_running"] = True
                runtime["rewrite_in_background"] = False
                render_rewrite_progress(
                    "running",
                    "正在根据已勾选意见优化全文并重新排版，请稍候。",
                )

                def enter_background_rewrite() -> None:
                    runtime["rewrite_in_background"] = True
                    if not ui_alive():
                        return
                    render_rewrite_progress(
                        "running",
                        "任务仍在执行，可继续审核其他文章或使用其他功能。",
                    )
                    ui.notify(
                        "已转入后台改写，可继续使用其他功能；右侧可查看进度",
                        type="info",
                    )
                    if on_enter_background is not None:
                        on_enter_background()

                set_button_loading(
                    smart_btn,
                    True,
                    "AI 正在按已勾选意见修改原文并重新排版，请稍候…",
                    on_background=enter_background_rewrite,
                    background_label="转入后台改写",
                )
                refreshed_review: dict[str, Any] | None = None
                try:
                    generated_review = await run.io_bound(
                        lambda: service.generate_editorial_rewrite_candidate(
                            batch_id,
                            job_id,
                            str(review["id"]),
                            issue_ids=selected_ids,
                            rewrite_mode="engagement_optimization",
                            paragraph_numbers=[],
                            instruction="",
                        )
                    )
                    application = dict(generated_review.get("application") or {})
                    if not application.get("id"):
                        raise RuntimeError("AI 修改稿生成成功，但缺少应用记录")
                    refreshed_review = generated_review
                    if not ui_alive():
                        return
                    runtime["application"] = application
                except Exception as exc:  # noqa: BLE001
                    if ui_alive():
                        render_rewrite_progress(
                            "failed",
                            f"{exc}。请检查模型配置或稍后重新提交。",
                        )
                        ui.notify(
                            f"整篇优化失败：{exc}",
                            type="negative",
                            timeout=12000,
                        )
                finally:
                    runtime["rewrite_running"] = False
                    if ui_alive():
                        set_button_loading(smart_btn, False)
                if refreshed_review is not None and ui_alive():
                    runtime["review"] = refreshed_review
                    notify_review_updated(refreshed_review)
                    render_review_result(
                        refreshed_review,
                        include_comparison=False,
                    )
                    render_article_comparison(
                        refreshed_review,
                        dict(runtime.get("application") or {}),
                    )
                    render_review_summary(refreshed_review)
                    candidate_snapshot = dict(
                        (runtime.get("application") or {}).get(
                            "candidate_snapshot"
                        )
                        or {}
                    )
                    has_number_risks = bool(
                        candidate_snapshot.get("risk_warnings")
                    )
                    render_rewrite_progress(
                        "completed",
                        (
                            "AI 候选稿已生成，检测到关键数字变化；当前仍保留原文，"
                            "请在对比区核对后选择最终版本。"
                            if has_number_risks
                            else "AI 候选稿已生成，当前正文仍保留原文；"
                            "请在下方对比后选择最终版本。"
                        ),
                    )
                    ui.notify(
                        (
                            "AI 候选稿已生成，并检测到关键数字变化；"
                            "请在对比区人工核对，原文尚未被覆盖"
                            if has_number_risks
                            else "AI 改写候选稿已生成，尚未覆盖原文；"
                            "请在下方对比后选择保留原文或采用改写稿"
                        ),
                        type="warning" if has_number_risks else "positive",
                        timeout=10000,
                    )
                    if bool(comparison_section.visible):
                        await scroll_to_article_comparison()
                    elif on_article_updated is not None:
                        await on_article_updated()
                    else:
                        await scroll_to_review_result()

            with ui.row().classes("w-full justify-end q-gutter-sm q-mt-sm"):
                smart_btn = ui.button(
                    "按所选建议优化整篇",
                    on_click=smart_rewrite,
                ).props("unelevated color=teal-8 no-caps icon=auto_fix_high")

        def render_review_summary(review: dict[str, Any] | None) -> None:
            result_summary_host.clear()
            if not review:
                return
            result = dict(review.get("result") or {})
            status = str(review.get("status") or "")
            if status in {"failed", "stale"}:
                return
            progress = editorial_review_progress(review)
            with result_summary_host, ui.card().classes(
                "review-summary-card review-status-progress w-full"
            ).props("flat"):
                with ui.row().classes(
                    "w-full items-center justify-between q-gutter-sm"
                ):
                    ui.label(str(progress["title"])).classes(
                        "text-subtitle1 text-weight-bold"
                    )
                    ui.label(str(progress["percent"])).classes(
                        "review-status-progress__percent text-weight-bold"
                    )
                ui.linear_progress(
                    value=float(progress["value"]),
                    show_value=False,
                    size="12px",
                ).props(
                    "color=teal-8 track-color=teal-1 rounded"
                ).classes("review-status-progress__bar w-full")
                detail = (
                    str(progress["stage"])
                    if bool(progress["active"])
                    else str(
                        result.get("conclusion")
                        or result.get("summary")
                        or progress["stage"]
                    )
                )
                ui.label(detail).classes("muted")
                if bool(progress["active"]):
                    elapsed = int(progress["elapsed_seconds"] or 0)
                    ui.label(
                        f"已处理 {elapsed // 60:02d}:{elapsed % 60:02d} · 完成后将自动刷新结果"
                    ).classes("muted text-caption")

        def render_review_result(
            review: dict[str, Any] | None,
            *,
            include_comparison: bool = True,
        ) -> None:
            result_host.clear()
            if include_comparison:
                render_article_comparison(
                    review,
                    dict(runtime.get("application") or {}),
                )
            if not review:
                return
            result = dict(review.get("result") or {})
            issues = [dict(item) for item in result.get("issues") or []]
            known_issue_ids = {str(item.get("id") or "") for item in issues}
            runtime["selected_issue_ids"].intersection_update(known_issue_ids)
            with result_host:
                ui.separator()
                review_status = str(review.get("status") or "")
                status_labels = {
                    "running": "评审中",
                    "rewriting": "正在生成修改稿",
                    "completed": "评审完成",
                    "candidate_ready": "候选稿待确认",
                    "applied": "修改稿已应用",
                    "source_kept": "已保留原文",
                    "failed": "评审失败",
                    "stale": "评审已过期",
                }
                status_colors = {
                    "running": "blue-7",
                    "rewriting": "blue-7",
                    "completed": "teal-7",
                    "candidate_ready": "indigo-7",
                    "applied": "green-7",
                    "source_kept": "blue-grey-7",
                    "failed": "red-7",
                    "stale": "orange-8",
                }
                with ui.row().classes(
                    "review-result-header w-full items-center justify-between"
                ):
                    with ui.column().classes("gap-0"):
                        ui.label(
                            f'评审结果 · {review.get("profile_name") or "AI 评审团"}'
                        ).classes("text-h6 text-weight-bold")
                        ui.label(
                            f'模型：{review.get("model_name") or "公众号绑定模型"}'
                        ).classes("muted")
                    with ui.row().classes("items-center"):
                        if result and review_status not in {
                            "running",
                            "rewriting",
                            "failed",
                            "stale",
                        }:
                            ui.badge(
                                f'总分 {int(result.get("overall_score") or 0)}'
                            ).props("color=teal-7")
                        ui.badge(
                            status_labels.get(review_status, review_status or "未知状态")
                        ).props(
                            f'color={status_colors.get(review_status, "grey-7")}'
                        )
                        blockers = int(review.get("blocking_count") or 0)
                        if blockers:
                            ui.badge(f"{blockers} 条阻断项待核实").props(
                                "color=red-7"
                            )
                if review_status in {"running", "rewriting"}:
                    ui.label(
                        "该评审仍在处理中，下方会自动显示当前进度并在完成后刷新结果。"
                    ).classes("text-info")
                    return
                if review_status in {"failed", "stale"}:
                    error = str(review.get("error") or "")
                    message = (
                        "文章已在评审后发生修改，请重新启动 AI 评审。"
                        if review_status == "stale"
                        else "上一次 AI 评审没有成功，请检查模型配置后重试。"
                    )
                    ui.label(message).classes("text-negative")
                    if error:
                        ui.label(f"原因：{error}").classes(
                            "text-negative text-caption"
                        )
                    return
                ui.label("评审结论").classes(
                    "text-subtitle1 text-weight-bold q-mt-sm"
                )
                if result.get("summary"):
                    ui.label(str(result["summary"])).classes("text-weight-medium")
                if result.get("conclusion"):
                    ui.label(f'发布建议：{result["conclusion"]}').classes(
                        "text-weight-medium text-indigo-9"
                    )
                strengths = list(result.get("strengths") or [])
                if strengths:
                    ui.label("优点：" + "；".join(str(item) for item in strengths)).classes(
                        "text-positive text-caption"
                    )
                dimensions = list(result.get("dimensions") or [])
                if dimensions:
                    engagement_dimension_ids = {
                        "title_click",
                        "opening_retention",
                        "completion_potential",
                        "like_potential",
                        "share_potential",
                    }
                    returned_dimension_ids = {
                        str(item.get("id") or "") for item in dimensions
                    }
                    if engagement_dimension_ids.issubset(
                        returned_dimension_ids
                    ):
                        ui.label(
                            "以下为 AI 预估的运营潜力分，不是真实公众号后台数据。"
                        ).classes("muted text-caption")
                    else:
                        ui.label(
                            "这是旧版评审维度；重新点击“开始 AI 评审”后，"
                            "将改为标题、开头、完读、点赞和转发五项运营潜力。"
                        ).classes("muted text-caption")
                    with ui.grid(columns=5).classes(
                        "review-score-grid w-full"
                    ):
                        for dimension in dimensions:
                            score_text = _format_dimension_score(
                                dimension.get("score"),
                                summary=dimension.get("summary"),
                                score_available=dimension.get("score_available"),
                            )
                            with ui.card().classes(
                                "review-score-card"
                            ).props("flat"):
                                ui.label(
                                    str(dimension.get("name") or "运营潜力")
                                ).classes("text-caption text-weight-bold")
                                ui.label(
                                    score_text
                                ).classes("text-h6 text-indigo-8")
                                if score_text == "—":
                                    ui.label(
                                        "本次评审未返回该项评分"
                                    ).classes("muted text-caption")
                                elif dimension.get("summary"):
                                    ui.label(
                                        str(dimension.get("summary") or "")
                                    ).classes("muted text-caption")
                if not issues:
                    ui.label("评审未发现需要处理的整体问题。").classes(
                        "text-positive q-mt-sm"
                    )
                issue_group = ""
                for issue in issues:
                    issue_id = str(issue.get("id") or "")
                    can_auto_apply = _issue_can_auto_apply(issue)
                    resolution = str(issue.get("resolution") or "open")
                    next_group = "editorial" if can_auto_apply else "safety"
                    if next_group != issue_group:
                        ui.label(
                            "整体改进方向（可选择）"
                            if next_group == "editorial"
                            else "发布风险（需人工核实）"
                        ).classes("text-subtitle1 text-weight-bold q-mt-sm")
                        issue_group = next_group
                    with ui.card().classes(
                        f"review-issue-card review-issue-card--{next_group} w-full"
                    ).props("flat"):
                        with ui.row().classes(
                            "w-full items-center justify-between"
                        ):
                            with ui.row().classes("items-center q-gutter-xs"):
                                ui.badge(
                                    str(issue.get("role_name") or "评审角色")
                                ).props("color=blue-grey-7")
                                severity = str(issue.get("severity") or "medium")
                                ui.badge(
                                    SEVERITY_LABELS.get(severity, severity)
                                ).props(
                                    f'color={SEVERITY_COLORS.get(severity, "grey-7")}'
                                )
                                if issue.get("blocks_draft") and resolution == "open":
                                    ui.badge("阻止写入草稿").props("color=red-7")
                                if resolution != "open":
                                    ui.badge(
                                        RESOLUTION_LABELS.get(resolution, resolution)
                                    ).props("color=green-7")
                            if can_auto_apply:
                                selected = ui.checkbox(
                                    "勾选并交给 AI 改写",
                                    value=issue_id
                                    in runtime["selected_issue_ids"],
                                )
                                selected.on_value_change(
                                    lambda event, iid=issue_id: _update_selection(
                                        runtime["selected_issue_ids"],
                                        iid,
                                        bool(event.value),
                                    )
                                )
                            else:
                                ui.badge("需人工核实").props(
                                    "outline color=deep-orange-7"
                                ).tooltip(
                                    "事实或合规提醒不能交给 AI 猜测或自动改写"
                                )
                        location = (
                            str(issue.get("location") or "")
                            if not can_auto_apply
                            else ""
                        )
                        category = str(issue.get("category") or "")
                        if location or category:
                            ui.label(
                                " · ".join(item for item in (location, category) if item)
                            ).classes("muted text-caption")
                        if issue.get("excerpt") and not can_auto_apply:
                            ui.label(f'原文：{issue["excerpt"]}').classes(
                                "text-caption text-blue-grey-8"
                            )
                        ui.label(
                            f'评审判断：{issue.get("problem") or "未说明"}'
                        ).classes(
                            "text-weight-medium"
                        )
                        ui.label(
                            f'整体改进方向：{issue.get("suggestion") or "请人工判断"}'
                        )
                        if not can_auto_apply:
                            ui.label(
                                "这类提醒不会交给 AI 猜测或改写。您无需填写意见，"
                                "只需选择核实结果。"
                            ).classes("muted text-caption")

                            async def resolve_issue(
                                resolution_value: str,
                                button: Any,
                                *,
                                iid: str = issue_id,
                            ) -> None:
                                note = {
                                    "resolved": (
                                        "用户在桌面端 AI 评审结论中确认已人工核实"
                                    ),
                                    "waived": (
                                        "用户在桌面端 AI 评审结论中选择保留原文并接受风险"
                                    ),
                                }.get(resolution_value, "")
                                set_button_loading(
                                    button,
                                    True,
                                    "正在保存该事实/合规项的人工处理结果…",
                                )
                                updated_review: dict[str, Any] | None = None
                                try:
                                    updated_review = await run.io_bound(
                                        lambda: service.resolve_editorial_review_issue(
                                            str(review["id"]),
                                            iid,
                                            resolution=resolution_value,
                                            note=note,
                                            resolved_by="桌面端运营人员",
                                        )
                                    )
                                    if ui_alive():
                                        ui.notify("核实结果已保存", type="positive")
                                except Exception as exc:  # noqa: BLE001
                                    if ui_alive():
                                        ui.notify(
                                            f"保存核实结果失败：{exc}",
                                            type="negative",
                                            timeout=10000,
                                        )
                                finally:
                                    if ui_alive():
                                        set_button_loading(button, False)
                                if updated_review is not None and ui_alive():
                                    runtime["review"] = updated_review
                                    notify_review_updated(updated_review)
                                    render_review_result(updated_review)
                                    render_review_summary(updated_review)
                                    await scroll_to_review_result()

                            def open_waive_confirmation(
                                *,
                                button: Any,
                                handler: Any = resolve_issue,
                            ) -> None:
                                with ui.dialog() as confirm, ui.card():
                                    ui.label("确认保留原文并接受这条风险？").classes(
                                        "text-subtitle1 text-weight-bold"
                                    )
                                    ui.label(
                                        "确认后该风险不再阻止写入草稿，系统会保留审计记录。"
                                    ).classes("muted")

                                    async def confirm_waive() -> None:
                                        confirm.close()
                                        await handler("waived", button)

                                    with ui.row().classes("w-full justify-end"):
                                        ui.button(
                                            "取消",
                                            on_click=confirm.close,
                                        ).props("flat no-caps")
                                        ui.button(
                                            "确认接受风险",
                                            on_click=confirm_waive,
                                        ).props(
                                            "unelevated color=deep-orange-7 no-caps"
                                        )
                                confirm.open()

                            if resolution == "open":
                                with ui.row().classes("items-center"):
                                    verified_btn = ui.button(
                                        "我已核实",
                                    ).props(
                                        "outline dense color=teal-8 no-caps icon=fact_check"
                                    )
                                    waive_btn = ui.button(
                                        "保留原文并接受风险",
                                    ).props(
                                        "outline dense color=deep-orange-7 no-caps icon=warning"
                                    )
                                verified_btn.on_click(
                                    lambda _=None, button=verified_btn, handler=resolve_issue: handler(
                                        "resolved", button
                                    )
                                )
                                waive_btn.on_click(
                                    lambda _=None,
                                    button=waive_btn,
                                    open_confirmation=open_waive_confirmation: open_confirmation(
                                        button=button
                                    )
                                )
                            else:
                                resolved_by = str(
                                    issue.get("resolved_by") or "运营人员"
                                )
                                resolved_at = str(issue.get("resolved_at") or "")
                                ui.label(
                                    "处理记录："
                                    + resolved_by
                                    + (f" · {resolved_at}" if resolved_at else "")
                                ).classes("muted text-caption")
                                if issue.get("resolution_note"):
                                    ui.label(
                                        f'处理说明：{issue["resolution_note"]}'
                                    ).classes("muted text-caption")
                                reopen_btn = ui.button(
                                    "恢复待核实",
                                ).props(
                                    "flat dense color=blue-grey-7 no-caps icon=undo"
                                )
                                reopen_btn.on_click(
                                    lambda _=None, button=reopen_btn, handler=resolve_issue: handler(
                                        "open", button
                                    )
                                )
                render_rewrite_controls(review)

        async def start_review() -> None:
            if not require_saved_editor():
                return
            if not list(roles_in.value or []):
                ui.notify("请至少选择一个评审角色", type="warning")
                return
            if not list(styles_in.value or []):
                ui.notify("请至少选择一种目标风格", type="warning")
                return
            review_config = collect_config()
            profile_id = str(profile_in.value or "") or None

            def run_review() -> dict[str, Any]:
                return service.run_editorial_review(
                    batch_id,
                    job_id,
                    profile_id=profile_id,
                    config=review_config,
                )

            if on_background_review is not None:
                started = on_background_review(
                    batch_id=batch_id,
                    job_id=job_id,
                    account_name=str(job.get("account_name") or "当前公众号"),
                    operation=run_review,
                )
                if started:
                    runtime["review"] = {
                        **dict(runtime.get("review") or {}),
                        "status": "running",
                    }
                    sync_start_button()
                    ui.notify(
                        "AI 评审已转入后台，可继续处理其他文章；右侧可查看进度。",
                        type="positive",
                        timeout=8000,
                    )
                    if on_enter_background is not None:
                        on_enter_background()
                return

            set_button_loading(
                start_btn,
                True,
                "AI 正在评估标题、开头、完读、点赞和转发潜力…",
            )
            completed_review: dict[str, Any] | None = None
            try:
                review = await run.io_bound(
                    run_review
                )
                if not ui_alive():
                    return
                runtime["review"] = review
                runtime["application"] = None
                runtime["selected_issue_ids"].clear()
                notify_review_updated(review)
                render_review_result(review)
                render_review_summary(review)
                completed_review = review
                ui.notify(
                    f'AI 评审完成，共发现 {len((review.get("result") or {}).get("issues") or [])} 条建议',
                    type="positive",
                    timeout=10000,
                )
            except Exception as exc:  # noqa: BLE001
                if ui_alive():
                    ui.notify(f"AI 评审失败：{exc}", type="negative", timeout=12000)
                    try:
                        failed_reviews = await run.io_bound(
                            lambda: service.list_editorial_reviews(
                                job_id=job_id,
                                limit=1,
                            )
                        )
                    except Exception:  # noqa: BLE001
                        failed_reviews = []
                    if failed_reviews:
                        runtime["review"] = failed_reviews[0]
                        notify_review_updated(failed_reviews[0])
                        render_review_result(failed_reviews[0])
                        render_review_summary(failed_reviews[0])
            finally:
                if ui_alive():
                    set_button_loading(start_btn, False)
                    sync_start_button()
            if completed_review is not None:
                if ui_alive():
                    await scroll_to_review_result()

        async def request_review_start() -> None:
            """Require an explicit decision before replacing an existing review."""

            _label, disabled, requires_confirmation = _review_start_action(
                dict(runtime.get("review") or {}) or None
            )
            if disabled:
                ui.notify("AI 评审正在进行中，请等待完成", type="info")
                return
            if not requires_confirmation:
                await start_review()
                return

            with ui.dialog() as confirm_rerun, ui.card().classes("q-pa-md"):
                ui.label("确认重新评审？").classes(
                    "text-subtitle1 text-weight-bold"
                )
                ui.label(
                    "重新评审会调用该公众号绑定的文本模型并产生一次新的评审记录；"
                    "现有评审记录会保留。"
                ).classes("muted")

                async def confirm_review_rerun() -> None:
                    confirm_rerun.close()
                    await start_review()

                with ui.row().classes("w-full justify-end q-gutter-sm"):
                    ui.button(
                        "取消",
                        on_click=confirm_rerun.close,
                    ).props("flat no-caps")
                    ui.button(
                        "确认重新评审",
                        on_click=confirm_review_rerun,
                    ).props("unelevated color=indigo-7 no-caps")
            confirm_rerun.open()

        def sync_start_button() -> None:
            label, disabled, _requires_confirmation = _review_start_action(
                dict(runtime.get("review") or {}) or None
            )
            start_btn.set_text(label)
            start_btn.props(remove="unelevated outline flat color icon")
            if label == "重新评审":
                start_btn.props(add="flat color=blue-grey-7 icon=refresh")
            else:
                start_btn.props(add="unelevated color=indigo-7 icon=rate_review")
            if disabled:
                start_btn.disable()
            else:
                start_btn.enable()

        with start_action_host:
            start_btn = ui.button(
                "开始 AI 评审",
                on_click=request_review_start,
            ).props("no-caps")
            sync_start_button()
            ui.label(
                "评审和生成修改稿会调用该公众号绑定的文本模型，可能产生模型费用。"
            ).classes("muted text-caption")

        if runtime["review"]:
            applications = service.list_editorial_review_applications(
                str(runtime["review"]["id"]), limit=1
            )
            runtime["application"] = applications[0] if applications else None
            render_review_result(runtime["review"])
            render_review_summary(runtime["review"])
            result_section.set_visibility(True)

        def refresh_active_review() -> None:
            """Refresh an open workbench without requiring close-and-reopen."""

            if not ui_alive():
                return
            current = dict(runtime.get("review") or {})
            current_status = str(current.get("status") or "")
            if current_status not in {"running", "rewriting"}:
                return
            try:
                reviews = service.list_editorial_reviews(job_id=job_id, limit=1)
            except Exception:  # noqa: BLE001
                render_review_summary(current)
                return
            refreshed = dict(reviews[0]) if reviews else current
            changed = (
                str(refreshed.get("id") or "") != str(current.get("id") or "")
                or str(refreshed.get("status") or "") != current_status
                or int(refreshed.get("revision") or 0)
                != int(current.get("revision") or 0)
            )
            runtime["review"] = refreshed
            render_review_summary(refreshed)
            if changed:
                render_review_result(refreshed)
                sync_start_button()
                notify_review_updated(refreshed)

        client_timer(2.0, refresh_active_review, immediate=False)

    return {
        "reveal_result": scroll_to_review_result,
        "reveal_comparison": scroll_to_article_comparison,
        "reveal_settings": scroll_to_review_settings,
        "start_review": request_review_start,
        "settings_summary": current_settings_summary,
    }


def build_editorial_review_profiles_panel(
    _state: Any,
    *,
    on_profiles_change: Callable[[], None] | None = None,
) -> None:
    """Manage reusable custom editorial review profiles."""

    service = BatchService(
        load_config(),
        owner_user_id=str(getattr(_state, "current_user_id", "") or ""),
        recover_stale_work=False,
    )
    options = service.get_editorial_review_options()
    role_options = {
        str(item["id"]): str(item["name"]) for item in options.get("roles") or []
    }
    style_options = {
        str(item["id"]): str(item["name"]) for item in options.get("styles") or []
    }
    strictness_options = {
        str(item["id"]): str(item["name"])
        for item in options.get("strictness_levels") or []
    }
    host = ui.column().classes("w-full gap-3")

    def open_editor(
        record: dict[str, Any] | None = None,
        *,
        copy_record: bool = False,
    ) -> None:
        editing_id = (
            str(record["id"])
            if record and not copy_record and not bool(record.get("builtin"))
            else None
        )
        config = dict((record or {}).get("config") or {})
        with ui.dialog() as dialog, ui.card().classes("w-full").style(
            "max-width:860px;max-height:92vh;overflow-y:auto"
        ):
            ui.label(
                "编辑自定义评审方案" if editing_id else "新建自定义评审方案"
            ).classes("text-h6 text-weight-bold")
            ui.label(
                "这里只定义业务评审规则；JSON、评分字段、事实与合规底线由系统控制。"
            ).classes("muted")
            name_in = ui.input(
                "方案名称",
                value=(
                    f'{record.get("name")}（副本）'
                    if record and copy_record
                    else str((record or {}).get("name") or "")
                ),
            ).classes("w-full").props("outlined stack-label maxlength=80")
            description_in = ui.input(
                "方案说明",
                value=str((record or {}).get("description") or ""),
            ).classes("w-full").props("outlined stack-label maxlength=500")
            with ui.grid(columns=2).classes("w-full gap-3"):
                roles_in = ui.select(
                    role_options,
                    value=list(config.get("role_ids") or []),
                    label="评审角色",
                    multiple=True,
                ).classes("w-full").props(
                    "outlined stack-label options-dense use-chips"
                )
                styles_in = ui.select(
                    style_options,
                    value=list(config.get("style_ids") or []),
                    label="目标风格",
                    multiple=True,
                ).classes("w-full").props(
                    "outlined stack-label options-dense use-chips"
                )
            strictness_in = ui.toggle(
                strictness_options,
                value=str(config.get("strictness") or "standard"),
            )
            focus_in = ui.textarea(
                "评审重点",
                value=str(config.get("focus") or ""),
            ).classes("w-full").props(
                "outlined rows=3 stack-label maxlength=4000 counter"
            )
            audience_in = ui.input(
                "目标读者",
                value=str(config.get("target_audience") or ""),
            ).classes("w-full").props("outlined stack-label")
            with ui.grid(columns=2).classes("w-full gap-3"):
                required_in = ui.textarea(
                    "必须检查项（每行一项）",
                    value=_join_lines(config.get("required_checks")),
                ).classes("w-full").props("outlined rows=4 stack-label")
                ignored_in = ui.textarea(
                    "忽略项（每行一项）",
                    value=_join_lines(config.get("ignored_items")),
                ).classes("w-full").props("outlined rows=4 stack-label")
                banned_in = ui.textarea(
                    "禁用表达（每行一项）",
                    value=_join_lines(config.get("banned_expressions")),
                ).classes("w-full").props("outlined rows=4 stack-label")
                must_keep_in = ui.textarea(
                    "必须保留内容（每行一项）",
                    value=_join_lines(config.get("must_keep")),
                ).classes("w-full").props("outlined rows=4 stack-label")
            advanced_in = ui.textarea(
                "高级业务规则",
                value=str(config.get("advanced_rules") or ""),
            ).classes("w-full").props(
                "outlined rows=5 stack-label maxlength=8000 counter"
            )
            with ui.grid(columns=2).classes("w-full gap-3"):
                good_example_in = ui.textarea(
                    "示例好文章（可选）",
                    value=str(config.get("good_example") or ""),
                    placeholder="粘贴一段能代表期望质量和风格的文章示例",
                ).classes("w-full").props(
                    "outlined rows=6 stack-label maxlength=6000 counter"
                )
                bad_example_in = ui.textarea(
                    "示例坏文章（可选）",
                    value=str(config.get("bad_example") or ""),
                    placeholder="粘贴需要避免的文章示例",
                ).classes("w-full").props(
                    "outlined rows=6 stack-label maxlength=6000 counter"
                )
            score_weights_in = ui.textarea(
                "评分权重（每行“维度=权重”，0–100）",
                value=_format_weights(config.get("score_weights")),
                placeholder="例如：\n标题点击力=25\n开头留存力=25\n完读潜力=20\n点赞潜力=15\n转发潜力=15",
            ).classes("w-full").props("outlined rows=4 stack-label")
            permissions = dict(config.get("permissions") or {})
            with ui.row().classes("items-center q-gutter-md"):
                allow_rewrite_in = ui.switch(
                    "允许生成修改稿",
                    value=bool(permissions.get("allow_rewrite", True)),
                )
                allow_title_in = ui.switch(
                    "允许修改标题",
                    value=bool(permissions.get("allow_title_changes", True)),
                )
                allow_body_in = ui.switch(
                    "允许修改正文",
                    value=bool(permissions.get("allow_body_changes", True)),
                )
                enabled_in = ui.switch(
                    "启用方案",
                    value=bool((record or {}).get("enabled", True)),
                )

            async def submit() -> None:
                if not str(name_in.value or "").strip():
                    ui.notify("请填写评审方案名称", type="warning")
                    return
                if not list(roles_in.value or []):
                    ui.notify("请至少选择一个评审角色", type="warning")
                    return
                if not list(styles_in.value or []):
                    ui.notify("请至少选择一种目标风格", type="warning")
                    return
                set_button_loading(save_btn, True, "正在保存评审方案…")
                try:
                    await run.io_bound(
                        lambda: service.save_editorial_review_profile(
                            profile_id=editing_id,
                            name=str(name_in.value or ""),
                            description=str(description_in.value or ""),
                            enabled=bool(enabled_in.value),
                            config={
                                **config,
                                "role_ids": list(roles_in.value or []),
                                "style_ids": list(styles_in.value or []),
                                "strictness": str(
                                    strictness_in.value or "standard"
                                ),
                                "focus": str(focus_in.value or ""),
                                "target_audience": str(audience_in.value or ""),
                                "required_checks": _split_lines(required_in.value),
                                "ignored_items": _split_lines(ignored_in.value),
                                "banned_expressions": _split_lines(banned_in.value),
                                "must_keep": _split_lines(must_keep_in.value),
                                "advanced_rules": str(advanced_in.value or ""),
                                "good_example": str(good_example_in.value or ""),
                                "bad_example": str(bad_example_in.value or ""),
                                "score_weights": _parse_weights(
                                    score_weights_in.value
                                ),
                                "permissions": {
                                    "allow_rewrite": bool(allow_rewrite_in.value),
                                    "allow_title_changes": bool(allow_title_in.value),
                                    "allow_body_changes": bool(allow_body_in.value),
                                },
                            },
                        )
                    )
                    dialog.close()
                    render()
                    if on_profiles_change:
                        on_profiles_change()
                    ui.notify("评审方案已保存", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(
                        f"保存评审方案失败：{exc}",
                        type="negative",
                        timeout=10000,
                    )
                finally:
                    set_button_loading(save_btn, False)

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                save_btn = ui.button("保存方案", on_click=submit).props(
                    "unelevated color=teal-9 no-caps icon=save"
                )
        dialog.open()

    def confirm_delete(profile: dict[str, Any]) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label(f'确定删除评审方案“{profile["name"]}”吗？').classes(
                "text-weight-medium"
            )

            async def remove() -> None:
                set_button_loading(delete_btn, True, "正在删除评审方案…")
                try:
                    await run.io_bound(
                        lambda: service.delete_editorial_review_profile(
                            str(profile["id"])
                        )
                    )
                    dialog.close()
                    render()
                    if on_profiles_change:
                        on_profiles_change()
                    ui.notify("评审方案已删除", type="positive")
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"删除失败：{exc}", type="negative")
                finally:
                    set_button_loading(delete_btn, False)

            with ui.row().classes("w-full justify-end"):
                ui.button("取消", on_click=dialog.close).props("flat no-caps")
                delete_btn = ui.button("删除", on_click=remove).props(
                    "unelevated color=red-7 no-caps"
                )
        dialog.open()

    def render() -> None:
        host.clear()
        profiles = service.list_editorial_review_profiles(include_builtin=True)
        with host:
            with ui.element("div").classes("card w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.column().classes("gap-0"):
                        ui.label("AI 评审方案管理").classes(
                            "text-h6 text-weight-bold"
                        )
                        ui.label(
                            "内置方案可以复制修改；自定义方案可绑定为不同公众号的默认评审团。"
                        ).classes("muted")
                    ui.button(
                        "新建自定义方案",
                        on_click=lambda: open_editor(),
                    ).props("unelevated color=teal-9 no-caps icon=add")
            for profile in profiles:
                with ui.element("div").classes("card w-full"):
                    with ui.row().classes("w-full items-start justify-between"):
                        with ui.column().classes("gap-0").style(
                            "min-width:0;flex:1"
                        ):
                            with ui.row().classes("items-center"):
                                ui.label(str(profile["name"])).classes(
                                    "text-weight-bold"
                                )
                                ui.badge(
                                    "内置" if profile.get("builtin") else "自定义"
                                ).props(
                                    "color=blue-grey-7"
                                    if profile.get("builtin")
                                    else "color=teal-7"
                                )
                                if not profile.get("enabled"):
                                    ui.badge("已停用").props("color=grey-7")
                            ui.label(
                                str(profile.get("description") or "")
                            ).classes("muted")
                            profile_config = dict(profile.get("config") or {})
                            ui.label(
                                "角色："
                                + "、".join(
                                    role_options.get(str(item), str(item))
                                    for item in profile_config.get("role_ids") or []
                                )
                                + "；风格："
                                + "、".join(
                                    style_options.get(str(item), str(item))
                                    for item in profile_config.get("style_ids") or []
                                )
                            ).classes("text-caption")
                        with ui.row().classes("items-center"):
                            if profile.get("builtin"):
                                ui.button(
                                    "复制修改",
                                    on_click=lambda _=None, item=dict(profile): open_editor(
                                        item, copy_record=True
                                    ),
                                ).props("flat dense color=teal-9 no-caps")
                            else:
                                ui.button(
                                    "编辑",
                                    on_click=lambda _=None, item=dict(profile): open_editor(
                                        item
                                    ),
                                ).props("flat dense color=teal-9 no-caps")
                                ui.button(
                                    "删除",
                                    on_click=lambda _=None, item=dict(profile): confirm_delete(
                                        item
                                    ),
                                ).props("flat dense color=red-7 no-caps")

    render()


def enabled_profile_options(service: BatchService) -> dict[str, str]:
    """Return profile options for per-account default selectors."""

    return {
        str(item["id"]): (
            f'{item["name"]}（内置）' if item.get("builtin") else str(item["name"])
        )
        for item in service.list_editorial_review_profiles(include_builtin=True)
        if bool(item.get("builtin")) or bool(item.get("enabled"))
    }


def _join_lines(value: Any) -> str:
    if not isinstance(value, (list, tuple, set)):
        return str(value or "")
    return "\n".join(str(item) for item in value if str(item).strip())


def _split_lines(value: Any) -> list[str]:
    return list(
        dict.fromkeys(
            item.strip()
            for item in str(value or "").replace("\r\n", "\n").split("\n")
            if item.strip()
        )
    )


def _format_weights(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return "\n".join(
        f"{key}={weight}"
        for key, weight in value.items()
        if str(key).strip()
    )


def _parse_weights(value: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in str(value or "").replace("\r\n", "\n").split("\n"):
        if "=" not in line:
            continue
        name, raw_weight = line.split("=", 1)
        name = name.strip()
        try:
            weight = int(raw_weight.strip())
        except ValueError:
            continue
        if name:
            result[name] = max(0, min(100, weight))
    return result


def _update_selection(selected: set[str], issue_id: str, checked: bool) -> None:
    if checked:
        selected.add(issue_id)
    else:
        selected.discard(issue_id)
