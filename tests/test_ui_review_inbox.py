from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import Database
from app.services.batches import BatchService
from app.ui.panels import tasks
from app.ui.styles import APP_CSS

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "app" / "ui" / "desktop.py"
STYLES = ROOT / "app" / "ui" / "styles.py"


def test_task_times_are_displayed_in_china_standard_time() -> None:
    assert (
        tasks._format_time("2026-08-05T06:21:54+00:00")  # noqa: SLF001
        == "2026-08-05 14:21:54"
    )
    assert (
        tasks._format_time("2026-08-05T06:21:54Z")  # noqa: SLF001
        == "2026-08-05 14:21:54"
    )
    assert (
        tasks._format_time(  # noqa: SLF001
            datetime(
                2026,
                8,
                5,
                10,
                21,
                54,
                tzinfo=timezone(timedelta(hours=4)),
            )
        )
        == "2026-08-05 14:21:54"
    )
    assert (
        tasks._format_time("2026-08-05T14:21:54")  # noqa: SLF001
        == "2026-08-05 14:21:54"
    )


def test_review_inbox_adapter_prefers_service_contract_and_normalizes_counts() -> None:
    class Service:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def list_review_inbox(self, **filters: object) -> dict[str, object]:
            self.calls.append(filters)
            return {
                "items": [{"job_id": 7, "title": "待审核文章"}],
                "counts": {"review": "3", "write_failed": 1},
                "next_cursor": 8,
            }

    service = Service()
    payload = tasks._load_review_inbox(  # noqa: SLF001
        service,  # type: ignore[arg-type]
        bucket="review",
        account_id="",
        limit=1,
    )

    assert service.calls == [
        {
            "bucket": "review",
            "account_id": None,
            "limit": 1,
            "cursor": None,
        }
    ]
    assert payload == {
        "items": [{"job_id": 7, "title": "待审核文章"}],
        "counts": {
            "review": 3,
            "ready_for_draft": 0,
            "write_failed": 1,
            "generation_failed": 0,
            "today_completed": 0,
        },
        "next_cursor": "8",
    }


def test_review_inbox_adapter_paginates_past_service_page_cap_without_duplicates() -> None:
    rows = [{"job_id": index} for index in range(1, 151)]

    class Service:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def list_review_inbox(self, **filters: object) -> dict[str, object]:
            self.calls.append(filters)
            assert int(filters["limit"]) <= 100
            start = int(str(filters.get("cursor") or "0"))
            end = min(start + int(filters["limit"]), len(rows))
            return {
                "items": rows[start:end],
                "counts": {"review": len(rows)},
                "next_cursor": str(end) if end < len(rows) else None,
            }

    service = Service()
    payload = tasks._load_review_inbox(  # noqa: SLF001
        service,  # type: ignore[arg-type]
        bucket="review",
        account_id="",
        limit=150,
    )

    job_ids = [int(item["job_id"]) for item in payload["items"]]
    assert service.calls == [
        {
            "bucket": "review",
            "account_id": None,
            "limit": 100,
            "cursor": None,
        },
        {
            "bucket": "review",
            "account_id": None,
            "limit": 50,
            "cursor": "100",
        },
    ]
    assert job_ids == list(range(1, 151))
    assert job_ids[100:] == list(range(101, 151))
    assert len(job_ids) == len(set(job_ids))
    assert payload["counts"]["review"] == 150
    assert payload["next_cursor"] is None


def test_review_inbox_adapter_preserves_cursor_when_visible_limit_stops_midstream() -> None:
    rows = [{"job_id": index} for index in range(1, 151)]

    class Service:
        def list_review_inbox(self, **filters: object) -> dict[str, object]:
            start = int(str(filters.get("cursor") or "0"))
            end = min(start + int(filters["limit"]), len(rows))
            return {
                "items": rows[start:end],
                "counts": {"review": len(rows)},
                "next_cursor": str(end) if end < len(rows) else None,
            }

    payload = tasks._load_review_inbox(  # noqa: SLF001
        Service(),  # type: ignore[arg-type]
        bucket="review",
        account_id="",
        limit=120,
    )

    assert [item["job_id"] for item in payload["items"]] == list(range(1, 121))
    assert payload["next_cursor"] == "120"


def test_review_inbox_adapter_forwards_server_search_across_pages() -> None:
    matching_rows = [{"job_id": index} for index in range(1, 131)]

    class Service:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def list_review_inbox(self, **filters: object) -> dict[str, object]:
            self.calls.append(filters)
            assert filters["search"] == "蓝血研究"
            start = int(str(filters.get("cursor") or "0"))
            end = min(start + int(filters["limit"]), len(matching_rows))
            return {
                "items": matching_rows[start:end],
                "counts": {"review": len(matching_rows)},
                "next_cursor": (
                    str(end) if end < len(matching_rows) else None
                ),
            }

    service = Service()
    payload = tasks._load_review_inbox(  # noqa: SLF001
        service,  # type: ignore[arg-type]
        bucket="review",
        account_id="",
        search="蓝血研究",
        limit=120,
    )

    assert [item["job_id"] for item in payload["items"]] == list(
        range(1, 121)
    )
    assert [call["cursor"] for call in service.calls] == [None, "100"]
    assert all(call["search"] == "蓝血研究" for call in service.calls)


def test_review_inbox_database_filters_before_pagination(tmp_path) -> None:
    db = Database(tmp_path / "review-search.db")

    def add_article(index: int, title: str) -> int:
        batch_id = f"batch-{index}"
        db.create_batch(batch_id, topic=f"话题 {index}")
        job_id = db.create_job(
            topic=f"话题 {index}",
            source="desktop",
            raw_content="原文",
            meta={"batch_id": batch_id},
        )
        db.update_job(
            job_id,
            status="ready_for_review",
            step="inject",
            selected_title=title,
            body="正文",
        )
        db.attach_batch_job(batch_id, job_id, "account-a", "测试公众号")
        return job_id

    target_id = add_article(0, "唯一匹配的蓝血研究文章")
    for index in range(1, 41):
        add_article(index, f"普通文章 {index}")

    first_page = db.list_review_inbox_rows(
        bucket="review",
        limit=30,
        offset=0,
    )
    assert target_id not in {int(item["id"]) for item in first_page}

    searched = db.list_review_inbox_rows(
        bucket="review",
        search="蓝血研究",
        limit=30,
        offset=0,
    )
    assert [int(item["id"]) for item in searched] == [target_id]
    assert db.review_inbox_counts(search="蓝血研究")["review"] == 1
    assert db.has_active_batches() is False


def test_legacy_service_fallback_projects_all_inbox_buckets() -> None:
    today = datetime.now().date().isoformat()
    batches = [
        {
            "id": "batch-1",
            "display_id": "1001",
            "topic": "供应链专题",
            "jobs": [
                {
                    "id": 1,
                    "status": "ready_for_review",
                    "step": "render",
                    "review_status": "needs_changes",
                    "account_id": "a",
                    "account_name": "高优先级号",
                    "selected_title": "待修改",
                },
                {
                    "id": 2,
                    "status": "failed",
                    "step": "inject",
                    "review_status": "write_failed",
                    "account_id": "a",
                    "account_name": "高优先级号",
                    "error": "wechat api failed",
                },
                {
                    "id": 3,
                    "status": "failed",
                    "step": "rewriting",
                    "review_status": "unviewed",
                    "account_id": "b",
                    "account_name": "普通号",
                    "error": "model failed",
                },
                {
                    "id": 5,
                    "status": "ready_for_review",
                    "step": "inject",
                    "review_status": "confirmed",
                    "account_id": "a",
                    "account_name": "高优先级号",
                    "selected_title": "待写入文章",
                },
                {
                    "id": 4,
                    "status": "drafted",
                    "step": "inject",
                    "review_status": "drafted",
                    "account_id": "a",
                    "account_name": "高优先级号",
                    "updated_at": f"{today}T08:00:00+00:00",
                },
            ],
        }
    ]

    review = tasks._fallback_review_inbox(  # noqa: SLF001
        batches,
        bucket="review",
    )
    assert review["counts"] == {
        "review": 1,
        "ready_for_draft": 1,
        "write_failed": 1,
        "generation_failed": 1,
        "today_completed": 1,
    }
    assert review["items"][0]["review_status"] == "needs_changes"
    assert review["items"][0]["priority_reason"] == "已标记需要修改"

    ready_for_draft = tasks._fallback_review_inbox(  # noqa: SLF001
        batches,
        bucket="ready_for_draft",
    )
    assert ready_for_draft["items"][0]["job_id"] == 5

    account_filtered = tasks._fallback_review_inbox(  # noqa: SLF001
        batches,
        bucket="generation_failed",
        account_id="a",
    )
    assert account_filtered["items"] == []
    assert account_filtered["counts"]["generation_failed"] == 0


def test_task_center_uses_one_all_batches_queue() -> None:
    source = inspect.getsource(tasks.build_tasks_panel)

    assert 'initial_view: str = "batches"' in source
    assert source.count('{"batches": "全部批次"}') == 2
    for removed_queue in ("待我处理", "可写草稿"):
        assert f'"{removed_queue}"' not in source
    assert 'runtime["inbox_bucket"]' not in source
    assert "_render_inbox_article_card(" not in source
    assert "_render_batch_card(" in source


def test_batch_queue_fills_the_viewport_before_offering_more() -> None:
    source = inspect.getsource(tasks.build_tasks_panel)
    task_list_css = APP_CSS[APP_CSS.rindex(".ops-task-list {") :]

    assert tasks.TASK_BATCH_PAGE_SIZE == 30
    assert '"visible_limit": TASK_BATCH_PAGE_SIZE' in source
    assert "int(runtime[\"visible_limit\"]) + TASK_BATCH_PAGE_SIZE" in source
    assert 'queue_count_label.set_text(f"{filtered_total} 条")' in source
    assert "overflow-y: auto !important" in task_list_css[:700]
    assert "overscroll-behavior: contain" in task_list_css[:700]


def test_inbox_review_action_opens_the_deep_workbench_directly() -> None:
    source = inspect.getsource(tasks._render_inbox_article_card)  # noqa: SLF001

    assert "尚未评审" in source
    assert "推荐下一步" in source
    assert source.count("open_review_workbench(") == 1
    assert '"打开审核"' in source
    assert '"快速审核"' not in source
    assert '"深度编辑"' not in source


def test_inbox_row_shows_per_article_token_usage_in_gold_without_hiding_it() -> None:
    card_source = inspect.getsource(tasks._render_inbox_article_card)  # noqa: SLF001
    service_source = inspect.getsource(BatchService.list_review_inbox)
    token_css = APP_CSS[APP_CSS.index(".ops-task-row-token {") :]

    assert 'item.get("generation_usage")' in card_source
    assert "_generation_usage_text(" in card_source
    assert 'item.get("generation_token_usage")' in card_source
    assert 'classes("ops-task-row-token")' in card_source
    assert "article_generation_usage(" in service_source
    assert 'item["generation_usage"]' in service_source
    assert 'item["generation_token_usage"]' in service_source
    assert "color: var(--ui-color-warning)" in token_css[:500]
    assert "text-overflow: ellipsis" in token_css[:500]
    assert "@container (max-width: 720px)" in token_css
    narrow_css = token_css[token_css.index("@container (max-width: 720px)") :]
    assert ".ops-task-row-token" in narrow_css
    assert "display: none" not in narrow_css[narrow_css.index(".ops-task-row-token") :][:250]


def test_generation_usage_copy_distinguishes_actual_partial_and_manus_credit() -> None:
    actual, _ = tasks._generation_usage_text(  # noqa: SLF001
        {
            "known_tokens": 23_410,
            "api_call_count": 5,
            "metered_calls": 5,
            "complete": True,
        }
    )
    partial, partial_hint = tasks._generation_usage_text(  # noqa: SLF001
        {
            "known_tokens": 18_920,
            "api_call_count": 5,
            "metered_calls": 4,
            "unavailable_calls": 1,
            "complete": False,
        }
    )
    manus, manus_hint = tasks._generation_usage_text(  # noqa: SLF001
        {
            "api_call_count": 1,
            "metered_calls": 0,
            "unavailable_calls": 1,
            "manus_tasks": 1,
            "provider_credits": 37,
            "credit_metered_calls": 1,
            "complete": False,
        }
    )

    assert actual == "实际 23,410 Token · 5/5"
    assert partial == "已确认 18,920 Token · 4/5"
    assert "不可作为文章总 Token" in partial_hint
    assert manus == "Manus 37 Credits · 无 Token"
    assert "服务商没有提供" in manus_hint


def test_generation_usage_copy_leads_with_estimated_or_charged_points() -> None:
    estimated, estimated_hint = tasks._generation_usage_text(  # noqa: SLF001
        {
            "known_tokens": 9_000,
            "api_call_count": 1,
            "metered_calls": 1,
            "estimated_points": 155,
            "complete": True,
        }
    )
    charged, charged_hint = tasks._generation_usage_text(  # noqa: SLF001
        {
            "known_tokens": 9_000,
            "api_call_count": 1,
            "metered_calls": 1,
            "estimated_points": 155,
            "charged_points": 155,
            "live_pricing": 1,
            "complete": True,
        }
    )

    assert estimated == "预计 155 积分 · 实际 9,000 Token · 1/1"
    assert "不会实际扣除" in estimated_hint
    assert charged == "消耗 155 积分 · 实际 9,000 Token · 1/1"
    assert "已结算的最终积分" in charged_hint


def test_legacy_review_entry_redirects_to_the_full_page_route() -> None:
    source = inspect.getsource(tasks.open_review_workbench)

    redirect = source.index('review_runtime.get("open_review_page")')
    dialog = source.index("with ui.dialog() as dialog")
    assert redirect < dialog
    assert 'review_runtime["review_open"] = True' in source[redirect:dialog]
    assert "open_review_page(batch_id, job_id)" in source[redirect:dialog]
    assert "return" in source[redirect:dialog]


def test_failed_inbox_card_exposes_quick_retry_recovery_options_and_batch() -> None:
    source = inspect.getsource(tasks._render_inbox_article_card)  # noqa: SLF001

    assert 'status == "failed"' in source
    assert '"从失败步骤重试"' in source
    assert "await _retry_job_with_loading(" in source
    assert 'step="auto"' in source
    assert '"恢复选项"' in source
    assert "open_retry_job_dialog(" in source
    assert '"查看所在批次"' in source


def test_retry_helper_forwards_overrides_and_clears_loading(monkeypatch) -> None:
    calls: list[tuple[str, int, dict[str, object]]] = []
    loading: list[bool] = []
    button = object()

    class Service:
        def retry_job(
            self,
            batch_id: str,
            job_id: int,
            **kwargs: object,
        ) -> dict[str, object]:
            calls.append((batch_id, job_id, kwargs))
            return {"accepted": True}

    async def immediate(callback):  # type: ignore[no-untyped-def]
        return callback()

    monkeypatch.setattr(tasks.run, "io_bound", immediate)
    monkeypatch.setattr(
        tasks,
        "set_button_loading",
        lambda active_button, value: (
            active_button is button and loading.append(value)
        ),
    )

    result = asyncio.run(
        tasks._retry_job_with_loading(  # noqa: SLF001
            Service(),  # type: ignore[arg-type]
            "batch-1",
            9,
            button,
            step="rewrite",
            model_id="model-2",
            source_url="https://example.com/replacement",
            raw_content="替换原文",
        )
    )

    assert result == {"accepted": True}
    assert calls == [
        (
            "batch-1",
            9,
            {
                "step": "rewrite",
                "model_id": "model-2",
                "source_url": "https://example.com/replacement",
                "raw_content": "替换原文",
            },
        )
    ]
    assert loading == [True, False]


def test_retry_dialog_supports_steps_model_and_replacement_inputs() -> None:
    source = inspect.getsource(tasks.open_retry_job_dialog)

    for step in (
        "auto",
        "ingest",
        "rewrite",
        "title_optimize",
        "render",
        "images",
        "inject",
    ):
        assert f'"{step}"' in source
    assert "state.model_options(include_default=False)" in source
    assert '"临时文本模型（可选）"' in source
    assert '"替换来源链接（可选）"' in source
    assert '"粘贴替换原文（可选）"' in source
    assert "await _retry_job_with_loading(" in source


def test_review_workbench_opens_directly_in_deep_edit_without_mode_switch() -> None:
    signature = inspect.signature(tasks.open_review_workbench)
    assert "initial_mode" not in signature.parameters

    mode_source = inspect.getsource(tasks.open_review_workbench)
    assert "ui.toggle(" not in mode_source
    assert "review-mode-toggle" not in mode_source
    assert "apply_deep_review_mode()" in mode_source
    assert "deep_review_controls" in mode_source
    assert "control.set_visibility(True)" in mode_source
    assert "render_quick_review_summary()" in mode_source
    assert "尚未进行 AI 评审" in mode_source
    assert "手动开始 AI 评审" not in mode_source
    assert "_quick_review_action(" not in mode_source
    assert '"查看完整评审"' not in mode_source
    assert "review_jury_actions.update(build_review_jury_panel(" in mode_source
    assert "await reveal_result()" in mode_source
    assert "await reveal_settings()" not in mode_source
    assert mode_source.index("quick_summary_host =") < mode_source.index(
        "title_choice ="
    )
    assert "当前封面" in mode_source
    assert "阻断摘要" in mode_source

    start = mode_source.index("def apply_deep_review_mode")
    end = mode_source.index('ui.element("div")', start)
    mode_action = mode_source[start:end]
    assert "set_visibility" in mode_action
    assert ".clear(" not in mode_action
    assert "dialog.close" not in mode_action
    assert "open_review_workbench" not in mode_action


def test_quick_review_uses_phone_viewport_and_loads_material_cover_preview() -> None:
    source = inspect.getsource(tasks.open_review_workbench)
    styles = STYLES.read_text(encoding="utf-8")

    assert '"preview-frame review-phone-preview w-full"' in source
    assert "width: 375px;" in styles
    assert ".review-phone-preview .article-preview" in styles
    assert "load_selected_cover_preview" in source
    assert "service.list_cover_options(" in source
    assert "wechat_image_proxy_url(" in source
    assert "正在读取封面缩略图" in source


def test_task_center_exposes_background_generation_and_review_progress() -> None:
    source = inspect.getsource(tasks.build_tasks_panel)
    styles = STYLES.read_text(encoding="utf-8")

    assert "background-activity-dock" in source
    assert "start_background_review" in source
    assert "AI 正在评审文章" in source
    assert "editorial_review_progress(review)" in source
    assert 'review_progress["stage"]' in source
    assert 'review_progress["value"]' in source
    assert "后台生成中" in source
    assert "查看详情" in source
    assert "ui.linear_progress(" in source
    assert "show_value=False" in source
    assert "progress_text" in source
    assert ".background-activity-dock" in styles
    assert ".background-activity-progress-label" in styles


def test_background_progress_is_formatted_as_a_clamped_percentage() -> None:
    assert tasks._format_progress(0.55) == (0.55, "55%")  # noqa: SLF001
    assert tasks._format_progress(0.75) == (0.75, "75%")  # noqa: SLF001
    assert tasks._format_progress(2) == (1.0, "100%")  # noqa: SLF001
    assert tasks._format_progress(-1) == (0.0, "0%")  # noqa: SLF001
    assert tasks._format_progress("invalid") == (0.0, "0%")  # noqa: SLF001


def test_structured_failure_actions_have_only_known_operator_handlers() -> None:
    source = inspect.getsource(tasks._render_inbox_article_card)  # noqa: SLF001

    for action in (
        "replace_url",
        "paste_text",
        "retry_step",
        "retry_inject",
        "open_account_settings",
        "open_template_settings",
        "open_relay_settings",
        "show_ip_whitelist_guide",
        "check_relay",
        "reconcile_draft",
        "change_model",
        "copy_error",
    ):
        assert f'"{action}"' in source
    assert "visible_actions" in source
    assert "action for action in failure_actions if action in known_actions" in source
    assert "force_wechat_check=True" in source
    assert "已提交安全对账" in source


def test_failure_action_retry_step_maps_stages_without_whitelisting_unknowns() -> None:
    assert tasks._failure_action_retry_step(  # noqa: SLF001
        "retry_rewrite", {}
    ) == "rewrite"
    assert tasks._failure_action_retry_step(  # noqa: SLF001
        "retry_step", {"stage": "title_optimize"}
    ) == "title_optimize"
    assert tasks._failure_action_retry_step(  # noqa: SLF001
        "retry_step", {"stage": "unexpected"}
    ) == "auto"
    assert tasks._failure_action_retry_step(  # noqa: SLF001
        "unknown_action", {"stage": "rewrite"}
    ) is None


def test_batch_failed_retry_uses_in_place_job_recovery() -> None:
    source = inspect.getsource(tasks._render_batch_detail_content)  # noqa: SLF001
    helper = inspect.getsource(tasks._submit_failed_job_retries)  # noqa: SLF001

    assert "_submit_failed_job_retries(" in source
    assert "service.retry_job(" in helper
    assert 'step="auto"' in helper
    assert "service.retry_failed(" not in source
    assert "_set_retry_loading_safely(" in source
    assert "已按失败步骤原地恢复" in source

    class Service:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, str]] = []

        def retry_job(self, batch_id: str, job_id: int, *, step: str) -> None:
            self.calls.append((batch_id, job_id, step))
            if job_id == 2:
                raise RuntimeError("模型繁忙")

    service = Service()
    accepted, errors = tasks._submit_failed_job_retries(  # noqa: SLF001
        service,  # type: ignore[arg-type]
        "batch-a",
        [
            {"id": 1, "account_name": "公众号 A"},
            {"id": 2, "account_name": "公众号 B"},
        ],
    )
    assert accepted == 1
    assert errors == ["公众号 B：模型繁忙"]
    assert service.calls == [
        ("batch-a", 1, "auto"),
        ("batch-a", 2, "auto"),
    ]


def test_quick_review_separates_blockers_from_reminders() -> None:
    source = inspect.getsource(tasks.open_review_workbench)
    start = source.index("def render_quick_review_summary")
    end = source.index("\n        render_quick_review_summary()", start)
    summary = source[start:end]

    assert 'quality.get("errors")' in summary
    assert 'quality.get("warnings")' in summary
    assert '"阻断摘要："' in summary
    assert '"提醒摘要："' in summary


def test_deep_footer_keeps_edit_actions_and_hides_the_duplicate_quick_action() -> None:
    source = inspect.getsource(tasks.open_review_workbench)

    assert 'needs_changes_btn = ui.button(' in source
    assert '"需要修改"' in source
    assert '"确认此文章"' in source
    assert "deep_review_actions.extend((save_btn, needs_changes_btn))" in source
    assert "control.set_visibility(True)" in source
    assert "control.set_visibility(False)" in source


def test_confirmation_gate_blocks_running_and_open_risks() -> None:
    assert tasks._review_confirmation_gate(None) == ("", 0)  # noqa: SLF001
    assert tasks._review_confirmation_gate(  # noqa: SLF001
        {"status": "running", "blocking_count": 0}
    ) == ("AI 评审仍在进行中", 0)
    assert tasks._review_confirmation_gate(  # noqa: SLF001
        {"status": "completed", "blocking_count": 2}
    ) == ("AI 评审仍有 2 个阻断项待处理", 2)
    assert tasks._review_confirmation_gate(  # noqa: SLF001
        {"status": "candidate_ready", "blocking_count": 0}
    ) == ("AI 改写稿已生成，请先选择使用原文还是改写稿", 0)


def test_quick_review_summary_omits_duplicate_review_action_buttons() -> None:
    source = inspect.getsource(tasks.open_review_workbench)

    assert 'review_jury_actions.get("settings_summary")' in source
    assert '"调整设置"' not in source
    assert "on_click=start_initial_review_from_quick" not in source
    assert "on_click=reveal_review_settings" not in source
    assert 'review_jury_actions.get("reveal_comparison")' not in source
    assert '"当前版本：AI 改写后"' in source
    assert '"待选择：保留原文或采用 AI 改写稿"' in source
    assert '"已选择：保留改写前原文"' in source
    assert '"AI 改写后又有人工编辑"' in source
    assert "rewrite_matches_editor" in source
    assert "on_click=reveal_article_comparison" not in source


def test_confirm_rechecks_gate_before_invoking_confirmation_endpoint() -> None:
    source = inspect.getsource(tasks.open_review_workbench)
    start = source.index("async def confirm()")
    end = source.index("\n        def needs_changes", start)
    confirm_source = source[start:end]

    assert "service.list_editorial_reviews(" in confirm_source
    assert "if reason:" in confirm_source
    assert "await reveal_deep_review()" in confirm_source
    assert confirm_source.index("if reason:") < confirm_source.index(
        "service.confirm_job("
    )


def test_blocking_footer_is_disabled_with_an_adjacent_processing_action() -> None:
    source = inspect.getsource(tasks.open_review_workbench)
    start = source.index("def sync_confirm_gate")
    end = source.index("async def confirm()", start)
    gate_source = source[start:end]

    assert 'confirm_btn.set_text(f"先处理 {blocking_count} 个阻断项")' in gate_source
    assert "confirm_btn.disable()" in gate_source
    assert "go_process_btn.set_visibility(True)" in gate_source
    assert 'confirm_btn.set_text("确认此文章")' in gate_source
    assert "confirm_btn.enable()" in gate_source
    assert '"去处理"' in source
    assert "on_review_updated=handle_review_updated" in source


def test_ai_rewrite_names_state_their_scope_and_footer_has_clearance() -> None:
    source = inspect.getsource(tasks.open_review_workbench)
    styles = STYLES.read_text(encoding="utf-8")

    assert '"AI 定点改写（单段）"' in source
    assert '"按要求改写所选段落"' in source
    assert 'classes("review-action-spacer")' in source
    assert ".review-action-spacer" in styles
    assert "safe-area-inset-bottom" in styles


def test_needs_changes_keeps_the_deep_workbench_open() -> None:
    source = inspect.getsource(tasks.open_review_workbench)
    start = source.index("def needs_changes")
    end = source.index("def apply_deep_review_mode", start)
    action = source[start:end]

    assert "service.request_job_changes" in action
    assert "apply_deep_review_mode()" in action
    assert "dialog.close" not in action
    assert "open_review_workbench" not in action
    assert "on_change" not in action


def test_account_editor_persists_and_displays_review_priority() -> None:
    source = DESKTOP.read_text(encoding="utf-8")

    assert '"高优先级公众号"' in source
    assert 'saved_record["review_priority"]' in source
    assert "state.db.upsert_official_account(saved_record)" in source
    assert '"审核优先"' in source
