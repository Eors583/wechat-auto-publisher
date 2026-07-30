from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "AnalyticsService": ("app.services.analytics", "AnalyticsService"),
    "BatchService": ("app.services.batches", "BatchService"),
    "ConfigurationService": (
        "app.services.configuration",
        "ConfigurationService",
    ),
    "CreationPlanService": (
        "app.services.creation_plans",
        "CreationPlanService",
    ),
    "EditorialReviewService": (
        "app.services.editorial_reviews",
        "EditorialReviewService",
    ),
    "FollowedContentService": (
        "app.services.followed_content",
        "FollowedContentService",
    ),
    "OnboardingService": ("app.services.onboarding", "OnboardingService"),
    "TopicSourceService": (
        "app.services.topic_sources",
        "TopicSourceService",
    ),
    "get_batch_service": ("app.services.batches", "get_batch_service"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load service facades on demand and keep workflow imports acyclic."""

    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
