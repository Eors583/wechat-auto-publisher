from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.config import load_config
from app.services.batches import BatchService


def test_billing_api_is_customer_safe_and_commercially_configurable_for_admins(
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
        policy = client.get(
            "/api/v1/admin/billing/policy", headers=headers
        )
        live_policy = client.put(
            "/api/v1/admin/billing/policy",
            headers=headers,
            json={
                **{
                    key: value
                    for key, value in policy.json().items()
                    if key not in {
                        "id",
                        "enabled",
                        "version",
                        "created_at",
                        "updated_at",
                        "live_configuration_issues",
                    }
                },
                "mode": "live",
            },
        )
        users = client.get("/api/v1/admin/users", headers=headers)
        invalid_grant = client.post(
            "/api/v1/admin/billing/credits/grants",
            headers=headers,
            json={
                "user_id": users.json()[0]["id"],
                "points": 1_000,
                "expires_at": "2099-01-01T00:00:00",
            },
        )
        grant = client.post(
            "/api/v1/admin/billing/credits/grants",
            headers=headers,
            json={
                "user_id": users.json()[0]["id"],
                "points": 1_000,
                "reason": "API 商业积分验收",
            },
        )
        live_summary = client.get(
            "/api/v1/me/billing/summary", headers=headers
        )
        plans = client.get("/api/v1/billing/plans")

    assert summary.status_code == 200
    assert summary.json()["mode"] == "shadow"
    assert summary.json()["credits"]["charged"] == 0
    assert "provider_cost" not in summary.text
    assert "retail_cost" not in summary.text
    assert usage.status_code == 200
    assert price.status_code == 200
    assert price.json()["id"] == "api-price-card"
    assert price.json()["metering_mode"] == "TOKEN"
    assert admin_summary.status_code == 200
    assert "provider_cost_micro_cny" in admin_summary.json()
    assert policy.status_code == 200
    assert policy.json()["live_configuration_issues"] == []
    assert live_policy.status_code == 200
    assert live_policy.json()["mode"] == "live"
    assert invalid_grant.status_code == 400
    assert grant.status_code == 200
    assert grant.json()["wallet"]["available"] == 1_000
    assert live_summary.status_code == 200
    assert live_summary.json()["mode"] == "live"
    assert live_summary.json()["credits"]["available"] == 1_000
    assert plans.status_code == 200
    assert plans.json()[0]["mode"] == "live"
    assert any(
        item["task_code"] == "article_standard"
        for item in plans.json()[0]["task_rates"]
    )
