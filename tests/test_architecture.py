from __future__ import annotations

from pathlib import Path

from app.services.batch_contracts import batch_progress, public_job
from app.workflows import DeliverySteps, GenerationSteps, RenderingStep, WorkflowContext


ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_is_a_small_facade_over_independent_stages() -> None:
    pipeline_source = (ROOT / "app" / "pipeline.py").read_text(encoding="utf-8")
    assert len(pipeline_source.splitlines()) < 250
    assert "GenerationSteps" in pipeline_source
    assert "RenderingStep" in pipeline_source
    assert "DeliverySteps" in pipeline_source


def test_workflow_stages_do_not_import_pipeline_facade() -> None:
    workflow_dir = ROOT / "app" / "workflows"
    for path in workflow_dir.glob("*.py"):
        assert "from app.pipeline" not in path.read_text(encoding="utf-8")


def test_workflow_modules_have_separate_public_types() -> None:
    assert WorkflowContext.__module__.endswith("context")
    assert GenerationSteps.__module__.endswith("generation")
    assert RenderingStep.__module__.endswith("rendering")
    assert DeliverySteps.__module__.endswith("delivery")


def test_batch_contract_projection_is_independent_from_service() -> None:
    internal = {
        "id": 7,
        "status": "ready_for_review",
        "step": "inject",
        "titles": ["标题一"],
        "subtitles": ["副标题"],
        "meta": {
            "official_account_id": "account-a",
            "official_account_name": "公众号A",
            "selected_model_name": "Kimi",
        },
    }
    projected = public_job(internal, include_content=False)
    progress = batch_progress([projected])
    assert projected["account_name"] == "公众号A"
    assert progress == {
        "total": 1,
        "completed": 1,
        "ready_for_review": 1,
        "drafted": 0,
        "failed": 0,
        "confirmed": 0,
        "unconfirmed": 1,
        "review_total": 1,
        "reviewed": 0,
    }


def test_all_wechat_api_callsites_use_the_shared_factory() -> None:
    callsite_paths = (
        ROOT / "app" / "workflows" / "context.py",
        ROOT / "app" / "services" / "followed_content.py",
        ROOT / "app" / "benchmark.py",
        ROOT / "app" / "ui" / "desktop.py",
    )
    for path in callsite_paths:
        source = path.read_text(encoding="utf-8")
        assert "build_wechat_client(" in source
        assert "WeChatClient(" not in source
        assert "WeChatAuth(" not in source

    desktop_source = callsite_paths[-1].read_text(encoding="utf-8")
    assert "build_wechat_auth(" in desktop_source
