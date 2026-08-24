from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.config import load_config
from app.services.batches import BatchService


def test_billing_api_is_shadow_read_only_for_users_and_cost_aware_for_admins(
    tmp_path,
) -> None:
    config = {
        **load_config(),
        "_db_path": str(tmp_path / "billing-api.db"),
        "api": {"token": "billing-admin-token"},
    }
    service = BatchService(config)
    app = create_api_app(config, service, start_feishu=False)
    headers = {"Authorization": "Bearer billing-admin-token"}

    with TestClient(app) as client:
        summary = client.get(
            "/api/v1/me/billing/summary", headers=headers
        )
        usage = client.get("/api/v1/me/billing/usage", headers=headers)
        price = client.post(
            "/api/v1/admin/billing/price-cards",
            headers=headers,
            json={
                "id": "api-price-card",
                "provider": "openai",
                "provider_model": "gpt-test",
                "modality": "text",
                "input_micro_cny_per_million": 1_000_000,
            },
        )
        admin_summary = client.get(
            "/api/v1/admin/billing/usage-summary", headers=headers
        )

    assert summary.status_code == 200
    assert summary.json()["mode"] == "shadow"
    assert summary.json()["credits"]["charged"] == 0
    assert "provider_cost" not in summary.text
    assert "retail_cost" not in summary.text
    assert usage.status_code == 200
    assert price.status_code == 200
    assert price.json()["id"] == "api-price-card"
    assert admin_summary.status_code == 200
    assert "provider_cost_micro_cny" in admin_summary.json()
