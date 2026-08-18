from __future__ import annotations

import threading

import pytest

from app.config import load_config
from app.services.batches import BatchService
from app.services.model_readiness import active_model_auth_failure_ids


def _service_with_ready_job(tmp_path) -> tuple[BatchService, str, int]:
    config = load_config()
    config = {**config, "_db_path": str(tmp_path / "review.db")}
    service = BatchService(config)
    batch_id = "batch-review-1"
    service.db.create_batch(
        batch_id,
        topic="测试批次",
        source_url="https://example.com/article",
    )
    job_id = service.db.create_job(
        topic="测试批次",
        source="test",
        source_url="https://example.com/article",
        meta={
            "official_account_id": "account-a",
            "official_account_name": "公众号A",
            "selected_model_name": "Kimi",
        },
    )
    service.db.update_job(
        job_id,
        status="ready_for_review",
        step="inject",
        body="正文" * 1200,
        titles_json=["候选标题一", "候选标题二"],
        title_candidates_json=["候选标题一", "候选标题二"],
        selected_title="候选标题一",
    )
    service.db.attach_batch_job(batch_id, job_id, "account-a", "公众号A")
    service.db.update_batch(batch_id, status="ready_for_review")
    return service, batch_id, job_id


def test_title_selection_does_not_implicitly_confirm(tmp_path) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    selected = service.select_job(batch_id, job_id, title_index=1)
    assert selected["selected_title"] == "候选标题二"
    assert selected["review_status"] == "viewed"
    assert service.get_batch(batch_id)["progress"]["confirmed"] == 0


def test_explicit_confirmation_is_persisted_in_batch(tmp_path) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    notifications: list[dict] = []
    service.add_listener(lambda batch: notifications.append(batch))
    service.mark_job_viewed(batch_id, job_id)
    confirmed = service.confirm_job(batch_id, job_id)
    assert confirmed["review_status"] == "confirmed"
    progress = service.get_batch(batch_id)["progress"]
    assert progress["confirmed"] == 1
    assert progress["ready_for_draft"] == 1
    assert progress["unconfirmed"] == 0
    assert service.get_batch(batch_id)["status"] == "ready_for_draft"
    assert notifications[-1]["status"] == "ready_for_draft"

    service.request_job_changes(batch_id, job_id)
    assert service.get_batch(batch_id)["status"] == "ready_for_review"
    assert notifications[-1]["status"] == "ready_for_review"


def test_unviewed_job_cannot_be_confirmed(tmp_path) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    with pytest.raises(ValueError, match="先打开并查看文章"):
        service.confirm_job(batch_id, job_id)

    service.mark_job_viewed(batch_id, job_id)
    assert service.confirm_job(batch_id, job_id)["review_status"] == "confirmed"
    # Repeated confirmation is safe and idempotent.
    assert service.confirm_job(batch_id, job_id)["review_status"] == "confirmed"


def test_needs_changes_job_can_be_confirmed_without_fake_save(tmp_path) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    service.mark_job_viewed(batch_id, job_id)
    service.request_job_changes(batch_id, job_id)

    confirmed = service.confirm_job(batch_id, job_id)

    assert confirmed["review_status"] == "confirmed"
    assert service.get_batch(batch_id)["progress"]["ready_for_draft"] == 1


def test_cancel_preserves_terminal_review_jobs(tmp_path) -> None:
    service, batch_id, ready_job_id = _service_with_ready_job(tmp_path)
    active_job_id = service.db.create_job(
        topic="测试批次",
        source="test",
        source_url="https://example.com/article",
        meta={
            "official_account_id": "account-b",
            "official_account_name": "公众号B",
        },
    )
    service.db.update_job(active_job_id, status="rewriting", step="rewrite")
    service.db.attach_batch_job(batch_id, active_job_id, "account-b", "公众号B")
    service.db.update_batch(batch_id, status="processing")

    cancelled = service.cancel_batch(batch_id)
    statuses = {int(job["id"]): str(job["status"]) for job in cancelled["jobs"]}
    assert statuses[ready_job_id] == "ready_for_review"
    assert statuses[active_job_id] == "cancelled"


def test_batch_write_rejects_unconfirmed_articles(tmp_path) -> None:
    service, batch_id, _job_id = _service_with_ready_job(tmp_path)
    try:
        service.inject_batch(batch_id)
    except ValueError as exc:
        assert "未显式确认" in str(exc)
    else:
        raise AssertionError("unconfirmed batch must not be written")


def test_batch_write_preserves_confirmed_manual_title_without_title_index(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    manual_title = "运营人员最终确认的手工标题"
    service.db.update_job(job_id, selected_title=manual_title)
    service.mark_job_viewed(batch_id, job_id)
    service.confirm_job(batch_id, job_id)
    calls: list[tuple[int, dict]] = []

    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda *_args, **_kwargs: (
            {"_db_path": str(tmp_path / "review.db")},
            {"model_id": "text-model"},
        ),
    )

    class FakePipeline:
        def __init__(self, _config) -> None:
            pass

        def review_and_inject(self, selected_job_id: int, **kwargs) -> None:
            calls.append((selected_job_id, kwargs))
            service.db.update_job(selected_job_id, status="drafted", step="inject")

    monkeypatch.setattr("app.services.batches.Pipeline", FakePipeline)

    result = service.inject_batch(batch_id)

    assert calls == [(job_id, {})]
    assert service.db.get_job(job_id)["selected_title"] == manual_title
    assert result["jobs"][0]["selected_title"] == manual_title


def test_batch_write_rejects_concurrent_duplicate_request(tmp_path, monkeypatch) -> None:
    config = {**load_config(), "_db_path": str(tmp_path / "inject-lock.db")}
    first = BatchService(config)
    second = BatchService(config)
    entered = threading.Event()
    release = threading.Event()

    def slow_inject(_batch_id: str) -> dict[str, str]:
        entered.set()
        assert release.wait(timeout=3)
        return {"id": "batch-1"}

    monkeypatch.setattr(first, "_inject_batch_locked", slow_inject)
    worker = threading.Thread(target=lambda: first.inject_batch("batch-1"))
    worker.start()
    assert entered.wait(timeout=3)
    try:
        with pytest.raises(ValueError, match="正在写入草稿箱"):
            second.inject_batch("batch-1")
    finally:
        release.set()
        worker.join(timeout=3)


def test_archive_hides_batch_from_default_task_center(tmp_path) -> None:
    service, batch_id, _job_id = _service_with_ready_job(tmp_path)
    assert any(item["id"] == batch_id for item in service.list_batches())
    service.archive_batch(batch_id)
    assert not any(item["id"] == batch_id for item in service.list_batches())
    assert any(
        item["id"] == batch_id
        for item in service.list_batches(include_archived=True)
    )


def test_body_edit_invalidates_existing_inline_images(tmp_path) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    service.db.update_job(
        job_id,
        html_content="<p>旧正文</p><section data-inline-image-id=\"1\"></section>",
        meta_json={
            "official_account_id": "account-a",
            "inline_images_resolved": True,
            "inline_images": [{"index": 1, "url": "https://example.com/old.jpg"}],
            "inline_image_warnings": ["旧提示"],
        },
    )
    updated = service.update_job_content(batch_id, job_id, body="修改后的正文" * 100)
    assert updated["html_content"] == ""
    assert updated["meta"]["inline_images_resolved"] is False
    assert updated["meta"]["inline_images"] == []
    assert updated["meta"]["inline_image_warnings"] == []


def test_unchanged_body_does_not_invalidate_reviewed_images(tmp_path) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    original = "正文" * 1200
    service.db.update_job(
        job_id,
        body=original,
        html_content='<p>正文</p><section data-inline-image-id="1"></section>',
        meta_json={
            "official_account_id": "account-a",
            "inline_images_resolved": True,
            "inline_images": [{"index": 1, "url": "https://example.com/kept.jpg"}],
        },
    )

    updated = service.update_job_content(batch_id, job_id, body=original)

    assert updated["html_content"]
    assert updated["meta"]["inline_images_resolved"] is True
    assert updated["meta"]["inline_images"][0]["url"].endswith("kept.jpg")


def test_paragraph_revision_uses_context_saves_version_and_keeps_images(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    body = "上一段用于衔接。\n\n目标段包含关键数据32%。\n\n下一段给出行动建议。"
    service.db.update_job(
        job_id,
        body=body,
        html_content="<p>旧预览</p>",
        meta_json={
            "official_account_id": "account-a",
            "inline_images_resolved": True,
            "inline_images": [
                {
                    "index": 1,
                    "anchor": "目标段包含关键数据32%。",
                    "url": "https://example.com/kept.jpg",
                }
            ],
        },
    )
    captured: dict[str, str] = {}

    class FakeClient:
        def complete(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return "修改后的目标段仍保留关键数据32%，表达更加克制。"

    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda *_args, **_kwargs: (
            {"ai": {"rewrite_prompt": "公众号写作规则唯一标记"}},
            {"model_id": "text-model"},
        ),
    )
    monkeypatch.setattr(
        "app.services.batches.build_text_client", lambda *_args, **_kwargs: FakeClient()
    )

    def fake_rerender(selected_batch_id: str, selected_job_id: int) -> dict:
        service.db.update_job(selected_job_id, html_content="<p>新预览</p>")
        service.db.update_batch_job_review(selected_batch_id, selected_job_id, "viewed")
        return service._public_job(  # noqa: SLF001
            service._batch_job(selected_batch_id, selected_job_id),  # noqa: SLF001
            include_content=True,
        )

    monkeypatch.setattr(service, "rerender_job", fake_rerender)

    updated = service.regenerate_paragraph(
        batch_id,
        job_id,
        1,
        instruction="语气更克制，但保留数字",
    )

    assert updated["body"].split("\n\n") == [
        "上一段用于衔接。",
        "修改后的目标段仍保留关键数据32%，表达更加克制。",
        "下一段给出行动建议。",
    ]
    assert "上一段用于衔接" in captured["prompt"]
    assert "下一段给出行动建议" in captured["prompt"]
    assert "公众号写作规则唯一标记" in captured["prompt"]
    assert "语气更克制" in captured["prompt"]
    assert updated["meta"]["inline_images"][0]["url"].endswith("kept.jpg")
    assert "修改后的目标段" in updated["meta"]["inline_images"][0]["anchor"]
    versions = service.list_job_versions(batch_id, job_id)
    assert versions[0]["body"] == body
    assert versions[0]["has_visual_snapshot"] is True


def test_paragraph_model_auth_failure_invalidates_current_model_readiness(
    tmp_path,
    monkeypatch,
) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    model_id = "text-model-auth-failure"
    service.db.upsert_ai_model(
        {
            "id": model_id,
            "name": "失效文本模型",
            "provider_type": "openai_compatible",
            "api_base": "https://model.example.test/v1",
            "model": "chat",
            "api_key_encrypted": "encrypted-key-fingerprint",
            "enabled": True,
        }
    )
    cfg = {
        "_db_path": str(service.db.path),
        "ai": {"rewrite_prompt": "公众号规则"},
    }

    class UnauthorizedClient:
        def complete(self, _prompt: str) -> str:
            raise RuntimeError("HTTP 401 unauthorized")

    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda *_args, **_kwargs: (cfg, {"model_id": model_id}),
    )
    monkeypatch.setattr(
        "app.services.batches.build_text_client",
        lambda *_args, **_kwargs: UnauthorizedClient(),
    )

    with pytest.raises(RuntimeError, match="401"):
        service.regenerate_paragraph(
            batch_id,
            job_id,
            0,
            instruction="让表达更简洁",
        )

    assert active_model_auth_failure_ids(
        service.db,
        cfg,
    ) == {model_id}


def test_single_image_revision_only_replaces_selected_asset(tmp_path, monkeypatch) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    old_assets = [
        {
            "index": 1,
            "anchor": "论点一收束段",
            "caption": "论点一",
            "prompt": "旧提示一",
            "url": "https://example.com/one.jpg",
        },
        {
            "index": 2,
            "anchor": "论点二收束段",
            "caption": "论点二",
            "prompt": "旧提示二",
            "url": "https://example.com/two.jpg",
        },
    ]
    service.db.update_job(
        job_id,
        html_content='<section data-inline-image-id="1"></section><section data-inline-image-id="2"></section>',
        meta_json={
            "official_account_id": "account-a",
            "inline_images_resolved": True,
            "inline_images": old_assets,
        },
    )
    service.db.upsert_ai_model(
        {
            "id": "image-model",
            "name": "测试生图",
            "provider_type": "image_minimax",
            "api_base": "https://example.com/image",
            "model": "image-01",
            "api_key_encrypted": "encrypted",
            "enabled": True,
        }
    )
    cfg = {
        "_root": str(tmp_path),
        "inline_images": {"enabled": True, "image_model_id": "image-model"},
    }
    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda *_args, **_kwargs: (cfg, {"model_id": "text-model"}),
    )
    calls: list[dict] = []

    def fake_regenerate(**kwargs):
        calls.append(kwargs)
        return {
            **kwargs["asset"],
            "url": "https://example.com/two-revised.jpg",
            "revision_instruction": kwargs["instruction"],
            "revision_count": 1,
        }

    monkeypatch.setattr(
        "app.services.batches.regenerate_inline_image_asset", fake_regenerate
    )
    render_options: list[dict] = []

    class FakePipeline:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def _wechat_client(self):
            return object()

        def run_job(self, selected_job_id: int, **kwargs) -> None:
            render_options.append(kwargs)
            service.db.update_job(
                selected_job_id,
                status="ready_for_review",
                step="inject",
                html_content="<p>重新排版后的最终预览</p>",
            )

    monkeypatch.setattr("app.services.batches.Pipeline", FakePipeline)

    updated = service.regenerate_inline_image(
        batch_id,
        job_id,
        2,
        instruction="改成供应链仓库现场，突出库存积压",
    )

    assert len(calls) == 1
    assert calls[0]["asset"]["index"] == 2
    assets = updated["meta"]["inline_images"]
    assert assets[0] == old_assets[0]
    assert assets[1]["url"].endswith("two-revised.jpg")
    assert assets[1]["anchor"] == "论点二收束段"
    assert updated["review_status"] == "viewed"
    assert render_options[0]["attempt_stage_overrides"] == {
        "render": "images"
    }
    assert render_options[0]["attempt_model_ids"] == {
        "images": "image-model"
    }
    version = service.db.get_job_version(job_id, service.list_job_versions(batch_id, job_id)[0]["id"])
    assert "two.jpg" in str(version["meta_json"])


def test_visual_version_restores_inline_images_and_cover(tmp_path) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    old_meta = {
        "official_account_id": "account-a",
        "inline_images_resolved": True,
        "inline_images": [{"index": 1, "url": "https://example.com/old.jpg"}],
        "generated_cover_active": True,
        "generated_cover": {"url": "https://example.com/old-cover.jpg"},
    }
    service.db.update_job(
        job_id,
        html_content="<p>旧成品</p>",
        thumb_media_id="old-cover-media",
        meta_json=old_meta,
    )
    version_id = service.db.save_job_version(job_id, reason="视觉修改前")
    service.db.update_job(
        job_id,
        html_content="<p>新成品</p>",
        thumb_media_id="new-cover-media",
        meta_json={
            **old_meta,
            "inline_images": [{"index": 1, "url": "https://example.com/new.jpg"}],
        },
    )

    restored = service.restore_job_version(batch_id, job_id, version_id)

    assert restored["html_content"] == "<p>旧成品</p>"
    assert restored["thumb_media_id"] == "old-cover-media"
    assert restored["meta"]["inline_images"][0]["url"].endswith("old.jpg")
    assert restored["meta"]["generated_cover"]["url"].endswith("old-cover.jpg")


def test_move_paragraph_saves_version_and_rerenders(tmp_path, monkeypatch) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    original_body = "第一段\n\n第二段\n\n第三段"
    service.db.update_job(job_id, body=original_body, html_content="<p>旧排版</p>")
    rerendered: list[tuple[str, int]] = []

    def fake_rerender(selected_batch_id: str, selected_job_id: int) -> dict:
        rerendered.append((selected_batch_id, selected_job_id))
        return service._public_job(  # noqa: SLF001 - service boundary test
            service._batch_job(selected_batch_id, selected_job_id),  # noqa: SLF001
            include_content=True,
        )

    monkeypatch.setattr(service, "rerender_job", fake_rerender)
    moved = service.move_paragraph(batch_id, job_id, 0, 2)

    assert moved["body"] == "第二段\n\n第三段\n\n第一段"
    assert moved["html_content"] == ""
    assert moved["review_status"] == "viewed"
    assert rerendered == [(batch_id, job_id)]
    versions = service.list_job_versions(batch_id, job_id)
    assert versions[0]["body"] == original_body


def test_delete_paragraph_saves_version_and_rerenders(tmp_path, monkeypatch) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    original_body = "第一段\n\n第二段\n\n第三段"
    service.db.update_job(job_id, body=original_body)
    rerendered: list[tuple[str, int]] = []

    def fake_rerender(selected_batch_id: str, selected_job_id: int) -> dict:
        rerendered.append((selected_batch_id, selected_job_id))
        return service._public_job(  # noqa: SLF001 - service boundary test
            service._batch_job(selected_batch_id, selected_job_id),  # noqa: SLF001
            include_content=True,
        )

    monkeypatch.setattr(service, "rerender_job", fake_rerender)
    updated = service.delete_paragraph(batch_id, job_id, 1)

    assert updated["body"] == "第一段\n\n第三段"
    assert updated["review_status"] == "viewed"
    assert rerendered == [(batch_id, job_id)]
    versions = service.list_job_versions(batch_id, job_id)
    assert versions[0]["body"] == original_body


@pytest.mark.parametrize(
    ("operation", "args", "message"),
    [
        ("move", (-1, 1), "所选段落不存在"),
        ("move", (0, 3), "目标段落不存在"),
        ("move", (1, 1), "目标位置不能相同"),
        ("delete", (3,), "所选段落不存在"),
    ],
)
def test_paragraph_edit_validates_indexes(
    tmp_path, operation: str, args: tuple[int, ...], message: str
) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    service.db.update_job(job_id, body="第一段\n\n第二段\n\n第三段")

    with pytest.raises(ValueError, match=message):
        if operation == "move":
            service.move_paragraph(batch_id, job_id, args[0], args[1])
        else:
            service.delete_paragraph(batch_id, job_id, args[0])


def test_delete_paragraph_keeps_at_least_one_paragraph(tmp_path) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    service.db.update_job(job_id, body="唯一段落")

    with pytest.raises(ValueError, match="至少需要保留一个正文段落"):
        service.delete_paragraph(batch_id, job_id, 0)


def _configure_image_revision(
    service: BatchService, tmp_path, monkeypatch
) -> dict:
    service.db.upsert_ai_model(
        {
            "id": "revision-image-model",
            "name": "Revision image model",
            "provider_type": "image_minimax",
            "api_base": "https://example.com/image",
            "model": "image-01",
            "api_key_encrypted": "encrypted",
            "enabled": True,
        }
    )
    cfg = {
        "_root": str(tmp_path),
        "_db_path": str(tmp_path / "review.db"),
        "inline_images": {
            "enabled": True,
            "source_mode": "generate",
            "generate_cover": True,
            "image_model_id": "revision-image-model",
        },
    }
    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda *_args, **_kwargs: (cfg, {"model_id": "text-model"}),
    )
    return cfg


def test_cover_revision_failure_restores_previous_visual_state(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    _configure_image_revision(service, tmp_path, monkeypatch)
    original_html = '<section><img src="https://example.com/old-cover.jpg"></section>'
    original_meta = {
        "official_account_id": "account-a",
        "generated_cover_active": True,
        "generated_cover": {
            "url": "https://example.com/old-cover.jpg",
            "media_id": "old-cover-media",
        },
        "layout_quality": {"image_count": 1, "warnings": []},
    }
    service.db.update_job(
        job_id,
        html_content=original_html,
        thumb_media_id="old-cover-media",
        meta_json=original_meta,
    )
    service.db.update_batch_job_review(batch_id, job_id, "confirmed")

    class FailingPipeline:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run_job(self, selected_job_id: int, **_kwargs) -> None:
            service.db.update_job(
                selected_job_id,
                status="failed",
                step="render",
                error="cover provider unavailable",
                html_content="<p>partially rendered replacement</p>",
                thumb_media_id="partial-cover-media",
                meta_json={
                    "generated_cover_active": False,
                    "cover_image_warning": "generation failed",
                },
            )
            raise RuntimeError("cover provider unavailable")

    monkeypatch.setattr("app.services.batches.Pipeline", FailingPipeline)

    with pytest.raises(RuntimeError, match="cover provider unavailable"):
        service.regenerate_cover(
            batch_id,
            job_id,
            instruction="Use a documentary-style management scene",
        )

    restored = service._batch_job(batch_id, job_id)  # noqa: SLF001
    assert restored["status"] == "ready_for_review"
    assert restored["step"] == "inject"
    assert restored["error"] is None
    assert restored["review_status"] == "confirmed"
    assert restored["html_content"] == original_html
    assert restored["thumb_media_id"] == "old-cover-media"
    assert restored["meta"] == original_meta


def test_remove_inline_image_updates_layout_quality_count(tmp_path) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    html = (
        '<p>Argument one</p><section data-inline-image-id="1">'
        '<img src="https://example.com/one.jpg"></section>'
        '<p>Argument two</p><section data-inline-image-id="2">'
        '<img src="https://example.com/two.jpg"></section>'
    )
    service.db.update_job(
        job_id,
        html_content=html,
        meta_json={
            "official_account_id": "account-a",
            "inline_images_resolved": True,
            "inline_images": [
                {"index": 1, "url": "https://example.com/one.jpg"},
                {"index": 2, "url": "https://example.com/two.jpg"},
            ],
            "layout_quality": {
                "errors": [],
                "warnings": [],
                "paragraph_count": 2,
                "image_count": 2,
                "long_paragraph_count": 0,
            },
        },
    )

    updated = service.remove_inline_image(batch_id, job_id, 1)

    assert 'data-inline-image-id="1"' not in updated["html_content"]
    assert 'data-inline-image-id="2"' in updated["html_content"]
    assert [asset["index"] for asset in updated["meta"]["inline_images"]] == [2]
    assert updated["meta"]["layout_quality"]["image_count"] == 1


def test_bulk_inline_image_revision_failure_restores_previous_visual_state(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    _configure_image_revision(service, tmp_path, monkeypatch)
    original_html = (
        '<p>Argument</p><section data-inline-image-id="1">'
        '<img src="https://example.com/old.jpg"></section>'
    )
    original_meta = {
        "official_account_id": "account-a",
        "inline_images_resolved": True,
        "inline_images": [
            {
                "index": 1,
                "anchor": "Argument conclusion",
                "url": "https://example.com/old.jpg",
            }
        ],
        "layout_quality": {"image_count": 1, "warnings": []},
    }
    service.db.update_job(
        job_id,
        html_content=original_html,
        thumb_media_id="existing-cover-media",
        meta_json=original_meta,
    )
    service.db.update_batch_job_review(batch_id, job_id, "confirmed")

    class FailingPipeline:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run_job(self, selected_job_id: int, **_kwargs) -> None:
            service.db.update_job(
                selected_job_id,
                status="failed",
                step="render",
                error="image provider rate limited",
                html_content="<p>partial replacement</p>",
                thumb_media_id="partial-cover-media",
                meta_json={
                    "inline_images_resolved": False,
                    "inline_images": [],
                },
            )
            raise RuntimeError("image provider rate limited")

    monkeypatch.setattr("app.services.batches.Pipeline", FailingPipeline)

    with pytest.raises(RuntimeError, match="image provider rate limited"):
        service.regenerate_inline_images(batch_id, job_id)

    restored = service._batch_job(batch_id, job_id)  # noqa: SLF001
    assert restored["status"] == "ready_for_review"
    assert restored["step"] == "inject"
    assert restored["error"] is None
    assert restored["review_status"] == "confirmed"
    assert restored["html_content"] == original_html
    assert restored["thumb_media_id"] == "existing-cover-media"
    assert restored["meta"] == original_meta


def test_restore_visual_snapshot_does_not_rerender(tmp_path, monkeypatch) -> None:
    service, batch_id, job_id = _service_with_ready_job(tmp_path)
    saved_meta = {
        "official_account_id": "account-a",
        "inline_images_resolved": True,
        "inline_images": [{"index": 1, "url": "https://example.com/saved.jpg"}],
        "generated_cover_active": True,
        "generated_cover": {"url": "https://example.com/saved-cover.jpg"},
        "layout_quality": {"image_count": 2, "warnings": []},
    }
    service.db.update_job(
        job_id,
        html_content="<p>saved visual snapshot</p>",
        thumb_media_id="saved-cover-media",
        meta_json=saved_meta,
    )
    version_id = service.db.save_job_version(job_id, reason="visual checkpoint")
    service.db.update_job(
        job_id,
        html_content="<p>newer article</p>",
        thumb_media_id="newer-cover-media",
        meta_json={"official_account_id": "account-a", "inline_images": []},
    )

    monkeypatch.setattr(
        service,
        "rerender_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("visual snapshot restoration must not rerender")
        ),
    )

    restored = service.restore_job_version(batch_id, job_id, version_id)

    assert restored["html_content"] == "<p>saved visual snapshot</p>"
    assert restored["thumb_media_id"] == "saved-cover-media"
    assert restored["meta"] == saved_meta
    assert restored["review_status"] == "viewed"
