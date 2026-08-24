from __future__ import annotations

from app.ai.usage import NormalizedUsage, UsageRecord, emit_usage
from app.db import Database
from app.services.auth import AuthService
from app.services.billing import BillingService, calculate_shadow_price


def _scoped_db(tmp_path):
    root = Database(tmp_path / "billing.db")
    user = AuthService(root).register("billing-user", "secure-pass-123")
    return root, root.for_user(str(user["id"])), str(user["id"])


def test_shadow_operation_records_actual_usage_without_charging(tmp_path) -> None:
    root, db, _user_id = _scoped_db(tmp_path)
    root.upsert_model_price_card(
        {
            "id": "openai-test",
            "provider": "openai",
            "provider_model": "gpt-test",
            "modality": "text",
            "input_micro_cny_per_million": 1_000_000,
            "cached_input_micro_cny_per_million": 100_000,
            "output_micro_cny_per_million": 2_000_000,
            "markup_basis_points": 15_000,
            "points_per_cny": 100,
        }
    )

    with BillingService(db).operation(
        scene="article_generation",
        subject_type="job",
        subject_id="42",
        idempotency_key="generation:42:0",
        job_id=None,
    ):
        emit_usage(
            provider="openai",
            provider_model="gpt-test",
            usage=NormalizedUsage(
                input_tokens=1_000_000,
                cached_input_tokens=500_000,
                output_tokens=100_000,
                source="provider_actual",
            ),
            model_id="official-openai",
            funding_source="platform",
        )

    summary = BillingService(db).summary()
    rows = BillingService(db).list_usage()
    assert summary["mode"] == "shadow"
    assert summary["credits"]["charged"] == 0
    assert summary["usage"]["input_tokens"] == 1_000_000
    assert rows[0]["status"] == "succeeded"
    assert rows[0]["charged_points"] == 0
    assert rows[0]["estimated_points"] > 0


def test_customer_funded_usage_has_zero_platform_cost() -> None:
    priced = calculate_shadow_price(
        record=UsageRecord(
            provider="openai",
            provider_model="custom",
            modality="text",
            usage=NormalizedUsage(input_tokens=500_000),
            funding_source="customer",
        ),
        card={
            "input_micro_cny_per_million": 9_000_000,
            "markup_basis_points": 20_000,
            "points_per_cny": 100,
        },
    )

    assert priced["provider_cost_micro_cny"] == 0
    assert priced["estimated_points"] == 0
    assert priced["pricing_status"] == "customer_funded"


def test_credit_ledger_is_owner_scoped_and_append_only_through_public_methods(tmp_path) -> None:
    root, alice, _alice_id = _scoped_db(tmp_path)
    bob_user = AuthService(root).register("billing-bob", "secure-pass-456")
    bob = root.for_user(str(bob_user["id"]))

    alice.grant_credit_points(
        points=500,
        source_type="admin",
        actor_user_id="admin",
        reason="影子阶段测试赠送",
    )

    assert alice.credit_wallet_summary()["available"] == 500
    assert alice.list_credit_ledger()[0]["amount_points"] == 500
    assert bob.credit_wallet_summary()["available"] == 0
    assert bob.list_credit_ledger() == []
    assert not hasattr(alice, "update_credit_ledger")
    assert not hasattr(alice, "delete_credit_ledger")


def test_usage_operation_idempotency_is_scoped_per_owner(tmp_path) -> None:
    _root, db, user_id = _scoped_db(tmp_path)
    payload = {
        "owner_user_id": user_id,
        "scene": "review",
        "source_channel": "api",
        "subject_type": "job",
        "subject_id": "9",
        "idempotency_key": "same-request",
        "status": "running",
        "mode": "shadow",
    }
    first = db.create_usage_operation({**payload, "id": "op-first"})
    second = db.create_usage_operation({**payload, "id": "op-second"})

    assert first == second == "op-first"


def test_shadow_operation_start_and_finish_failures_never_replace_business_result(
    tmp_path, monkeypatch
) -> None:
    _root, db, _user_id = _scoped_db(tmp_path)
    service = BillingService(db)
    monkeypatch.setattr(
        db,
        "create_usage_operation",
        lambda _payload: (_ for _ in ()).throw(RuntimeError("schema unavailable")),
    )
    reached = False
    with service.operation(
        scene="review",
        subject_type="job",
        subject_id="1",
    ):
        reached = True
    assert reached is True

    monkeypatch.undo()
    service = BillingService(db)
    monkeypatch.setattr(
        db,
        "finish_usage_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("write failed")),
    )
    with service.operation(
        scene="review",
        subject_type="job",
        subject_id="2",
    ):
        pass


def test_failed_or_discarded_platform_events_are_not_billable(tmp_path) -> None:
    root, db, _user_id = _scoped_db(tmp_path)
    with BillingService(db).operation(
        scene="review",
        subject_type="job",
        subject_id="1",
    ):
        emit_usage(
            provider="openai",
            provider_model="gpt-test",
            usage=NormalizedUsage(input_tokens=10),
            funding_source="platform",
            status="failed",
            error_code="timeout",
        )
        emit_usage(
            provider="openai",
            provider_model="gpt-test",
            usage=NormalizedUsage(input_tokens=10),
            funding_source="platform",
            contributes_to_result=False,
        )

    assert [row["billable"] for row in root.admin_list_ai_usage_events()] == [0, 0]
