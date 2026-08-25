from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from app.ai.usage import (
    TOKEN_USAGE_RECORDED,
    UsageRecord,
    bind_usage_recorder,
)
from app.db import Database

MICRO_CNY_PER_CNY = 1_000_000
TOKENS_PER_RATE_UNIT = 1_000_000
logger = logging.getLogger(__name__)


def billing_mode() -> str:
    """Live charging is intentionally impossible in this release."""

    requested = str(
        os.getenv("WECHAT_PUBLISHER_BILLING_MODE") or "shadow"
    ).strip().casefold()
    return "off" if requested in {"off", "disabled", "false", "0"} else "shadow"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _ceil_div(value: int, divisor: int) -> int:
    return 0 if value <= 0 else (value + divisor - 1) // divisor


def calculate_shadow_price(
    record: UsageRecord,
    card: dict[str, Any] | None,
) -> dict[str, Any]:
    usage = record.usage
    if record.funding_source in {"customer", "local"}:
        return {
            "provider_cost_micro_cny": 0,
            "retail_cost_micro_cny": 0,
            "estimated_points": 0,
            "pricing_status": "customer_funded",
            "price_snapshot": {},
        }
    if (
        usage.token_status != TOKEN_USAGE_RECORDED
        and not usage.image_count
        and not usage.fixed_units
    ):
        return {
            "provider_cost_micro_cny": 0,
            "retail_cost_micro_cny": 0,
            "estimated_points": 0,
            "pricing_status": (
                "usage_estimated"
                if usage.source == "estimated"
                else "usage_unavailable"
            ),
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

    uncached = max(usage.input_tokens - usage.cached_input_tokens, 0)
    token_numerator = (
        uncached * int(card.get("input_micro_cny_per_million") or 0)
        + usage.cached_input_tokens
        * int(card.get("cached_input_micro_cny_per_million") or 0)
        + usage.output_tokens
        * int(card.get("output_micro_cny_per_million") or 0)
    )
    provider_cost = _ceil_div(token_numerator, TOKENS_PER_RATE_UNIT)
    provider_cost += usage.image_count * int(
        card.get("image_micro_cny_each") or 0
    )
    provider_cost += usage.fixed_units * int(
        card.get("fixed_request_micro_cny") or 0
    )
    markup_basis_points = max(0, int(card.get("markup_basis_points") or 10_000))
    retail_cost = _ceil_div(provider_cost * markup_basis_points, 10_000)
    points_per_cny = max(0, int(card.get("points_per_cny") or 100))
    estimated_points = _ceil_div(retail_cost * points_per_cny, MICRO_CNY_PER_CNY)
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
            "image_micro_cny_each",
            "fixed_request_micro_cny",
            "markup_basis_points",
            "points_per_cny",
            "effective_from",
            "effective_to",
        )
    }
    return {
        "provider_cost_micro_cny": provider_cost,
        "retail_cost_micro_cny": retail_cost,
        "estimated_points": estimated_points,
        "pricing_status": "priced",
        "price_snapshot": snapshot,
    }


class BillingService:
    """Owner-scoped shadow metering. It never blocks or deducts credits."""

    def __init__(self, db: Database) -> None:
        self.db = db

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
    ) -> Iterator[str | None]:
        owner_user_id = str(self.db.owner_user_id or "").strip()
        if billing_mode() == "off" or not owner_user_id:
            yield None
            return
        try:
            operation_id = self.db.create_usage_operation(
                {
                "id": uuid.uuid4().hex,
                "owner_user_id": owner_user_id,
                "scene": str(scene or "unknown")[:80],
                "source_channel": str(source_channel or "system")[:32],
                "subject_type": str(subject_type or "operation")[:40],
                "subject_id": str(subject_id or "")[:200],
                "idempotency_key": str(idempotency_key or uuid.uuid4().hex)[:240],
                "status": "running",
                "mode": "shadow",
                "job_id": int(job_id) if job_id is not None else None,
                "created_at": _utc_now(),
                }
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "shadow billing operation could not start; business call continues"
            )
            yield None
            return

        def record_usage(record: UsageRecord) -> None:
            self._record_event(operation_id, record, job_id=job_id)

        try:
            with bind_usage_recorder(record_usage):
                yield operation_id
        except BaseException:
            self._finish_operation_safely(operation_id, "failed")
            raise
        else:
            self._finish_operation_safely(operation_id, "succeeded")

    def _record_event(
        self,
        operation_id: str,
        record: UsageRecord,
        *,
        job_id: int | None,
    ) -> None:
        card = self.db.get_effective_model_price_card(
            provider=record.provider,
            provider_model=record.provider_model,
            modality=record.modality,
        )
        priced = calculate_shadow_price(record, card)
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
                    and (
                        usage.token_status == TOKEN_USAGE_RECORDED
                        or usage.image_count > 0
                        or usage.fixed_units > 0
                        or usage.provider_credits is not None
                    )
                ),
                "status": record.status,
                "error_code": record.error_code,
                "created_at": _utc_now(),
            }
        )

    def _finish_operation(self, operation_id: str, status: str) -> None:
        totals = self.db.usage_operation_totals(operation_id)
        self.db.finish_usage_operation(
            operation_id,
            status=status,
            estimated_points=int(totals.get("estimated_points") or 0),
            charged_points=0,
            completed_at=_utc_now(),
        )

    def _finish_operation_safely(self, operation_id: str, status: str) -> None:
        try:
            self._finish_operation(operation_id, status)
        except Exception:  # noqa: BLE001
            logger.exception(
                "shadow billing operation could not finish; business call continues"
            )

    def summary(self) -> dict[str, Any]:
        try:
            totals = self.db.billing_usage_summary()
            wallet = self.db.credit_wallet_summary()
        except Exception:  # noqa: BLE001
            logger.exception("shadow billing summary unavailable")
            totals = {}
            wallet = {}
        return {
            "mode": billing_mode(),
            "plan": {
                "id": "shadow",
                "name": "影子计量",
                "cycle": None,
                "period_end": None,
            },
            "credits": {
                "available": int(wallet.get("available") or 0),
                "reserved": 0,
                "charged": 0,
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
            "notice": "当前为影子计量，不扣积分、不限制现有功能。",
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
    "BillingService",
    "MICRO_CNY_PER_CNY",
    "billing_mode",
    "calculate_shadow_price",
]
