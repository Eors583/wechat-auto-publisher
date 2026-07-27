"""Modular Feishu tool handlers grouped by business domain."""

from app.feishu.tool_modules.admin import AdminToolMixin
from app.feishu.tool_modules.discovery import DiscoveryToolMixin
from app.feishu.tool_modules.editorial_review import EditorialReviewToolMixin
from app.feishu.tool_modules.review import ReviewToolMixin
from app.feishu.tool_modules.system import SystemToolMixin

__all__ = [
    "AdminToolMixin",
    "DiscoveryToolMixin",
    "EditorialReviewToolMixin",
    "ReviewToolMixin",
    "SystemToolMixin",
]
