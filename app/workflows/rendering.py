from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.ads import render_ad_html, select_ad
from app.ai.openai_compat import is_junk_title_or_subtitle
from app.cover import generate_article_cover, pick_random_image_media_id, resolve_cover
from app.inline_images import insert_inline_images, resolve_inline_images
from app.render import finalize_article_html, make_digest
from app.services.failures import sanitize_failure_text
from app.wechat.template_snapshot import (
    capture_template_snapshot,
    load_template_snapshot,
)

from .context import WorkflowContext

logger = logging.getLogger(__name__)


class RenderingStep:
    """Transform rewritten content into final WeChat-compatible HTML."""

    def __init__(self, context: WorkflowContext) -> None:
        self.context = context

    def render(
        self,
        job: dict[str, Any],
        *,
        cover_media_id: str | None = None,
    ) -> dict[str, Any]:
        job_id = int(job["id"])
        config = self.context.config
        db = self.context.db
        db.update_job(job_id, status="rendering", step="render", error=None)
        template_cfg = config.get("template") or {}
        editor_cfg = dict(config.get("editor_template") or {})
        editor_cfg["_root"] = config.get("_root")
        client = self.context.wechat_client()
        snapshot = self._template_snapshot(client, editor_cfg)

        ad = (
            select_ad(db, config.get("ads") or {})
            if template_cfg.get("show_inline_ad", False)
            else None
        )
        generated_html = self.context.renderer.render(
            body=job.get("body") or "",
            subtitle=_safe_subtitle(job.get("selected_subtitle")),
            ad_html=render_ad_html(ad) if ad else "",
            show_byline=False if snapshot else None,
        )

        meta = dict(job.get("meta") or {})
        inline_assets, inline_warnings = self._inline_assets(job, meta, client)
        generated_html = insert_inline_images(generated_html, inline_assets)
        finalized = finalize_article_html(
            generated_html,
            editor_cfg,
            snapshot=snapshot,
            load_local_snapshot=False,
        )
        thumb, generated_cover, cover_warning = self._resolve_cover(
            job, client, cover_media_id
        )

        if generated_cover:
            meta["generated_cover"] = generated_cover
            meta["generated_cover_active"] = True
            meta.pop("cover_image_warning", None)
        if cover_warning:
            meta["cover_image_warning"] = cover_warning

        meta["editor_template_applied"] = bool(snapshot)
        meta["layout_quality"] = {
            "errors": finalized.report.errors,
            "warnings": finalized.report.warnings,
            "paragraph_count": finalized.report.paragraph_count,
            "image_count": finalized.report.image_count,
            "long_paragraph_count": finalized.report.long_paragraph_count,
        }
        if inline_warnings:
            meta["layout_quality"]["warnings"] = list(
                dict.fromkeys(
                    list(meta["layout_quality"].get("warnings") or [])
                    + inline_warnings
                )
            )
        if snapshot:
            meta["editor_template_snapshot"] = str(snapshot.path)
            meta["editor_template_sha256"] = hashlib.sha256(
                snapshot.content.encode("utf-8")
            ).hexdigest()
            if snapshot.source_media_id:
                meta["editor_template_source_media_id"] = snapshot.source_media_id
        else:
            meta.pop("editor_template_snapshot", None)
            meta.pop("editor_template_sha256", None)
            meta.pop("editor_template_source_media_id", None)

        db.update_job(
            job_id,
            html_content=finalized.html,
            digest=str(job.get("digest") or "").strip()
            or make_digest(job.get("body") or ""),
            thumb_media_id=thumb,
            ad_id=(ad or {}).get("id") if ad else None,
            meta_json=meta,
        )
        return self.context.require_job(job_id)

    @staticmethod
    def _template_snapshot(client: Any, editor_cfg: dict[str, Any]) -> Any:
        if not editor_cfg.get("enabled", False):
            return None
        snapshot = load_template_snapshot(editor_cfg)
        if snapshot is None and editor_cfg.get("auto_capture_from_drafts", True):
            try:
                snapshot = capture_template_snapshot(client, editor_cfg)
            except RuntimeError as exc:
                logger.info("editor template snapshot is not ready: %s", exc)
        return snapshot

    def _inline_assets(
        self,
        job: dict[str, Any],
        meta: dict[str, Any],
        client: Any,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        settings = dict(self.context.config.get("inline_images") or {})
        assets = list(meta.get("inline_images") or [])
        warnings = list(meta.get("inline_image_warnings") or [])
        if settings.get("enabled", False) and not meta.get("inline_images_resolved"):
            assets, warnings = resolve_inline_images(
                body=job.get("body") or "",
                settings=settings,
                client=client,
                db=self.context.db,
                root=self.context.config.get("_root") or ".",
                job_id=int(job["id"]),
                source_images=list(meta.get("source_images") or []),
            )
            meta["inline_images_resolved"] = True
            meta["inline_images"] = assets
            meta["inline_image_warnings"] = warnings
        return assets, warnings

    def _resolve_cover(
        self,
        job: dict[str, Any],
        client: Any,
        cover_media_id: str | None,
    ) -> tuple[str, dict[str, str] | None, str]:
        cache_key = _cover_cache_key(job)
        fallback = self._last_successful_cover(job, cache_key)
        meta = dict(job.get("meta") or {})
        explicit_cover = str(
            cover_media_id
            or meta.get("cover_media_id")
            or job.get("thumb_media_id")
            or ""
        ).strip()

        # Re-renders and manually selected material must never create another
        # billable image request. A new cover is generated only when no cover has
        # already been chosen for this job.
        if explicit_cover:
            return explicit_cover, None, ""

        image_settings = dict(self.context.config.get("inline_images") or {})
        if (
            bool(image_settings.get("generate_cover", True))
            and str(image_settings.get("image_model_id") or "").strip()
        ):
            try:
                generated = generate_article_cover(
                    title=str(job.get("selected_title") or job.get("topic") or ""),
                    body=str(job.get("body") or ""),
                    settings=image_settings,
                    db=self.context.db,
                    client=client,
                    root=self.context.config.get("_root") or ".",
                    job_id=int(job["id"]),
                    instruction=str(meta.get("cover_revision_instruction") or ""),
                )
                media_id = str(generated["media_id"])
                if cache_key:
                    self.context.db.set_setting(cache_key, media_id)
                return media_id, generated, ""
            except Exception as exc:  # noqa: BLE001
                safe_error = sanitize_failure_text(exc)
                logger.error(
                    "AI cover generation failed; falling back to material "
                    "library: %s",
                    safe_error,
                )
                cover_warning = (
                    f"AI 封面生成失败，已改用公众号素材：{safe_error}"
                )
        else:
            cover_warning = ""

        def pick_and_cache() -> str:
            media_id = pick_random_image_media_id(client)
            if cache_key:
                self.context.db.set_setting(cache_key, media_id)
            return media_id

        thumb = resolve_cover(
            topic=job.get("topic") or "",
            config=self.context.config,
            override_media_id=None,
            pick_from_library=pick_and_cache,
            fallback_media_id=fallback,
        )
        if cache_key and thumb:
            self.context.db.set_setting(cache_key, thumb)
        return thumb, None, cover_warning

    def _last_successful_cover(
        self, job: dict[str, Any], cache_key: str | None
    ) -> str | None:
        db = self.context.db
        if cache_key:
            cached = str(db.get_setting(cache_key) or "").strip()
            if cached:
                return cached
        account_id = str((job.get("meta") or {}).get("official_account_id") or "").strip()
        if not account_id:
            return None
        for previous in db.list_jobs(limit=300):
            if int(previous.get("id") or 0) == int(job.get("id") or 0):
                continue
            previous_account = str(
                (previous.get("meta") or {}).get("official_account_id") or ""
            ).strip()
            media_id = str(previous.get("thumb_media_id") or "").strip()
            if previous_account == account_id and media_id:
                if cache_key:
                    db.set_setting(cache_key, media_id)
                return media_id
        return None


def _cover_cache_key(job: dict[str, Any]) -> str | None:
    account_id = str((job.get("meta") or {}).get("official_account_id") or "").strip()
    return f"wechat:last_cover:{account_id}" if account_id else None


def _safe_subtitle(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or is_junk_title_or_subtitle(text):
        return None
    return text
