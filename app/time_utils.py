from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

# The product serves WeChat Official Account operators in mainland China.
# Persist timestamps as UTC, but keep all business-day and UI calculations on
# China Standard Time regardless of the host/container timezone.
BUSINESS_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def business_now() -> datetime:
    return datetime.now(BUSINESS_TIMEZONE)


def business_date() -> date:
    return business_now().date()


def to_business_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None

    # Older data may not contain an offset. It was historically displayed as
    # local business time, so preserve that wall-clock value instead of adding
    # another eight hours during migration.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BUSINESS_TIMEZONE)
    return parsed.astimezone(BUSINESS_TIMEZONE)


def format_business_datetime(value: Any, *, fallback: str = "-") -> str:
    parsed = to_business_datetime(value)
    if parsed is None:
        return fallback
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def business_day_bounds_utc(
    value: date | None = None,
) -> tuple[datetime, datetime]:
    day = value or business_date()
    start = datetime.combine(day, datetime.min.time(), tzinfo=BUSINESS_TIMEZONE)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(
        UTC
    )
