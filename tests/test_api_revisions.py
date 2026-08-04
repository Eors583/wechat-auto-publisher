from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.config import load_config
from app.services.batches import BatchService


def test_single_image_revision_api_delegates_to_shared_service(
    tmp_path, monkeypatch
) -> None:
    config = {
        **load_config(),
        "_db_path": str(tmp_path / "api-revisions.db"),
        "api": {"token": "test-token"},
        "feishu": {"enabled": False},
    }
    service = BatchService(config)
    captured: list[tuple[str, int, int, str]] = []

    def fake_revision(
        batch_id: str,
        job_id: int,
        image_index: int,
        *,
        instruction: str,
    ) -> dict:
        captured.append((batch_id, job_id, image_index, instruction))
        return {
            "id": job_id,
            "status": "ready_for_review",
            "review_status": "viewed",
            "meta": {
                "inline_images": [
                    {"index": image_index, "url": "https://example.com/revised.jpg"}
                ]
            },
        }

    monkeypatch.setattr(service, "regenerate_inline_image", fake_revision)
    app = create_api_app(config, service, start_feishu=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/batches/batch-1/jobs/12/inline-images/2/regenerate",
            headers={"Authorization": "Bearer test-token"},
            json={"instruction": "改成真实仓库盘点现场"},
        )

    assert response.status_code == 200
    assert captured == [("batch-1", 12, 2, "改成真实仓库盘点现场")]
    assert response.json()["meta"]["inline_images"][0]["index"] == 2


def test_single_image_revision_api_requires_instruction(tmp_path) -> None:
    config = {
        **load_config(),
        "_db_path": str(tmp_path / "api-revisions-validation.db"),
        "api": {"token": "test-token"},
        "feishu": {"enabled": False},
    }
    service = BatchService(config)
    app = create_api_app(config, service, start_feishu=False)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/batches/batch-1/jobs/12/inline-images/2/regenerate",
            headers={"Authorization": "Bearer test-token"},
            json={"instruction": ""},
        )

    assert response.status_code == 422
