from __future__ import annotations

import asyncio
import hmac
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.editorial_reviews import create_editorial_review_router
from app.config import load_config
from app.services import (
    BatchService,
    FollowedContentService,
    TopicSourceService,
    get_batch_service,
)


logger = logging.getLogger(__name__)


def _run_feishu_bot(
    config: dict[str, Any],
    service: BatchService,
    holder: dict[str, Any],
) -> None:
    """Run the Feishu SDK on its own event loop.

    lark-oapi captures the current event loop when its websocket module is
    imported.  Importing it from Uvicorn's lifespan thread makes it capture
    Uvicorn's already-running loop, so ws.Client.start() fails with
    ``This event loop is already running``.  Create and install a dedicated
    loop before importing the bot module.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    holder["status"] = "starting"
    try:
        from app.feishu.bot import FeishuBot

        bot = FeishuBot(config, service)
        holder["bot"] = bot
        holder["status"] = "connecting"
        bot.start()
    except Exception as exc:  # noqa: BLE001
        holder["status"] = "failed"
        holder["error"] = str(exc)
        logger.exception("Feishu long connection stopped")
    finally:
        asyncio.set_event_loop(None)
        if not loop.is_running() and not loop.is_closed():
            loop.close()


class CreateBatchRequest(BaseModel):
    topic: str | None = None
    source_mode: str | None = None
    reference_urls: list[str] = Field(default_factory=list)
    required_facts: str | None = None
    rewrite_intensity: str | None = None
    source_url: str | None = None
    raw_content: str | None = None
    account_ids: list[str] = Field(min_length=1)
    requested_by: str | None = None
    chat_id: str | None = None


class SelectJobRequest(BaseModel):
    title_index: int = Field(ge=0)
    subtitle_index: int | None = Field(default=None, ge=0)


class UpdateJobContentRequest(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    digest: str | None = None
    body: str | None = None


class RegenerateParagraphRequest(BaseModel):
    paragraph_index: int = Field(ge=0)
    instruction: str = Field(min_length=1, max_length=2000)


class RegenerateInlineImageRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


class RegenerateCoverRequest(BaseModel):
    instruction: str = Field(default="", max_length=2000)


class SelectCoverRequest(BaseModel):
    thumb_media_id: str = Field(min_length=1)


class TopicSourceRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1)
    source_type: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ManualTopicRequest(BaseModel):
    title: str = Field(min_length=1)
    url: str = ""
    summary: str = ""
    category: str = ""


class FollowedAccountRequest(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1)
    wechat_id: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    fetch_method: str = "public_search"
    sample_url: str = ""
    source_url: str = ""
    keywords: list[str] = Field(default_factory=list)
    is_owned: bool = False
    enabled: bool = True
    refresh_hours: int = Field(default=12, ge=1, le=720)


class FollowedArticleRequest(BaseModel):
    url: str = Field(min_length=1)
    followed_account_id: str | None = None
    source_channel: str = "api"


class FollowedArticleStateRequest(BaseModel):
    is_read: bool | None = None
    is_favorite: bool | None = None
    is_ignored: bool | None = None
    rewritten_batch_id: str | None = None


def create_api_app(
    config: dict[str, Any] | None = None,
    service: BatchService | None = None,
    *,
    start_feishu: bool = True,
) -> FastAPI:
    cfg = config or load_config()
    batch_service = service or get_batch_service(cfg)
    from app.feishu.settings import effective_feishu_settings

    cfg = dict(cfg)
    cfg["feishu"] = effective_feishu_settings(
        batch_service.db, dict(cfg.get("feishu") or {})
    )
    api_cfg = dict(cfg.get("api") or {})
    expected_token = str(api_cfg.get("token") or "").strip()
    bot_holder: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_feishu and bool((cfg.get("feishu") or {}).get("enabled", False)):
            threading.Thread(
                target=_run_feishu_bot,
                args=(cfg, batch_service, bot_holder),
                name="feishu-long-connection",
                daemon=True,
            ).start()
        yield

    app = FastAPI(
        title="公众号改写助手 API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.batch_service = batch_service
    app.state.config = cfg
    topic_service = TopicSourceService(batch_service.db, cfg)
    followed_service = FollowedContentService(batch_service.db, cfg)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not expected_token:
            return
        prefix = "Bearer "
        supplied = authorization[len(prefix):].strip() if authorization and authorization.startswith(prefix) else ""
        if not supplied or not hmac.compare_digest(supplied, expected_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API Token 无效",
            )

    app.include_router(
        create_editorial_review_router(batch_service, require_token)
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        from app.feishu.runtime import get_runtime

        feishu_enabled = bool(
            (cfg.get("feishu") or {}).get("enabled", False)
        )
        feishu_runtime = (
            get_runtime(batch_service.db)
            if feishu_enabled
            else {"status": "disabled"}
        )
        return {
            "ok": True,
            "service": "wechat-auto-publisher",
            "version": "1.0.0",
            "instance_root": str(cfg.get("_root") or ""),
            "feishu_enabled": feishu_enabled,
            "feishu_status": str(
                feishu_runtime.get("status") or "unknown"
            ),
            "feishu_error": (
                feishu_runtime.get("last_error")
                or bot_holder.get("error")
            ),
        }

    @app.get("/api/v1/accounts", dependencies=[Depends(require_token)])
    def accounts() -> list[dict[str, Any]]:
        return batch_service.list_accounts()

    @app.post("/api/v1/accounts/preflight", dependencies=[Depends(require_token)])
    def preflight_accounts_endpoint(
        account_ids: list[str],
        deep_model_check: bool = Query(default=False),
    ) -> list[dict[str, Any]]:
        return batch_service.preflight(
            account_ids, deep_model_check=deep_model_check
        )

    @app.get("/api/v1/topics/hot", dependencies=[Depends(require_token)])
    def recent_hot_topics(
        limit: int = Query(default=10, ge=1, le=30),
        refresh: bool = Query(default=True),
    ) -> dict[str, Any]:
        if refresh:
            topic_service.refresh()
        items = topic_service.list_topics(days=7, limit=limit)
        return {"days": 7, "count": len(items), "items": items}

    @app.get("/api/v1/topic-sources", dependencies=[Depends(require_token)])
    def list_topic_sources(enabled_only: bool = Query(default=False)) -> list[dict[str, Any]]:
        return topic_service.list_sources(enabled_only=enabled_only)

    @app.post("/api/v1/topic-sources", dependencies=[Depends(require_token)])
    def save_topic_source(payload: TopicSourceRequest) -> dict[str, Any]:
        try:
            return topic_service.save_source(payload.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/v1/topic-sources/{source_id}", dependencies=[Depends(require_token)])
    def delete_topic_source(source_id: str) -> dict[str, bool]:
        try:
            topic_service.delete_source(source_id)
            return {"ok": True}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/topic-sources/refresh", dependencies=[Depends(require_token)])
    def refresh_topic_sources(source_ids: list[str] = Query(default=[])) -> dict[str, Any]:
        return topic_service.refresh(source_ids or None)

    @app.get("/api/v1/topics", dependencies=[Depends(require_token)])
    def list_topics(
        source_ids: list[str] = Query(default=[]),
        days: int = Query(default=7, ge=1, le=365),
        keyword: str = "",
        favorite_only: bool = False,
        unused_only: bool = False,
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        return topic_service.list_topics(
            source_ids=source_ids or None,
            days=days,
            keyword=keyword,
            favorite_only=favorite_only,
            unused_only=unused_only,
            limit=limit,
        )

    @app.get("/api/v1/topics/search", dependencies=[Depends(require_token)])
    def search_topics(
        keyword: str = Query(min_length=1),
        source_ids: list[str] = Query(default=[]),
        days: int = Query(default=7, ge=1, le=365),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        try:
            return topic_service.search(
                keyword,
                source_ids or None,
                days=days,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/v1/topics/manual", dependencies=[Depends(require_token)])
    def add_manual_topic(payload: ManualTopicRequest) -> dict[str, Any]:
        try:
            return topic_service.add_manual_topic(**payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/followed-accounts", dependencies=[Depends(require_token)])
    def list_followed_accounts(enabled_only: bool = Query(default=False)) -> list[dict[str, Any]]:
        return followed_service.list_accounts(enabled_only=enabled_only)

    @app.post("/api/v1/followed-accounts", dependencies=[Depends(require_token)])
    def save_followed_account(payload: FollowedAccountRequest) -> dict[str, Any]:
        try:
            return followed_service.save_account(payload.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/v1/followed-accounts/{account_id}", dependencies=[Depends(require_token)])
    def delete_followed_account(account_id: str) -> dict[str, bool]:
        followed_service.delete_account(account_id)
        return {"ok": True}

    @app.post("/api/v1/followed-accounts/{account_id}/refresh", dependencies=[Depends(require_token)])
    def refresh_followed_account(account_id: str) -> dict[str, Any]:
        try:
            return followed_service.discover_account(account_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/followed-accounts/refresh", dependencies=[Depends(require_token)])
    def refresh_all_followed_accounts() -> dict[str, Any]:
        return followed_service.discover_all()

    @app.get("/api/v1/followed-articles", dependencies=[Depends(require_token)])
    def list_followed_articles(
        account_ids: list[str] = Query(default=[]),
        days: int = Query(default=7, ge=1, le=3650),
        keyword: str = "",
        unread_only: bool = False,
        favorite_only: bool = False,
        unrewritten_only: bool = False,
        include_ignored: bool = False,
        limit: int = Query(default=500, ge=1, le=2000),
    ) -> list[dict[str, Any]]:
        return followed_service.list_articles(
            account_ids=account_ids or None,
            days=days,
            keyword=keyword,
            unread_only=unread_only,
            favorite_only=favorite_only,
            unrewritten_only=unrewritten_only,
            include_ignored=include_ignored,
            limit=limit,
        )

    @app.post("/api/v1/followed-articles", dependencies=[Depends(require_token)])
    def add_followed_article(payload: FollowedArticleRequest) -> dict[str, Any]:
        try:
            return followed_service.add_article_url(**payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/v1/followed-articles/{article_id}", dependencies=[Depends(require_token)])
    def update_followed_article(
        article_id: str, payload: FollowedArticleStateRequest
    ) -> dict[str, Any]:
        try:
            return followed_service.update_article(
                article_id, **payload.model_dump(exclude_unset=True)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_token)],
    )
    def create_batch(payload: CreateBatchRequest) -> dict[str, Any]:
        try:
            return batch_service.create_batch(**payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/batches/{batch_id}", dependencies=[Depends(require_token)])
    def get_batch(
        batch_id: str,
        include_content: bool = Query(default=False),
    ) -> dict[str, Any]:
        try:
            return batch_service.get_batch(batch_id, include_content=include_content)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/batches", dependencies=[Depends(require_token)])
    def list_batches(
        limit: int = Query(default=100, ge=1, le=500),
        include_archived: bool = Query(default=False),
    ) -> list[dict[str, Any]]:
        return batch_service.list_batches(
            limit=limit, include_archived=include_archived
        )

    @app.put(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/selection",
        dependencies=[Depends(require_token)],
    )
    def select_job(
        batch_id: str,
        job_id: int,
        payload: SelectJobRequest,
    ) -> dict[str, Any]:
        try:
            return batch_service.select_job(batch_id, job_id, **payload.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/view",
        dependencies=[Depends(require_token)],
    )
    def view_job(batch_id: str, job_id: int) -> dict[str, Any]:
        try:
            return batch_service.mark_job_viewed(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/confirm",
        dependencies=[Depends(require_token)],
    )
    def confirm_job(batch_id: str, job_id: int) -> dict[str, Any]:
        try:
            return batch_service.confirm_job(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/needs-changes",
        dependencies=[Depends(require_token)],
    )
    def request_job_changes(batch_id: str, job_id: int) -> dict[str, Any]:
        try:
            return batch_service.request_job_changes(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/content",
        dependencies=[Depends(require_token)],
    )
    def update_job_content(
        batch_id: str, job_id: int, payload: UpdateJobContentRequest
    ) -> dict[str, Any]:
        try:
            return batch_service.update_job_content(
                batch_id, job_id, **payload.model_dump()
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/rerender",
        dependencies=[Depends(require_token)],
    )
    def rerender_job(batch_id: str, job_id: int) -> dict[str, Any]:
        try:
            return batch_service.rerender_job(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/paragraph",
        dependencies=[Depends(require_token)],
    )
    def regenerate_paragraph(
        batch_id: str, job_id: int, payload: RegenerateParagraphRequest
    ) -> dict[str, Any]:
        try:
            return batch_service.regenerate_paragraph(
                batch_id,
                job_id,
                payload.paragraph_index,
                instruction=payload.instruction,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/inline-images/regenerate",
        dependencies=[Depends(require_token)],
    )
    def regenerate_job_inline_images(batch_id: str, job_id: int) -> dict[str, Any]:
        try:
            return batch_service.regenerate_inline_images(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/inline-images/{image_index}/regenerate",
        dependencies=[Depends(require_token)],
    )
    def regenerate_job_inline_image(
        batch_id: str,
        job_id: int,
        image_index: int,
        payload: RegenerateInlineImageRequest,
    ) -> dict[str, Any]:
        try:
            return batch_service.regenerate_inline_image(
                batch_id,
                job_id,
                image_index,
                instruction=payload.instruction,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/inline-images/{image_index}",
        dependencies=[Depends(require_token)],
    )
    def remove_job_inline_image(
        batch_id: str, job_id: int, image_index: int
    ) -> dict[str, Any]:
        try:
            return batch_service.remove_inline_image(batch_id, job_id, image_index)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/versions",
        dependencies=[Depends(require_token)],
    )
    def list_job_versions(batch_id: str, job_id: int) -> list[dict[str, Any]]:
        try:
            return batch_service.list_job_versions(batch_id, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/versions/{version_id}/restore",
        dependencies=[Depends(require_token)],
    )
    def restore_job_version(
        batch_id: str, job_id: int, version_id: int
    ) -> dict[str, Any]:
        try:
            return batch_service.restore_job_version(batch_id, job_id, version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/covers",
        dependencies=[Depends(require_token)],
    )
    def list_job_covers(
        batch_id: str,
        job_id: int,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, str]]:
        try:
            return batch_service.list_cover_options(
                batch_id, job_id, limit=limit, offset=offset
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/cover",
        dependencies=[Depends(require_token)],
    )
    def select_job_cover(
        batch_id: str, job_id: int, payload: SelectCoverRequest
    ) -> dict[str, Any]:
        try:
            return batch_service.select_job_cover(
                batch_id, job_id, payload.thumb_media_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/jobs/{job_id}/cover/generate",
        dependencies=[Depends(require_token)],
    )
    def regenerate_job_cover(
        batch_id: str,
        job_id: int,
        payload: RegenerateCoverRequest | None = None,
    ) -> dict[str, Any]:
        try:
            return batch_service.regenerate_cover(
                batch_id,
                job_id,
                instruction=payload.instruction if payload else "",
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/drafts",
        dependencies=[Depends(require_token)],
    )
    def inject_batch(batch_id: str) -> dict[str, Any]:
        try:
            return batch_service.inject_batch(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/cancel",
        dependencies=[Depends(require_token)],
    )
    def cancel_batch(batch_id: str) -> dict[str, Any]:
        try:
            return batch_service.cancel_batch(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/retry-failed",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_token)],
    )
    def retry_failed(batch_id: str) -> dict[str, Any]:
        try:
            return batch_service.retry_failed(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/copy",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_token)],
    )
    def copy_batch(batch_id: str) -> dict[str, Any]:
        try:
            return batch_service.copy_batch(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/batches/{batch_id}/archive",
        dependencies=[Depends(require_token)],
    )
    def archive_batch(batch_id: str) -> dict[str, Any]:
        try:
            return batch_service.archive_batch(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


def main() -> None:
    cfg = load_config()
    api_cfg = dict(cfg.get("api") or {})
    port_override = str(os.getenv("WECHAT_PUBLISHER_API_PORT") or "").strip()
    uvicorn.run(
        create_api_app(cfg),
        host=str(api_cfg.get("host") or "127.0.0.1"),
        port=int(port_override or api_cfg.get("port") or 18766),
        log_level=str(api_cfg.get("log_level") or "info"),
        log_config=None,
    )


if __name__ == "__main__":
    main()
