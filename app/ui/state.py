from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from app.accounts import (
    DEFAULT_ACCOUNT_ID,
    ensure_account_layouts_initialized,
    ensure_config_accounts_imported,
    public_accounts,
)
from app.ai import (
    SUBTITLE_CANDIDATE_COUNT,
    TITLE_CANDIDATE_COUNT,
    clean_candidate_text,
)
from app.ai.model_registry import public_models
from app.ai.openai_compat import is_junk_title_or_subtitle
from app.config import database_target, load_config
from app.db import Database
from app.services.auth import AuthService
from app.services.onboarding import OnboardingService
from app.ui.loading import DEFAULT_REQUEST_MESSAGE, get_request_loading

logger = logging.getLogger(__name__)

STATUS_LABEL = {
    "pending": "等待中",
    "ingesting": "抓取原文",
    "rewriting": "AI 改写中",
    "title_optimizing": "优化标题",
    "rendering": "排版渲染",
    "injecting": "写入草稿",
    "ready_for_review": "待选标题/预览",
    "drafted": "已写入草稿箱",
    "published": "已发布",
    "failed": "失败",
    "cancelled": "已停止",
}


def set_button_loading(
    button: Any,
    loading: bool,
    message: str = DEFAULT_REQUEST_MESSAGE,
) -> None:
    """Show button feedback and a blocking overlay for an API request."""
    if button is None or bool(getattr(button, "is_deleted", False)):
        return
    client = getattr(button, "client", None)
    if client is not None and bool(getattr(client, "is_deleted", False)):
        return
    if loading:
        button.props(add="loading")
        button.disable()
        get_request_loading(button, message).show(message)
    else:
        button.props(remove="loading")
        button.enable()
        overlay = getattr(button, "_request_loading_overlay", None)
        if overlay is not None:
            overlay.hide()


class AppState:
    """UI state and option providers, separate from NiceGUI view composition."""

    def __init__(self, *, recover_stale_work: bool = True) -> None:
        self.config = load_config()
        self.db = Database(database_target(self.config))
        self.auth = AuthService(self.db)
        default_admin = self.auth.ensure_default_admin()
        self.current_user: dict[str, Any] | None = None
        if recover_stale_work:
            recovered = self.db.recover_stale_jobs(older_than_minutes=30)
            if recovered:
                logger.warning("Recovered %s stale in-progress jobs", recovered)
            recovered_reviews = self.db.recover_stale_editorial_reviews(
                older_than_minutes=30
            )
            if recovered_reviews:
                logger.warning(
                    "Recovered %s stale editorial review operations",
                    recovered_reviews,
                )
        # Legacy config.yaml accounts belong to the original administrator,
        # never to whichever customer happens to open the page first.
        self.db.set_owner_user(str(default_admin["id"]))
        ensure_config_accounts_imported(self.db, self.config)
        ensure_account_layouts_initialized(self.db, self.config)
        self.db.set_owner_user(None)
        OnboardingService(self.db, self.config).migrate_legacy_state()
        self.busy = False
        self.wizard_job_id: int | None = None
        self.selected_topic = ""
        self.topic_source = "manual"
        self.pending_rewrite: dict[str, Any] | None = None
        self.model_selects: list[Any] = []
        self._model_select_client: Any | None = None
        self.account_selects: list[Any] = []
        self.account_option_refreshers: list[Callable[[], None]] = []
        self.task_center_refresh: Any | None = None

    @property
    def is_admin(self) -> bool:
        return str((self.current_user or {}).get("role") or "") == "admin"

    @property
    def current_user_id(self) -> str:
        return str((self.current_user or {}).get("id") or "").strip()

    def bind_user(self, user: dict[str, Any] | None) -> None:
        """Bind this page's customer data access to the authenticated user."""

        self.current_user = dict(user) if user else None
        self.db.set_owner_user(self.current_user_id)

    def remembered_account_ids(self) -> list[str]:
        """Return the last desktop target selection, filtered by current accounts."""
        try:
            saved = json.loads(
                self.db.get_user_setting("ui.last_target_account_ids") or "[]"
            )
        except (TypeError, ValueError):
            saved = []
        available = set(self.account_options())
        return [
            str(account_id)
            for account_id in saved
            if str(account_id) in available
        ]

    def remember_account_ids(self, account_ids: list[str]) -> None:
        self.db.set_user_setting(
            "ui.last_target_account_ids",
            json.dumps([str(item) for item in account_ids], ensure_ascii=False),
        )

    def reload_config(self) -> dict[str, Any]:
        owner_user_id = self.current_user_id
        self.config = load_config()
        self.db = Database(
            database_target(self.config),
            owner_user_id=owner_user_id,
        )
        self.auth = AuthService(self.db)
        self.auth.ensure_default_admin()
        if self.is_admin:
            ensure_config_accounts_imported(self.db, self.config)
        ensure_account_layouts_initialized(self.db, self.config)
        return self.config

    def model_options(
        self,
        *,
        include_default: bool = True,
        purpose: str = "text",
        default_label: str | None = None,
    ) -> dict[str, str]:
        """Return enabled model choices for one model purpose.

        End users only see the merchant-managed model pool stored in the shared
        database. Legacy ``config.yaml`` providers remain available to offline
        migration code, but must not leak into desktop selectors.
        """

        if purpose not in {"text", "image"}:
            raise ValueError(f"unsupported model purpose: {purpose}")
        options = {
            str(item["id"]): f'{item["name"]} · {item["model"]}'
            for item in public_models(
                self.db,
                enabled_only=True,
                purpose=purpose,
            )
        }
        if not include_default:
            return options
        fallback_label = (
            "使用系统默认图片模型"
            if purpose == "image"
            else "使用系统默认模型"
        )
        return {"": default_label or fallback_label, **options}

    def register_model_select(
        self,
        select: Any,
        *,
        include_default: bool = True,
        purpose: str = "text",
        default_label: str | None = None,
        owner: Any | None = None,
    ) -> Any:
        """Register a model selector owned by this page's client.

        ``AppState`` is created once per desktop page. Keeping the element
        registry on that page state, and rejecting elements from another
        NiceGUI client, prevents a save in one browser window from mutating UI
        elements in another window.
        """

        if purpose not in {"text", "image"}:
            raise ValueError(f"unsupported model purpose: {purpose}")
        select_client = getattr(select, "client", None)
        if self._model_select_client is None and select_client is not None:
            self._model_select_client = select_client
        elif (
            select_client is not None
            and self._model_select_client is not None
            and select_client is not self._model_select_client
        ):
            logger.warning("ignored model selector from a different UI client")
            return select
        self.model_selects = [
            item
            for item in self.model_selects
            if not (
                isinstance(item, dict)
                and item.get("select") is select
            )
        ]
        self.model_selects.append(
            {
                "select": select,
                "include_default": include_default,
                "purpose": purpose,
                "default_label": default_label,
                "owner": owner,
                "client": select_client,
            }
        )
        if (
            owner is not None
            and hasattr(owner, "on_value_change")
            and not bool(getattr(owner, "_model_refresh_bound", False))
        ):
            owner._model_refresh_bound = True

            def refresh_when_reopened(event: Any) -> None:
                if bool(getattr(event, "value", False)):
                    self.register_model_select(
                        select,
                        include_default=include_default,
                        purpose=purpose,
                        default_label=default_label,
                        owner=owner,
                    )
                    self.refresh_model_selects()

            owner.on_value_change(refresh_when_reopened)
        return select

    def refresh_model_selects(self) -> None:
        active: list[Any] = []
        for registration in list(self.model_selects):
            if isinstance(registration, dict):
                select = registration.get("select")
                include_default = bool(
                    registration.get("include_default", True)
                )
                purpose = str(registration.get("purpose") or "text")
                default_label = registration.get("default_label")
                owner = registration.get("owner")
                registered_client = registration.get("client")
            else:
                # Backwards compatibility for tests and extensions which used
                # the original ``(select, include_default)`` tuple directly.
                select, include_default = registration
                purpose = "text"
                default_label = None
                owner = None
                registered_client = getattr(select, "client", None)
            if select is None or bool(getattr(select, "is_deleted", False)):
                continue
            if owner is not None and bool(getattr(owner, "is_deleted", False)):
                continue
            if (
                owner is not None
                and not bool(getattr(owner, "value", True))
            ):
                # Closed dialogs are refreshed by the value-change hook when
                # they are reopened. Avoid touching their client meanwhile.
                continue
            if (
                registered_client is not None
                and bool(getattr(registered_client, "is_deleted", False))
            ):
                continue
            try:
                options = self.model_options(
                    include_default=include_default,
                    purpose=purpose,
                    default_label=default_label,
                )
                current_value = getattr(select, "value", None)
                if current_value not in options:
                    current_value = "" if "" in options else None
                select.set_options(options, value=current_value)
                active.append(registration)
            except Exception:  # noqa: BLE001
                logger.exception("refresh model selector failed")
                active.append(registration)
        self.model_selects = active

    def account_options(self, *, include_default: bool = True) -> dict[str, str]:
        accounts = public_accounts(self.db, enabled_only=True)
        options = {
            str(item["id"]): f'{item["name"]} · {item["model_name"]}'
            for item in accounts
            if bool(item.get("has_model"))
        }
        if include_default:
            configured_app_id = str((self.config.get("wechat") or {}).get("app_id") or "")
            imported = any(
                str(item.get("app_id") or "") == configured_app_id
                for item in accounts
            )
            if configured_app_id and not imported:
                return {DEFAULT_ACCOUNT_ID: "系统默认公众号", **options}
        return options

    def refresh_account_selects(self) -> None:
        for select, include_default in list(self.account_selects):
            try:
                options = self.account_options(include_default=include_default)
                current = [value for value in (select.value or []) if value in options]
                select.set_options(options, value=current)
            except Exception:  # noqa: BLE001
                logger.exception("refresh account selector failed")
        for refresher in list(self.account_option_refreshers):
            try:
                refresher()
            except Exception:  # noqa: BLE001
                logger.exception("refresh custom account options failed")

    def register_account_option_refresher(
        self,
        refresher: Callable[[], None],
    ) -> None:
        """Register a selector whose account eligibility differs from the workbench.

        The workbench only accepts enabled accounts with a bound text model.
        Settings panels may need to show an enabled account before its model is
        configured, so they supply their own option refresh callback instead of
        reusing :meth:`account_options`.
        """

        if refresher not in self.account_option_refreshers:
            self.account_option_refreshers.append(refresher)


def clean_titles(job: dict[str, Any]) -> list[str]:
    raw = list(job.get("title_candidates") or []) + list(job.get("titles") or [])
    return _clean_candidate_options(
        raw,
        limit=TITLE_CANDIDATE_COUNT,
        min_length=6,
    )


def clean_subtitles(job: dict[str, Any]) -> list[str]:
    return _clean_candidate_options(
        list(job.get("subtitles") or []),
        limit=SUBTITLE_CANDIDATE_COUNT,
        min_length=2,
    )


def _clean_candidate_options(
    raw: list[Any],
    *,
    limit: int,
    min_length: int,
) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        title = clean_candidate_text(item)
        if len(title) < min_length or is_junk_title_or_subtitle(title):
            continue
        if title in seen:
            continue
        seen.add(title)
        output.append(title)
        if len(output) >= limit:
            break
    return output
