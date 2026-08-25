from __future__ import annotations

import json

import pytest

from app.ai.usage import NormalizedUsage, UsageRecord, emit_usage, fixed_usage
from app.db import Database
from app.services.auth import AuthService
from app.services.billing import (
    BillingConfigurationError,
    BillingService,
    InsufficientCreditsError,
    calculate_resource_price,
    live_configuration_issues,
    pricing_capacity,
)


def _commercial_db(tmp_path):
    root = Database(tmp_path / "commercial-billing.db")
    user = AuthService(root).register("commercial-user", "secure-pass-123")
    return root, root.for_user(str(user["id"])), str(user["id"])


def _enable_live(root: Database) -> dict:
    policy = root.get_billing_pricing_policy()
    root.upsert_billing_pricing_policy({**policy, "mode": "live"})
    return root.get_billing_pricing_policy()


def _save_example_token_card(root: Database) -> None:
    root.upsert_model_price_card(
        {
            "id": "example-token-model",
            "provider": "openai",
            "provider_model": "example-model",
            "modality": "text",
            "metering_mode": "TOKEN",
            "input_micro_cny_per_million": 10_000_000,
            "output_micro_cny_per_million": 40_000_000,
            "provider_risk_basis_points": 10_000,
        }
    )


def test_manus_spreadsheet_defaults_produce_155_points_for_standard_example(
    tmp_path,
) -> None:
    root, db, _user_id = _commercial_db(tmp_path)
    policy = _enable_live(root)
    _save_example_token_card(root)
    db.grant_credit_points(points=1_000, source_type="test")

    assert pricing_capacity(policy) == {
        "minimum_net_micro_cny_per_point": 7_433,
        "cost_capacity_micro_cny_per_point": 2_601,
    }

    with BillingService(db).operation(
        scene="article_generation",
        task_code="article_standard",
        subject_type="job",
        subject_id="42",
        idempotency_key="commercial-standard-42",
    ):
        emit_usage(
            provider="openai",
            provider_model="example-model",
            usage=NormalizedUsage(
                input_tokens=6_000,
                output_tokens=3_000,
                source="provider_actual",
            ),
        )

    operation = db.list_usage_operations()[0]
    wallet = db.credit_wallet_summary()
    ledger = db.list_credit_ledger(limit=10)
    snapshot = json.loads(str(operation["pricing_snapshot_json"]))

    assert operation["task_base_points"] == 60
    assert operation["resource_points"] == 95
    assert operation["estimated_points"] == 155
    assert operation["charged_points"] == 155
    assert wallet == {"available": 845, "reserved": 0, "charged": 155}
    assert sorted(row["amount_points"] for row in ledger) == [-400, 245, 1_000]
    assert snapshot["provider_cost_micro_cny"] == 180_000
    assert snapshot["risk_adjusted_cost_micro_cny"] == 207_000
    assert snapshot["reservation_cap_reached"] is False


def test_live_failure_and_incomplete_pricing_release_the_full_reservation(
    tmp_path,
) -> None:
    root, db, _user_id = _commercial_db(tmp_path)
    _enable_live(root)
    _save_example_token_card(root)
    db.grant_credit_points(points=1_000, source_type="test")
    service = BillingService(db)

    with pytest.raises(RuntimeError, match="provider failed"):
        with service.operation(
            scene="article_generation",
            task_code="article_standard",
            subject_type="job",
            subject_id="failed",
            idempotency_key="commercial-failed",
        ):
            raise RuntimeError("provider failed")

    with service.operation(
        scene="article_generation",
        task_code="article_standard",
        subject_type="job",
        subject_id="missing-price",
        idempotency_key="commercial-missing-price",
    ):
        emit_usage(
            provider="unknown-provider",
            provider_model="unknown-model",
            usage=NormalizedUsage(
                input_tokens=10,
                output_tokens=5,
                source="provider_actual",
            ),
        )

    operations = {
        row["subject_id"]: row for row in db.list_usage_operations(limit=10)
    }
    assert operations["failed"]["status"] == "failed"
    assert operations["failed"]["charged_points"] == 0
    assert operations["missing-price"]["status"] == "pricing_incomplete"
    assert operations["missing-price"]["estimated_points"] == 60
    assert operations["missing-price"]["charged_points"] == 0
    assert db.credit_wallet_summary()["available"] == 1_000


def test_live_mode_blocks_business_call_when_points_are_insufficient(tmp_path) -> None:
    root, db, _user_id = _commercial_db(tmp_path)
    _enable_live(root)
    reached = False

    with pytest.raises(InsufficientCreditsError, match="积分不足"):
        with BillingService(db).operation(
            scene="article_generation",
            task_code="article_standard",
            subject_type="job",
            subject_id="no-credit",
        ):
            reached = True

    assert reached is False
    assert db.list_usage_operations()[0]["status"] == "rejected"


def test_live_idempotency_key_cannot_repeat_provider_work_or_charge(tmp_path) -> None:
    root, db, _user_id = _commercial_db(tmp_path)
    _enable_live(root)
    _save_example_token_card(root)
    db.grant_credit_points(points=1_000, source_type="test")
    service = BillingService(db)
    payload = {
        "scene": "article_generation",
        "task_code": "article_standard",
        "subject_type": "job",
        "subject_id": "same-request",
        "idempotency_key": "same-live-request",
    }

    with service.operation(**payload):
        emit_usage(
            provider="openai",
            provider_model="example-model",
            usage=NormalizedUsage(
                input_tokens=6_000,
                output_tokens=3_000,
                source="provider_actual",
            ),
        )

    repeated_work_started = False
    with pytest.raises(BillingConfigurationError, match="请勿重复提交"):
        with service.operation(**payload):
            repeated_work_started = True

    assert repeated_work_started is False
    assert db.credit_wallet_summary()["available"] == 845
    assert len(db.list_usage_operations()) == 1


def test_token_fixed_unit_and_byok_modes_share_one_cost_unit(tmp_path) -> None:
    root, _db, _user_id = _commercial_db(tmp_path)
    policy = root.get_billing_pricing_policy()

    token = calculate_resource_price(
        UsageRecord(
            provider="openai",
            provider_model="reasoner",
            modality="text",
            usage=NormalizedUsage(
                input_tokens=1_000,
                cached_input_tokens=200,
                output_tokens=500,
                reasoning_tokens=300,
                source="provider_actual",
            ),
        ),
        {
            "metering_mode": "TOKEN",
            "input_micro_cny_per_million": 10_000_000,
            "cached_input_micro_cny_per_million": 1_000_000,
            "output_micro_cny_per_million": 20_000_000,
            "reasoning_micro_cny_per_million": 50_000_000,
            "provider_risk_basis_points": 10_000,
        },
        policy,
    )
    fixed = calculate_resource_price(
        UsageRecord(
            provider="fixed-provider",
            provider_model="fixed",
            modality="text",
            usage=fixed_usage(),
        ),
        {
            "metering_mode": "FIXED",
            "fixed_request_micro_cny": 300_000,
            "provider_risk_basis_points": 10_000,
        },
        policy,
    )
    unit = calculate_resource_price(
        UsageRecord(
            provider="manus",
            provider_model="manus-1.6",
            modality="text",
            usage=fixed_usage(provider_credits=37),
        ),
        {
            "metering_mode": "UNIT",
            "provider_unit_micro_cny_each": 2_000,
            "provider_risk_basis_points": 10_000,
        },
        policy,
    )
    byok = calculate_resource_price(
        UsageRecord(
            provider="custom",
            provider_model="user-model",
            modality="text",
            usage=NormalizedUsage(
                input_tokens=999_999,
                output_tokens=999_999,
                source="provider_actual",
            ),
        ),
        {"metering_mode": "BYOK"},
        policy,
    )

    assert token["provider_cost_micro_cny"] == 27_200
    assert fixed["provider_cost_micro_cny"] == 300_000
    assert fixed["pricing_status"] == "fixed_price"
    assert unit["provider_cost_micro_cny"] == 74_000
    assert unit["pricing_status"] == "unit_priced"
    assert byok == {
        "provider_cost_micro_cny": 0,
        "retail_cost_micro_cny": 0,
        "estimated_points": 0,
        "pricing_status": "customer_funded",
        "price_snapshot": {},
    }


def test_credit_expiry_must_be_future_timezone_aware_iso_timestamp(tmp_path) -> None:
    _root, db, _user_id = _commercial_db(tmp_path)

    with pytest.raises(ValueError, match="必须包含时区"):
        db.grant_credit_points(
            points=100,
            source_type="test",
            expires_at="2099-01-01T00:00:00",
        )
    with pytest.raises(ValueError, match="必须晚于当前时间"):
        db.grant_credit_points(
            points=100,
            source_type="test",
            expires_at="2020-01-01T00:00:00+00:00",
        )


def test_live_readiness_requires_effective_mode_specific_cost_cards(tmp_path) -> None:
    root, _db, _user_id = _commercial_db(tmp_path)
    root.upsert_model_price_card(
        {
            "id": "future-text-card",
            "provider": "future-provider",
            "provider_model": "future-model",
            "modality": "text",
            "metering_mode": "TOKEN",
            "input_micro_cny_per_million": 1_000_000,
            "effective_from": "2099-01-01T00:00:00+00:00",
        }
    )

    assert "至少启用一张当前生效的服务商价格卡" in live_configuration_issues(
        root
    )

    root.upsert_model_price_card(
        {
            "id": "empty-text-card",
            "provider": "empty-provider",
            "provider_model": "empty-model",
            "modality": "text",
            "metering_mode": "TOKEN",
            "image_micro_cny_each": 1_000_000,
        }
    )
    assert any(
        "缺少 TOKEN 成本参数" in issue
        for issue in live_configuration_issues(root)
    )

    policy = root.get_billing_pricing_policy()
    with pytest.raises(ValueError, match="必须低于 100%"):
        root.upsert_billing_pricing_policy(
            {
                **policy,
                "mode": "live",
                "target_margin_basis_points": 10_000,
            }
        )
