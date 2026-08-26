from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from app.ai.usage import (
    TOKEN_USAGE_RECORDED,
    UsageRecord,
    bind_usage_recorder,
)
from app.db import Database

MICRO_CNY_PER_CNY = 1_000_000
TOKENS_PER_RATE_UNIT = 1_000_000
BASIS_POINTS = 10_000
logger = logging.getLogger(__name__)


class BillingConfigurationError(ValueError):
    pass


class InsufficientCreditsError(ValueError):
    pass


def article_task_code(rewrite_intensity: Any) -> str:
    return {
        "light": "article_light",
        "strong": "article_deep",
    }.get(str(rewrite_intensity or "").strip().casefold(), "article_standard")


def billing_mode(policy: dict[str, Any] | None = None) -> str:
    """Apply an optional deployment override to the versioned DB policy."""

    requested = str(
        os.getenv("WECHAT_PUBLISHER_BILLING_MODE") or "managed"
    ).strip().casefold()
    if requested in {"off", "disabled", "false", "0"}:
        return "off"
    if requested in {"shadow", "live"}:
        return requested
    configured = str((policy or {}).get("mode") or "shadow").casefold()
    return configured if configured in {"off", "shadow", "live"} else "shadow"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _ceil_div(value: int, divisor: int) -> int:
    return 0 if value <= 0 else (value + divisor - 1) // divisor


def _round_points(value: int, unit: int) -> int:
    return _ceil_div(max(0, value), max(1, unit)) * max(1, unit)


def pricing_capacity(policy: dict[str, Any]) -> dict[str, int]:
    point_value = max(1, int(policy.get("point_retail_micro_cny") or 10_000))
    discount = min(
        BASIS_POINTS,
        max(0, int(policy.get("max_package_discount_basis_points") or 0)),
    )
    payment_fee = min(
        BASIS_POINTS,
        max(0, int(policy.get("payment_fee_basis_points") or 0)),
    )
    tax = max(0, int(policy.get("tax_basis_points") or 0))
    target_margin = min(
        BASIS_POINTS,
        max(0, int(policy.get("target_margin_basis_points") or 0)),
    )
    net_numerator = (
        point_value
        * (BASIS_POINTS - discount)
        * (BASIS_POINTS - payment_fee)
        * BASIS_POINTS
    )
    net_denominator = BASIS_POINTS * BASIS_POINTS * (BASIS_POINTS + tax)
    minimum_net = max(1, net_numerator // net_denominator)
    cost_capacity = max(
        1,
        minimum_net * (BASIS_POINTS - target_margin) // BASIS_POINTS,
    )
    return {
        "minimum_net_micro_cny_per_point": minimum_net,
        "cost_capacity_micro_cny_per_point": cost_capacity,
    }


def live_configuration_issues(db: Database) -> list[str]:
    """Return only conditions that would make live settlement unsafe."""

    issues: list[str] = []
    policy = db.get_billing_pricing_policy()
    for field, label in (
        ("max_package_discount_basis_points", "套餐最大折扣"),
        ("payment_fee_basis_points", "支付费率"),
        ("target_margin_basis_points", "目标贡献毛利率"),
    ):
        if int(policy.get(field) or 0) >= BASIS_POINTS:
            issues.append(f"{label}必须低于 100%")
    task_rates = db.list_billing_task_rates(enabled_only=True)
    if not task_rates:
        issues.append("至少启用一项任务积分价卡")
    elif any(
        int(rate.get("max_reserve_points") or 0)
        < int(rate.get("base_points") or 0)
        for rate in task_rates
    ):
        issues.append("任务最高冻结积分不能低于基础积分")

    effective_at = _utc_now()
    cards = [
        card
        for card in db.list_model_price_cards(enabled_only=True)
        if str(card.get("effective_from") or "") <= effective_at
        and (
            not card.get("effective_to")
            or str(card.get("effective_to")) > effective_at
        )
    ]
    if not cards:
        issues.append("至少启用一张当前生效的服务商价格卡")
    for card in cards:
        mode = str(card.get("metering_mode") or "TOKEN").upper()
        configured = mode == "BYOK"
        if mode == "TOKEN":
            if str(card.get("modality") or "text") == "image":
                configured = int(card.get("image_micro_cny_each") or 0) > 0
            else:
                configured = any(
                    int(card.get(field) or 0) > 0
                    for field in (
                        "input_micro_cny_per_million",
                        "output_micro_cny_per_million",
                        "reasoning_micro_cny_per_million",
                    )
                )
        elif mode == "FIXED":
            configured = int(card.get("fixed_request_micro_cny") or 0) > 0
        elif mode == "UNIT":
            configured = int(card.get("provider_unit_micro_cny_each") or 0) > 0
        if not configured:
            identity = "/".join(
                str(card.get(key) or "*")
                for key in ("provider", "provider_model", "modality")
            )
            issues.append(f"价格卡 {identity} 缺少 {mode} 成本参数")
    return issues


def calculate_resource_price(
    record: UsageRecord,
    card: dict[str, Any] | None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = dict(policy or {})
    usage = record.usage
    metering_mode = str((card or {}).get("metering_mode") or "TOKEN").upper()
    if record.funding_source in {"customer", "local"} or metering_mode == "BYOK":
        return {
            "provider_cost_micro_cny": 0,
            "retail_cost_micro_cny": 0,
            "estimated_points": 0,
            "pricing_status": "customer_funded",
            "price_snapshot": {},
        }
    if not card:
        return {
            "provider_cost_micro_cny": 0,
            "retail_cost_micro_cny": 0,
            "estimated_points": 0,
            "pricing_status": "price_missing",
            "price_snapshot": {},
        }

    provider_cost = 0
    pricing_status = "price_missing"
    if metering_mode == "TOKEN" and usage.token_status == TOKEN_USAGE_RECORDED:
        uncached = max(usage.input_tokens - usage.cached_input_tokens, 0)
        reasoning_rate = max(
            0,
            int(card.get("reasoning_micro_cny_per_million") or 0),
        )
        reasoning_tokens = min(usage.reasoning_tokens, usage.output_tokens)
        regular_output_tokens = (
            usage.output_tokens - reasoning_tokens
            if reasoning_rate
            else usage.output_tokens
        )
        token_numerator = (
            uncached * int(card.get("input_micro_cny_per_million") or 0)
            + usage.cached_input_tokens
            * int(card.get("cached_input_micro_cny_per_million") or 0)
            + regular_output_tokens
            * int(card.get("output_micro_cny_per_million") or 0)
            + reasoning_tokens * reasoning_rate
        )
        provider_cost = _ceil_div(token_numerator, TOKENS_PER_RATE_UNIT)
        provider_cost += usage.image_count * int(
            card.get("image_micro_cny_each") or 0
        )
        pricing_status = "priced"
    elif usage.image_count > 0 and int(card.get("image_micro_cny_each") or 0) > 0:
        provider_cost = usage.image_count * int(
            card.get("image_micro_cny_each") or 0
        )
        pricing_status = "fixed_price"
    elif metering_mode == "FIXED" or (
        metering_mode == "TOKEN"
        and usage.token_status != TOKEN_USAGE_RECORDED
        and int(card.get("fixed_request_micro_cny") or 0) > 0
    ):
        provider_cost = max(1, usage.fixed_units) * int(
            card.get("fixed_request_micro_cny") or 0
        )
        pricing_status = "fixed_price" if provider_cost > 0 else "price_missing"
    elif metering_mode == "UNIT":
        units = (
            int(usage.provider_credits)
            if usage.provider_credits is not None
            else int(usage.fixed_units)
        )
        provider_cost = units * int(card.get("provider_unit_micro_cny_each") or 0)
        pricing_status = "unit_priced" if units > 0 and provider_cost > 0 else "unit_missing"
    elif usage.source == "estimated":
        pricing_status = "usage_estimated"
    else:
        pricing_status = "usage_unavailable"

    global_risk = max(
        0,
        int(policy.get("provider_risk_reserve_basis_points") or 1_500),
    )
    provider_risk = max(
        0,
        int(
            card.get("provider_risk_basis_points")
            or card.get("markup_basis_points")
            or BASIS_POINTS
        ),
    )
    risk_adjusted_cost = _ceil_div(
        provider_cost * (BASIS_POINTS + global_risk) * provider_risk,
        BASIS_POINTS * BASIS_POINTS,
    )
    capacity = pricing_capacity(policy)
    estimated_points = _round_points(
        _ceil_div(
            risk_adjusted_cost,
            capacity["cost_capacity_micro_cny_per_point"],
        ),
        int(policy.get("rounding_points") or 5),
    )
    snapshot = {
        key: card.get(key)
        for key in (
            "id",
            "provider",
            "provider_model",
            "modality",
            "input_micro_cny_per_million",
            "cached_input_micro_cny_per_million",
            "output_micro_cny_per_million",
            "reasoning_micro_cny_per_million",
            "image_micro_cny_each",
            "fixed_request_micro_cny",
            "provider_unit_micro_cny_each",
            "provider_risk_basis_points",
            "metering_mode",
            "effective_from",
            "effective_to",
        )
    }
    snapshot["policy_version"] = int(policy.get("version") or 0)
    snapshot.update(capacity)
    return {
        "provider_cost_micro_cny": provider_cost,
        "retail_cost_micro_cny": risk_adjusted_cost,
        "estimated_points": estimated_points,
        "pricing_status": pricing_status,
        "price_snapshot": snapshot,
    }


def calculate_shadow_price(
    record: UsageRecord,
    card: dict[str, Any] | None,
) -> dict[str, Any]:
    """Backward-compatible name for callers using the former shadow calculator."""

    return calculate_resource_price(record, card)


class BillingService:
    """Owner-scoped metering with opt-in live reservation and settlement."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure_task_capacity(self, task_code: str, *, count: int = 1) -> None:
        """Reject a fixed-price batch before any paid provider work starts."""

        owner_user_id = str(self.db.owner_user_id or "").strip()
        policy = self.db.get_billing_pricing_policy()
        if not owner_user_id or billing_mode(policy) != "live":
            return
        task_count = max(0, int(count))
        if task_count == 0:
            return
        rate = self.db.get_billing_task_rate(task_code)
        if not rate:
            raise BillingConfigurationError(
                f"任务 {task_code} 尚未配置积分价卡，暂不能正式计费"
            )
        required = max(0, int(rate.get("max_reserve_points") or 0)) * task_count
        if required <= 0:
            raise BillingConfigurationError("该任务尚未配置最高冻结积分")
        self.db.release_expired_credit_reservations()
        available = int(self.db.credit_wallet_summary().get("available") or 0)
        if available < required:
            raise InsufficientCreditsError(
                f"积分不足：本次批量操作需冻结 {required} 积分，"
                f"当前可用 {available} 积分"
            )

    @contextmanager
    def operation(
        self,
        *,
        scene: str,
        subject_type: str,
        subject_id: str,
        source_channel: str = "system",
        idempotency_key: str = "",
        job_id: int | None = None,
        task_code: str = "",
        task_only: bool = False,
    ) -> Iterator[str | None]:
        owner_user_id = str(self.db.owner_user_id or "").strip()
        policy = self.db.get_billing_pricing_policy()
        mode = billing_mode(policy)
        if mode == "off" or not owner_user_id:
            yield None
            return
        effective_task_code = str(task_code or scene or "unknown")[:80]
        task_rate = self.db.get_billing_task_rate(effective_task_code)
        if mode == "live" and not task_rate:
            raise BillingConfigurationError(
                f"任务 {effective_task_code} 尚未配置积分价卡，暂不能正式计费"
            )
        requested_operation_id = uuid.uuid4().hex
        try:
            operation_id = self.db.create_usage_operation(
                {
                "id": requested_operation_id,
                "owner_user_id": owner_user_id,
                "scene": str(scene or "unknown")[:80],
                "source_channel": str(source_channel or "system")[:32],
                "subject_type": str(subject_type or "operation")[:40],
                "subject_id": str(subject_id or "")[:200],
                "idempotency_key": str(idempotency_key or uuid.uuid4().hex)[:240],
                "status": "running",
                "mode": mode,
                "job_id": int(job_id) if job_id is not None else None,
                "task_code": effective_task_code,
                "task_base_points": int((task_rate or {}).get("base_points") or 0),
                "created_at": _utc_now(),
                }
            )
        except Exception:  # noqa: BLE001
            if mode == "live":
                raise BillingConfigurationError("积分操作创建失败，请稍后重试")
            logger.exception(
                "shadow billing operation could not start; business call continues"
            )
            yield None
            return

        if operation_id != requested_operation_id:
            if mode == "live":
                raise BillingConfigurationError(
                    "该请求正在处理或已经完成，请勿重复提交"
                )
            logger.info(
                "duplicate shadow billing request skipped: %s",
                operation_id,
            )
            yield operation_id
            return

        if mode == "live":
            reserve_points = int((task_rate or {}).get("max_reserve_points") or 0)
            if reserve_points <= 0:
                self.db.finish_usage_operation(
                    operation_id,
                    status="rejected",
                    estimated_points=0,
                    charged_points=0,
                    completed_at=_utc_now(),
                )
                raise BillingConfigurationError("该任务尚未配置最高冻结积分")
            try:
                self.db.release_expired_credit_reservations()
                self.db.reserve_credit_points(
                    operation_id,
                    points=reserve_points,
                    expires_at=(
                        datetime.now(timezone.utc) + timedelta(hours=24)
                    ).isoformat(timespec="microseconds"),
                )
            except (ValueError, RuntimeError) as exc:
                self.db.finish_usage_operation(
                    operation_id,
                    status="rejected",
                    estimated_points=0,
                    charged_points=0,
                    completed_at=_utc_now(),
                )
                if "积分不足" in str(exc):
                    raise InsufficientCreditsError(str(exc)) from exc
                raise BillingConfigurationError(str(exc)) from exc

        def record_usage(record: UsageRecord) -> None:
            self._record_event(
                operation_id,
                record,
                job_id=job_id,
                policy=policy,
            )

        try:
            with bind_usage_recorder(record_usage):
                yield operation_id
        except BaseException:
            self._finish_operation_safely(
                operation_id,
                "failed",
                mode=mode,
                policy=policy,
                task_rate=task_rate or {},
                task_only=task_only,
            )
            raise
        else:
            self._finish_operation_safely(
                operation_id,
                "succeeded",
                mode=mode,
                policy=policy,
                task_rate=task_rate or {},
                task_only=task_only,
            )

    def _record_event(
        self,
        operation_id: str,
        record: UsageRecord,
        *,
        job_id: int | None,
        policy: dict[str, Any],
    ) -> None:
        card = self.db.get_effective_model_price_card(
            provider=record.provider,
            provider_model=record.provider_model,
            modality=record.modality,
        )
        priced = calculate_resource_price(record, card, policy)
        usage = record.usage
        self.db.insert_ai_usage_event(
            {
                "id": uuid.uuid4().hex,
                "owner_user_id": self.db.owner_user_id,
                "operation_id": operation_id,
                "job_id": job_id,
                "model_id": record.model_id,
                "provider": record.provider,
                "provider_model": record.provider_model,
                "funding_source": record.funding_source,
                "modality": record.modality,
                "input_tokens": usage.input_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "output_tokens": usage.output_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "total_tokens": usage.total_tokens,
                "image_count": usage.image_count,
                "fixed_units": usage.fixed_units,
                "usage_source": usage.source,
                "token_usage_status": usage.token_status,
                "provider_credits": usage.provider_credits,
                "raw_usage_json": json.dumps(
                    usage.raw_usage,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "provider_request_id": record.request_id,
                "provider_response_id": record.response_id,
                "provider_cost_micro_cny": priced["provider_cost_micro_cny"],
                "retail_cost_micro_cny": priced["retail_cost_micro_cny"],
                "estimated_points": priced["estimated_points"],
                "pricing_status": priced["pricing_status"],
                "price_snapshot_json": json.dumps(
                    priced["price_snapshot"], ensure_ascii=False
                ),
                "contributes_to_result": record.contributes_to_result,
                "billable": (
                    record.funding_source == "platform"
                    and record.contributes_to_result
                    and record.status == "succeeded"
                    and priced["pricing_status"]
                    in {"priced", "fixed_price", "unit_priced"}
                ),
                "status": record.status,
                "error_code": record.error_code,
                "created_at": _utc_now(),
            }
        )

    def _finish_operation(
        self,
        operation_id: str,
        status: str,
        *,
        mode: str,
        policy: dict[str, Any],
        task_rate: dict[str, Any],
        task_only: bool = False,
    ) -> None:
        totals = self.db.usage_operation_totals(operation_id)
        event_count = int(totals.get("event_count") or 0)
        unpriced_events = int(totals.get("unpriced_platform_events") or 0)
        platform_events = int(totals.get("billable_events") or 0)
        customer_events = int(totals.get("customer_funded_events") or 0)
        task_base_points = (
            int(task_rate.get("base_points") or 0)
            if status == "succeeded" and (event_count > 0 or task_only)
            else 0
        )
        resource_points = 0
        if status == "succeeded" and platform_events and not task_only:
            capacity = pricing_capacity(policy)
            cost = int(totals.get("risk_adjusted_cost_micro_cny") or 0)
            cost += max(
                0,
                int(policy.get("platform_task_cost_micro_cny") or 0),
            )
            resource_points = _round_points(
                _ceil_div(
                    cost,
                    capacity["cost_capacity_micro_cny_per_point"],
                ),
                int(policy.get("rounding_points") or 5),
            )
        elif status == "succeeded" and customer_events and not task_only:
            resource_points = max(
                0,
                int(policy.get("byok_infrastructure_points") or 0),
            )

        uncapped_points = task_base_points + resource_points
        pricing_complete = bool(
            status == "succeeded"
            and (
                task_only
                or (
                    event_count > 0
                    and unpriced_events == 0
                    and (platform_events > 0 or customer_events > 0)
                )
            )
        )
        final_status = status
        charged_points = 0
        reserved_points = 0
        if mode == "live":
            operation = self.db.get_usage_operation(operation_id) or {}
            reserved_points = int(operation.get("reserved_points") or 0)
            if pricing_complete:
                charged_points = min(uncapped_points, reserved_points)
            elif status == "succeeded":
                final_status = "pricing_incomplete"

        snapshot = {
            "policy_id": str(policy.get("id") or "default"),
            "policy_version": int(policy.get("version") or 0),
            "task_code": str(task_rate.get("task_code") or ""),
            "task_rate_version": int(task_rate.get("version") or 0),
            "task_base_points": task_base_points,
            "resource_points": resource_points,
            "uncapped_points": uncapped_points,
            "reserved_points": reserved_points,
            "charged_points": charged_points,
            "reservation_cap_reached": bool(
                pricing_complete and uncapped_points > reserved_points
            ),
            "pricing_complete": pricing_complete,
            "task_only": bool(task_only),
            "unpriced_platform_events": unpriced_events,
            "provider_cost_micro_cny": int(
                totals.get("provider_cost_micro_cny") or 0
            ),
            "risk_adjusted_cost_micro_cny": int(
                totals.get("risk_adjusted_cost_micro_cny") or 0
            ),
            **pricing_capacity(policy),
        }
        completed_at = _utc_now()
        if mode == "live":
            self.db.settle_credit_operation(
                operation_id,
                status=final_status,
                charged_points=charged_points,
                estimated_points=uncapped_points,
                task_base_points=task_base_points,
                resource_points=resource_points,
                pricing_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
                completed_at=completed_at,
            )
        else:
            self.db.finish_usage_operation(
                operation_id,
                status=final_status,
                estimated_points=uncapped_points,
                charged_points=0,
                task_base_points=task_base_points,
                resource_points=resource_points,
                pricing_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
                completed_at=completed_at,
            )

    def _finish_operation_safely(
        self,
        operation_id: str,
        status: str,
        *,
        mode: str,
        policy: dict[str, Any],
        task_rate: dict[str, Any],
        task_only: bool = False,
    ) -> None:
        try:
            self._finish_operation(
                operation_id,
                status,
                mode=mode,
                policy=policy,
                task_rate=task_rate,
                task_only=task_only,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "billing operation could not finish; business call continues"
            )

    def summary(self) -> dict[str, Any]:
        try:
            policy = self.db.get_billing_pricing_policy()
            mode = billing_mode(policy)
            if mode == "live":
                self.db.release_expired_credit_reservations()
            totals = self.db.billing_usage_summary()
            wallet = self.db.credit_wallet_summary()
        except Exception:  # noqa: BLE001
            logger.exception("billing summary unavailable")
            policy = {}
            mode = "shadow"
            totals = {}
            wallet = {}
        plan_name = {
            "off": "积分计量已暂停",
            "shadow": "积分试算",
            "live": "商业积分",
        }.get(mode, "积分试算")
        notice = {
            "off": "当前已暂停积分计量，不记录也不扣除积分。",
            "shadow": "当前为积分试算，不扣积分、不限制现有功能。",
            "live": "任务开始时冻结最高积分，完成后按实际成本结算并退回差额。",
        }.get(mode, "当前为积分试算，不扣积分、不限制现有功能。")
        return {
            "mode": mode,
            "plan": {
                "id": str(policy.get("id") or "default"),
                "name": plan_name,
                "cycle": None,
                "period_end": None,
                "policy_version": int(policy.get("version") or 0),
            },
            "credits": {
                "available": int(wallet.get("available") or 0),
                "reserved": int(wallet.get("reserved") or 0),
                "charged": int(wallet.get("charged") or 0),
            },
            "usage": {
                "operations": int(totals.get("operations") or 0),
                "input_tokens": int(totals.get("input_tokens") or 0),
                "cached_input_tokens": int(
                    totals.get("cached_input_tokens") or 0
                ),
                "output_tokens": int(totals.get("output_tokens") or 0),
                "images": int(totals.get("image_count") or 0),
                "provider_credits": int(totals.get("provider_credits") or 0),
                "unavailable_token_calls": int(
                    totals.get("unavailable_token_calls") or 0
                ),
                "estimated_points": int(totals.get("estimated_points") or 0),
            },
            "notice": notice,
        }

    def list_usage(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        try:
            return self.db.list_usage_operations(limit=limit, offset=offset)
        except Exception:  # noqa: BLE001
            logger.exception("shadow usage list unavailable")
            return []

    def article_generation_usage(
        self,
        job_ids: list[int],
    ) -> dict[int, dict[str, int | bool]]:
        """Return strict Token completeness keyed by article job id."""

        try:
            rows = self.db.article_generation_token_usage_by_jobs(job_ids)
        except Exception:  # noqa: BLE001
            logger.exception("article generation token usage unavailable")
            return {}
        return {
            int(row["job_id"]): self._normalized_article_usage(row)
            for row in rows
        }

    def article_generation_tokens(self, job_ids: list[int]) -> dict[int, int]:
        """Compatibility view: expose only complete provider-actual totals."""

        return {
            job_id: int(usage["known_tokens"])
            for job_id, usage in self.article_generation_usage(job_ids).items()
            if bool(usage["complete"])
        }

    @staticmethod
    def _normalized_article_usage(
        row: dict[str, Any],
    ) -> dict[str, int | bool]:
        values = {
            key: int(row.get(key) or 0)
            for key in (
                "known_tokens",
                "estimated_tokens",
                "api_call_count",
                "metered_calls",
                "pending_calls",
                "unavailable_calls",
                "estimated_calls",
                "manus_tasks",
                "provider_credits",
                "credit_metered_calls",
                "pricing_incomplete_calls",
                "estimated_points",
                "reserved_points",
                "charged_points",
                "live_pricing",
            )
        }
        values["complete"] = bool(
            values["api_call_count"] > 0
            and values["metered_calls"] == values["api_call_count"]
        )
        return values

    @classmethod
    def aggregate_article_generation_usage(
        cls,
        rows: list[dict[str, int | bool]],
    ) -> dict[str, int | bool] | None:
        if not rows:
            return None
        totals: dict[str, Any] = {
            key: sum(int(row.get(key) or 0) for row in rows)
            for key in (
                "known_tokens",
                "estimated_tokens",
                "api_call_count",
                "metered_calls",
                "pending_calls",
                "unavailable_calls",
                "estimated_calls",
                "manus_tasks",
                "provider_credits",
                "credit_metered_calls",
                "pricing_incomplete_calls",
                "estimated_points",
                "reserved_points",
                "charged_points",
                "live_pricing",
            )
        }
        totals["complete"] = bool(
            totals["api_call_count"] > 0
            and totals["metered_calls"] == totals["api_call_count"]
        )
        return totals

    def list_ledger(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        try:
            return self.db.list_credit_ledger(limit=limit, offset=offset)
        except Exception:  # noqa: BLE001
            logger.exception("credit ledger unavailable")
            return []


__all__ = [
    "BillingConfigurationError",
    "BillingService",
    "InsufficientCreditsError",
    "MICRO_CNY_PER_CNY",
    "article_task_code",
    "billing_mode",
    "calculate_resource_price",
    "calculate_shadow_price",
    "live_configuration_issues",
    "pricing_capacity",
]
