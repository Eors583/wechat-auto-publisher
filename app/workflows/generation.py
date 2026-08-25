from __future__ import annotations

import logging
from typing import Any

from app.ai import (
    SUBTITLE_CANDIDATE_COUNT,
    TITLE_CANDIDATE_COUNT,
    clean_candidate_text,
    normalize_digest,
    normalize_model_body,
)
from app.render import make_digest
from app.ai.openai_compat import is_junk_title_or_subtitle
from app.providers.ingest import ingest_text, ingest_url, ingest_urls

from .context import WorkflowContext


logger = logging.getLogger(__name__)


class GenerationSteps:
    """Source ingestion, AI rewrite and title optimization only."""

    def __init__(self, context: WorkflowContext) -> None:
        self.context = context

    def ingest(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = int(job["id"])
        db = self.context.db
        db.update_job(job_id, status="ingesting", step="ingest", error=None)
        raw = job.get("raw_content")
        url = job.get("source_url")
        meta = dict(job.get("meta") or {})
        reference_urls = [str(item) for item in meta.get("reference_urls") or [] if item]
        source_mode = str(meta.get("source_mode") or "")
        if reference_urls:
            ingested = ingest_urls(reference_urls)
        elif raw:
            ingested = ingest_text(raw, title=job.get("raw_title") or "")
        elif url:
            ingested = ingest_url(url)
        elif source_mode == "topic":
            ingested = ingest_text(
                f'请围绕“{job.get("topic") or "未命名话题"}”原创文章，'
                "使用可靠的通识信息，不虚构具体数据、人物引语或政策出处。",
                title=str(job.get("topic") or ""),
            )
        else:
            raise ValueError("Either raw text or URL is required for ingest")

        constraints: list[str] = []
        required_facts = str(meta.get("required_facts") or "").strip()
        if required_facts:
            constraints.append("【必须保留的事实】\n" + required_facts)
        intensity = str(meta.get("rewrite_intensity") or "standard")
        constraints.append(
            "【改写强度】"
            + {"light": "轻度改写，最大限度保留事实与结构", "strong": "深度重构表达与结构，但不得改变事实"}.get(
                intensity, "标准改写，保留事实并优化结构与表达"
            )
        )
        content = "\n\n".join([*constraints, ingested.content])

        db.update_job(
            job_id,
            topic=job.get("topic") or ingested.title or "未命名话题",
            raw_content=content,
            raw_title=ingested.title,
            source_url=ingested.source_url or url,
            meta_json={
                **(job.get("meta") or {}),
                "source_images": list(dict.fromkeys(ingested.images or []))[:12],
            },
        )
        return self.context.require_job(job_id)

    def rewrite(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = int(job["id"])
        db = self.context.db
        db.update_job(job_id, status="rewriting", step="rewrite", error=None)
        result = self.context.rewriter.rewrite(
            job.get("topic") or "未命名话题",
            job.get("raw_content") or "",
        )
        titles = _unique_titles(
            list(result.titles or []),
            limit=TITLE_CANDIDATE_COUNT,
        )
        subtitles = _unique_titles(
            list(result.subtitles or []),
            limit=SUBTITLE_CANDIDATE_COUNT,
        )
        if not titles:
            raise ValueError("没有生成有效的主标题候选")
        if not subtitles:
            raise ValueError("没有生成有效的副标题候选")
        db.update_job(
            job_id,
            body=normalize_model_body(result.body),
            digest=normalize_digest(result.digest) or make_digest(result.body),
            titles_json=titles,
            subtitles_json=subtitles,
            selected_title=titles[0] if titles else None,
            selected_subtitle=None,
            meta_json={
                **(job.get("meta") or {}),
                "rewrite_provider": result.provider,
                "prompt_trace": self.context.rewriter.prompt_trace(result.provider),
            },
        )
        return self.context.require_job(job_id)

    def optimize_titles(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = int(job["id"])
        db = self.context.db
        enabled = bool((self.context.config.get("ai") or {}).get("title_optimize", True))
        db.update_job(job_id, status="title_optimizing", step="title_optimize", error=None)
        fallback = list(job.get("titles") or [])
        body = job.get("body") or ""

        if not enabled:
            candidates = _unique_titles(
                fallback,
                limit=TITLE_CANDIDATE_COUNT,
            )
        else:
            try:
                result = self.context.rewriter.optimize_titles(
                    body, fallback_titles=fallback
                )
                candidates = [
                    *(result.titles or []),
                    *fallback,
                ]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Title optimize failed, fallback to rewrite titles: %s", exc)
                candidates = fallback[:TITLE_CANDIDATE_COUNT]
            candidates = _unique_titles(
                candidates,
                limit=TITLE_CANDIDATE_COUNT,
            )
            if not candidates:
                candidates = _unique_titles(
                    fallback,
                    limit=TITLE_CANDIDATE_COUNT,
                )
        if len(candidates) != TITLE_CANDIDATE_COUNT:
            raise ValueError(
                f"标题优化后必须保留 {TITLE_CANDIDATE_COUNT} 个候选，"
                f"当前只有 {len(candidates)} 个"
            )

        scored = self.context.scorer.score(candidates, body) if candidates else []
        selected = (
            scored[0][0]
            if scored
            else (candidates[0] if candidates else job.get("selected_title"))
        )
        db.update_job(
            job_id,
            title_candidates_json=candidates,
            selected_title=selected,
        )
        return self.context.require_job(job_id)


def _unique_titles(values: list[Any], *, limit: int) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = clean_candidate_text(str(value or ""))
        key = clean.casefold()
        if not clean or key in seen or is_junk_title_or_subtitle(clean):
            continue
        seen.add(key)
        candidates.append(clean)
        if len(candidates) >= limit:
            break
    return candidates
