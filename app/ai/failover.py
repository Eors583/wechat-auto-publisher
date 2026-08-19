from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

from app.db import Database

from . import (
    RewriteResult,
    SUBTITLE_CANDIDATE_COUNT,
    TITLE_CANDIDATE_COUNT,
    TitleResult,
    build_rewrite_user_prompt,
    build_title_user_prompt,
    clean_candidate_list,
    enforce_emphasis_rules,
    quality_check,
)
from .askmany import AskManyClient
from .gemini import GeminiClient
from .manus import (
    ManusAPIError,
    ManusClient,
    is_non_retryable_manus_error,
)
from .openai_compat import (
    OpenAICompatClient,
    is_junk_title_or_subtitle,
    is_overloaded_error,
)

logger = logging.getLogger(__name__)

HARD_MIN_BODY_CHARS = 2000

# 国内常用 OpenAI 兼容端点预设
DOMESTIC_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "api_base": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "env_key": "QWEN_API_KEY",
    },
    "moonshot": {
        "api_base": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-128k",
        "env_key": "MOONSHOT_API_KEY",
    },
    "zhipu": {
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "env_key": "ZHIPU_API_KEY",
    },
}


class FailoverRewriter:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        db: Database | None = None,
    ) -> None:
        self.config = config
        ai = config.get("ai", {})
        self.primary = ai.get("primary", "deepseek")
        self.fallback = ai.get("fallback", "qwen")
        self.max_retries = int(ai.get("max_retries_per_model", 2))
        # 运营硬规则：无论配置如何调整，正文都不得少于 2000 字。
        self.min_body_chars = max(
            HARD_MIN_BODY_CHARS,
            int(ai.get("min_body_chars", HARD_MIN_BODY_CHARS)),
        )
        self.max_similarity = float(ai.get("max_similarity", 0.72))
        self.rewrite_prompt = ai.get("rewrite_prompt", "")
        self.title_prompt = ai.get("title_prompt", "")

        manus_cfg = ai.get("manus", {})
        gemini_cfg = ai.get("gemini", {})
        askmany_cfg = ai.get("askmany", {})

        self._clients: dict[str, Any] = {
            "manus": ManusClient(
                api_key=str(manus_cfg.get("api_key") or ""),
                api_base=str(manus_cfg.get("api_base") or "https://api.manus.ai"),
                model=str(manus_cfg.get("model") or "manus-1.6"),
                timeout=float(manus_cfg.get("timeout_seconds") or 600),
            ),
            "gemini": GeminiClient(
                api_key=str(gemini_cfg.get("api_key") or ""),
                model=str(gemini_cfg.get("model") or "gemini-2.0-flash"),
            ),
        }

        for name, preset in DOMESTIC_PRESETS.items():
            cfg = ai.get(name, {}) or {}
            self._clients[name] = OpenAICompatClient(
                api_key=str(cfg.get("api_key") or ""),
                api_base=str(cfg.get("api_base") or preset["api_base"]),
                model=str(cfg.get("model") or preset["model"]),
                provider_name=name,
            )

        # 界面中由用户添加的模型。ID 是稳定选择值，名称仅用于日志展示。
        for model_id, custom_cfg in (ai.get("custom_models") or {}).items():
            provider_type = str(custom_cfg.get("provider_type") or "openai_compatible")
            if provider_type == "local_openai_compatible":
                if db is None:
                    raise ValueError("本地模型需要绑定用户数据库连接")
                from .local_browser import LocalBrowserCompatClient

                self._clients[model_id] = LocalBrowserCompatClient(
                    db=db,
                    model_id=str(custom_cfg.get("id") or model_id),
                    model=str(custom_cfg.get("model") or ""),
                    provider_name=str(custom_cfg.get("name") or model_id),
                )
            elif provider_type == "manus":
                self._clients[model_id] = ManusClient(
                    api_key=str(custom_cfg.get("api_key") or ""),
                    api_base=str(
                        custom_cfg.get("api_base") or "https://api.manus.ai"
                    ),
                    model=str(custom_cfg.get("model") or "manus-1.6"),
                    timeout=float(custom_cfg.get("timeout_seconds") or 600),
                )
            elif provider_type == "gemini":
                self._clients[model_id] = GeminiClient(
                    api_key=str(custom_cfg.get("api_key") or ""),
                    model=str(custom_cfg.get("model") or "gemini-2.0-flash"),
                )
            else:
                self._clients[model_id] = OpenAICompatClient(
                    api_key=str(custom_cfg.get("api_key") or ""),
                    api_base=str(custom_cfg.get("api_base") or ""),
                    model=str(custom_cfg.get("model") or ""),
                    provider_name=str(custom_cfg.get("name") or model_id),
                )

        # 自定义 OpenAI 兼容供应商
        custom = ai.get("openai", {}) or {}
        if custom.get("api_base") or custom.get("api_key"):
            self._clients["openai"] = OpenAICompatClient(
                api_key=str(custom.get("api_key") or ""),
                api_base=str(custom.get("api_base") or "https://api.openai.com/v1"),
                model=str(custom.get("model") or "gpt-4o-mini"),
                provider_name="openai",
            )

        self.askmany = AskManyClient(
            api_key=str(askmany_cfg.get("api_key") or ""),
            api_base=str(askmany_cfg.get("api_base") or "https://api.askmany.ai"),
            model=str(askmany_cfg.get("model") or "default"),
        )

    def rewrite(self, topic: str, raw_content: str) -> RewriteResult:
        prompt = build_rewrite_user_prompt(topic, raw_content, self.rewrite_prompt)
        providers: list[str] = []
        for name in (self.primary, self.fallback):
            if name and name not in providers:
                providers.append(name)

        errors: list[str] = []
        for provider in providers:
            client = self._clients.get(provider)
            if not client:
                errors.append(f"{provider}: unknown provider")
                continue
            for attempt in range(1, self.max_retries + 1):
                try:
                    logger.info("Rewrite via %s attempt %s", provider, attempt)
                    if hasattr(client, "rewrite_longform"):
                        result = client.rewrite_longform(
                            topic,
                            raw_content,
                            instruction=self.rewrite_prompt,
                            title_instruction=self.title_prompt,
                            min_chars=self.min_body_chars,
                            target_chars=max(self.min_body_chars + 500, 2500),
                        )
                    else:
                        result = client.rewrite(prompt)
                        if len(re.sub(r"\s+", "", result.body or "")) < self.min_body_chars and hasattr(
                            client, "expand_rewrite"
                        ):
                            expanded = client.expand_rewrite(
                                topic,
                                result.body,
                                target_chars=max(self.min_body_chars + 500, 2500),
                            )
                            if not expanded.titles and result.titles:
                                expanded.titles = result.titles
                            if not expanded.subtitles and result.subtitles:
                                expanded.subtitles = result.subtitles
                            if not expanded.digest and result.digest:
                                expanded.digest = result.digest
                            result = expanded
                    result.titles = [
                        item
                        for item in clean_candidate_list(
                            result.titles,
                        )
                        if not is_junk_title_or_subtitle(item)
                    ][:TITLE_CANDIDATE_COUNT]
                    result.subtitles = [
                        item
                        for item in clean_candidate_list(
                            result.subtitles,
                        )
                        if not is_junk_title_or_subtitle(item)
                    ][:SUBTITLE_CANDIDATE_COUNT]
                    result.body = enforce_emphasis_rules(result.body)
                    quality_check(
                        result,
                        raw_content,
                        min_body_chars=self.min_body_chars,
                        max_similarity=self.max_similarity,
                        required_titles=TITLE_CANDIDATE_COUNT,
                        required_subtitles=SUBTITLE_CANDIDATE_COUNT,
                    )
                    result.provider = provider
                    return result
                except Exception as exc:  # noqa: BLE001
                    msg = f"{provider}#{attempt}: {exc}"
                    logger.warning(msg)
                    errors.append(msg)
                    if is_non_retryable_manus_error(exc):
                        logger.warning(
                            "%s returned non-retryable Manus error %s; "
                            "skipping the remaining attempts",
                            provider,
                            (
                                exc.code
                                if isinstance(exc, ManusAPIError)
                                else "invalid_argument"
                            ),
                        )
                        break
                    if attempt >= self.max_retries:
                        continue
                    if is_overloaded_error(exc):
                        # 服务端过载：多等一会再重试
                        time.sleep(min(8 * attempt, 45))
                    else:
                        time.sleep(min(2**attempt, 8))
        detail = " | ".join(errors)
        if any(is_overloaded_error(e) for e in errors):
            raise RuntimeError(
                "当前 AI 服务过载（429），已自动重试仍失败。"
                "请稍等 1–2 分钟后再点「开始改写」。详情：" + detail
            )
        if len(providers) == 1:
            raise RuntimeError("Rewrite provider failed: " + detail)
        raise RuntimeError("All rewrite providers failed: " + detail)

    def prompt_trace(self, provider: str = "") -> dict[str, Any]:
        """Return non-sensitive prompt fingerprints for reproducible job audits."""
        rewrite_prompt = str(self.rewrite_prompt or "")
        title_prompt = str(self.title_prompt or "")
        client = self._clients.get(provider)
        generation_mode = (
            "longform_staged"
            if client is not None and hasattr(client, "rewrite_longform")
            else (
                "single_pass_structured"
                if provider == "manus"
                else "single_pass"
            )
        )
        return {
            "protocol_version": "rewrite-stages-v2",
            "generation_mode": generation_mode,
            "rewrite_prompt_sha256": hashlib.sha256(
                rewrite_prompt.encode("utf-8")
            ).hexdigest(),
            "title_prompt_sha256": hashlib.sha256(
                title_prompt.encode("utf-8")
            ).hexdigest(),
            "rewrite_prompt_chars": len(rewrite_prompt),
            "title_prompt_chars": len(title_prompt),
        }

    def optimize_titles(self, body: str, fallback_titles: list[str] | None = None) -> TitleResult:
        prompt = build_title_user_prompt(body, self.title_prompt)
        errors: list[str] = []

        # 1) AskMany（若配置了）
        if getattr(self.askmany, "api_key", None):
            for attempt in range(1, 3):
                try:
                    result = self.askmany.optimize_titles(prompt)
                    if len(result.titles) >= TITLE_CANDIDATE_COUNT:
                        return result
                    raise RuntimeError(
                        f"标题候选不足 {TITLE_CANDIDATE_COUNT} 个："
                        f"当前只有 {len(result.titles)} 个"
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"askmany#{attempt}: {exc}")
                    time.sleep(min(2**attempt, 6))

        # 2) 用主/备国内模型优化标题
        for provider in (self.primary, self.fallback):
            client = self._clients.get(provider)
            if not client or not hasattr(client, "optimize_titles"):
                continue
            try:
                result = client.optimize_titles(prompt)
                if len(result.titles) >= TITLE_CANDIDATE_COUNT:
                    return result
                raise RuntimeError(
                    f"标题候选不足 {TITLE_CANDIDATE_COUNT} 个："
                    f"当前只有 {len(result.titles)} 个"
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{provider}-title: {exc}")

        if fallback_titles:
            logger.warning("Title optimize failed, using rewrite titles: %s", errors)
            return TitleResult(
                titles=list(fallback_titles)[:TITLE_CANDIDATE_COUNT],
                provider="fallback",
            )
        raise RuntimeError("Title optimize failed: " + " | ".join(errors))


class TitleScorer:
    """Reserved CTR-style scorer interface."""

    def score(self, titles: list[str], body: str) -> list[tuple[str, float]]:
        scored: list[tuple[str, float]] = []
        for i, title in enumerate(titles):
            length_bonus = 1.0 if 12 <= len(title) <= 28 else 0.7
            digit_bonus = 0.1 if any(ch.isdigit() for ch in title) else 0.0
            scored.append((title, length_bonus + digit_bonus - i * 0.01))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
