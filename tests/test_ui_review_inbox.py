from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from pathlib import Path

from app.db import Database
from app.ui.panels import tasks


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "app" / "ui" / "desktop.py"
STYLES = ROOT / "app" / "ui" / "styles.py"


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


def test_legacy_service_fallback_projects_all_four_inbox_buckets() -> None:
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
        "write_failed": 1,
        "generation_failed": 1,
        "today_completed": 1,
    }
    assert review["items"][0]["review_status"] == "needs_changes"
    assert review["items"][0]["priority_reason"] == "已标记需要修改"

    account_filtered = tasks._fallback_review_inbox(  # noqa: SLF001
        batches,
        bucket="generation_failed",
        account_id="a",
    )
    assert account_filtered["items"] == []
    assert account_filtered["counts"]["generation_failed"] == 0


def test_task_center_defaults_to_inbox_and_exposes_four_counts() -> None:
    source = inspect.getsource(tasks.build_tasks_panel)

    assert '"inbox": "待处理收件箱"' in source
    assert 'value="inbox"' in source
    assert 'runtime["inbox_bucket"]' in source
    assert "INBOX_BUCKETS.items()" in source
    assert "_render_inbox_article_card(" in source
    assert "_render_batch_card(" in source


def test_inbox_render_uses_server_search_without_loading_all_batches() -> None:
    source = inspect.getsource(tasks.build_tasks_panel)
    inbox_start = source.index('if view_mode == "inbox":')
    inbox_end = source.index("\n        batches = service.list_batches(", inbox_start)
    inbox_branch = source[inbox_start:inbox_end]

    assert "service.list_batches(" not in inbox_branch
    assert "has_active_batches" in inbox_branch
    assert "search=str(search_in.value or \"\")" in inbox_branch
    assert "needle =" not in inbox_branch


def test_inbox_review_actions_share_one_workbench_with_explicit_modes() -> None:
    source = inspect.getsource(tasks._render_inbox_article_card)  # noqa: SLF001

    assert "尚未评审" in source
    assert "推荐下一步" in source
    assert source.count("open_review_workbench(") == 2
    assert 'initial_mode="quick"' in source
    assert 'initial_mode="deep"' in source


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


def test_review_workbench_is_quick_by_default_and_deep_edit_is_in_place() -> None:
    signature = inspect.signature(tasks.open_review_workbench)
    assert signature.parameters["initial_mode"].default == "quick"

    mode_source = inspect.getsource(tasks.open_review_workbench)
    assert '"quick": "快速审核"' in mode_source
    assert '"deep": "深度编辑"' in mode_source
    assert "deep_review_controls" in mode_source
    assert "control.set_visibility(show_deep_editor)" in mode_source
    assert "render_quick_review_summary()" in mode_source
    assert "尚未进行 AI 评审" in mode_source
    assert "手动开始 AI 评审" in mode_source
    assert "当前封面" in mode_source
    assert "阻断摘要" in mode_source

    start = mode_source.index("def apply_review_mode")
    end = mode_source.index("def switch_review_mode", start)
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
    source = inspect.getsource(tasks._render_batch_card)  # noqa: SLF001
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


def test_quick_footer_has_direct_decisions_and_deep_only_edit_actions() -> None:
    source = inspect.getsource(tasks.open_review_workbench)

    assert 'needs_changes_btn = ui.button(' in source
    assert '"需要修改"' in source
    assert '"确认此文章"' in source
    assert "deep_review_actions.extend((more_btn, save_btn))" in source
    assert "quick_review_actions.append(needs_changes_btn)" in source
    assert "control.set_visibility(show_deep_editor)" in source
    assert "control.set_visibility(not show_deep_editor)" in source


def test_needs_changes_keeps_workbench_open_and_reveals_deep_editor() -> None:
    source = inspect.getsource(tasks.open_review_workbench)
    start = source.index("def needs_changes")
    end = source.index("def apply_review_mode", start)
    action = source[start:end]

    assert "service.request_job_changes" in action
    assert 'review_mode.value = "deep"' in action
    assert 'apply_review_mode("deep")' in action
    assert "dialog.close" not in action
    assert "open_review_workbench" not in action
    assert "on_change" not in action


def test_account_editor_persists_and_displays_review_priority() -> None:
    source = DESKTOP.read_text(encoding="utf-8")

    assert '"高优先级公众号"' in source
    assert 'saved_record["review_priority"]' in source
    assert "state.db.upsert_official_account(saved_record)" in source
    assert '"审核优先"' in source
