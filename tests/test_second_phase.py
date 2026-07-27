from __future__ import annotations

from app.db import Database
from app.feishu.runtime import get_runtime, update_runtime
from app.services.preflight import preflight_accounts


def test_batch_source_configuration_and_job_versions(tmp_path) -> None:
    db = Database(tmp_path / "second-phase.db")
    db.create_batch(
        "batch-source",
        topic="人工智能经营",
        source_mode="references",
        reference_urls=["https://example.com/a", "https://example.com/b"],
        required_facts="保留收入数据",
        rewrite_intensity="深度改写",
    )
    batch = db.get_batch("batch-source")
    assert batch is not None
    assert batch["source_mode"] == "references"
    assert "example.com/a" in batch["reference_urls_json"]
    assert batch["required_facts"] == "保留收入数据"

    job_id = db.create_job(topic="测试", source="manual")
    db.update_job(job_id, body="第一版", selected_title="标题一")
    version_id = db.save_job_version(job_id, reason="编辑前")
    versions = db.list_job_versions(job_id)
    assert versions[0]["id"] == version_id
    assert versions[0]["body"] == "第一版"
    assert versions[0]["reason"] == "编辑前"


def test_feishu_runtime_round_trip(tmp_path) -> None:
    db = Database(tmp_path / "runtime.db")
    update_runtime(db, status="running", last_message_at="2026-07-20T10:00:00+00:00")
    runtime = get_runtime(db)
    assert runtime["status"] == "running"
    assert runtime["last_message_at"].startswith("2026-07-20")
    assert runtime["updated_at"]


def test_preflight_reports_unknown_account_without_crashing(tmp_path) -> None:
    db = Database(tmp_path / "preflight.db")
    results = preflight_accounts(db, ["missing-account"])
    assert len(results) == 1
    assert results[0]["account_id"] == "missing-account"
    assert results[0]["can_generate"] is False
    assert results[0]["can_write"] is False
