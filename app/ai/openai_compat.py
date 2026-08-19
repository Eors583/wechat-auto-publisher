from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from . import (
    EMPHASIS_PROMPT,
    RewriteResult,
    SUBTITLE_CANDIDATE_COUNT,
    TITLE_CANDIDATE_COUNT,
    TitleResult,
    clean_candidate_text,
    parse_rewrite_output,
    parse_title_output,
)

logger = logging.getLogger(__name__)


def _retry_after_seconds(resp: httpx.Response, attempt: int) -> float:
    """429/5xx 退避：优先 Retry-After，否则指数退避（最长 60s）。"""
    header = (resp.headers.get("retry-after") or "").strip()
    if header.isdigit():
        return max(1.0, min(float(header), 90.0))
    return min(float(2**attempt * 3), 60.0)


def is_overloaded_error(exc: BaseException | str) -> bool:
    text = str(exc).lower()
    return (
        "429" in text
        or "overloaded" in text
        or "engine_overloaded" in text
        or "rate limit" in text
        or "too many requests" in text
    )


def count_content_chars(text: str) -> int:
    """按中文习惯统计正文字数：去掉空白后的字符数。"""
    return len(re.sub(r"\s+", "", text or ""))


def _content_instruction_block(instruction: str) -> str:
    configured = str(instruction or "").strip() or "未配置额外运营写作要求。"
    return (
        "【运营配置的全局写作要求】\n"
        f"{configured}\n\n"
        "必须遵循其中有关选题、事实、观点、结构、语气、段落、字数、写作风格、"
        "小标题、重点加粗和禁止照搬的内容要求。配置中涉及 JSON、标题数组、"
        "副标题数组或同时返回多个字段的输出格式要求，不在正文阶段执行；"
        "具体输出格式以当前阶段协议为准。"
    )


def _title_instruction_block(instruction: str, title_instruction: str) -> str:
    global_instruction = str(instruction or "").strip() or "未配置额外全局要求。"
    configured_title = (
        str(title_instruction or "").strip() or "未配置额外标题要求。"
    )
    return (
        "【运营配置的全局要求】\n"
        f"{global_instruction}\n\n"
        "本阶段只采纳上述配置中与主标题、副标题、选题角度、事实准确性和表达风格"
        "有关的规则，不执行其中要求生成正文或正文达到指定字数的规则。\n\n"
        "【运营配置的标题要求】\n"
        f"{configured_title}\n\n"
        "运营配置中涉及候选数量、字段数量或 JSON 示例数量的旧规则一律不执行；"
        f"当前阶段必须生成恰好 {TITLE_CANDIDATE_COUNT} 个主标题和 "
        f"{SUBTITLE_CANDIDATE_COUNT} 个副标题。"
    )


class OpenAICompatClient:
    """OpenAI 兼容 Chat Completions（DeepSeek / 通义 / Kimi / 智谱等）。"""

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str,
        *,
        provider_name: str = "openai",
        timeout: float = 180.0,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.provider_name = provider_name
        self.timeout = timeout

    def rewrite(self, prompt: str) -> RewriteResult:
        """兼容旧入口：仍支持一次性 JSON（不推荐长文）。"""
        content = self.complete(
            prompt,
            system=(
                "你是资深公众号主编。正文必须足够长（约2500字）。"
                "若要求 JSON，请保证 body 完整。只输出要求格式。"
            ),
            max_tokens=8192,
        )
        result = parse_rewrite_output(content)
        result.provider = self.provider_name
        return result

    def rewrite_longform(
        self,
        topic: str,
        raw_content: str,
        *,
        instruction: str = "",
        title_instruction: str = "",
        min_chars: int = 1800,
        target_chars: int = 2500,
    ) -> RewriteResult:
        """两阶段生成：先写够长的纯正文，再单独生成标题。避免 JSON 截断导致字数不够。"""
        body = self._generate_body(
            topic,
            raw_content,
            instruction=instruction,
            target_chars=target_chars,
        )
        # 多轮扩写，直到达标或达到轮次上限
        for round_i in range(1, 4):
            n = count_content_chars(body)
            if n >= min_chars:
                break
            logger.info(
                "%s body still short (%s < %s), expand round %s",
                self.provider_name,
                n,
                min_chars,
                round_i,
            )
            body = self._expand_body_plain(
                topic,
                body,
                instruction=instruction,
                target_chars=target_chars,
            )

        titles, subtitles, digest = self._generate_titles_bundle(
            topic,
            body,
            instruction=instruction,
            title_instruction=title_instruction,
        )
        return RewriteResult(
            body=body.strip(),
            titles=titles,
            subtitles=subtitles,
            digest=digest,
            provider=self.provider_name,
            raw=body[:500],
        )

    def optimize_titles(self, prompt: str) -> TitleResult:
        content = self.complete(
            prompt,
            system="你是公众号爆款标题专家，只输出结构化 JSON。",
            max_tokens=1024,
        )
        result = parse_title_output(content)
        result.provider = self.provider_name
        if len(result.titles) < 1:
            raise RuntimeError(f"{self.provider_name} returned no titles")
        return result

    def expand_rewrite(
        self,
        topic: str,
        draft_body: str,
        *,
        instruction: str = "",
        title_instruction: str = "",
        target_chars: int = 2500,
    ) -> RewriteResult:
        body = self._expand_body_plain(
            topic,
            draft_body,
            instruction=instruction,
            target_chars=target_chars,
        )
        titles, subtitles, digest = self._generate_titles_bundle(
            topic,
            body,
            instruction=instruction,
            title_instruction=title_instruction,
        )
        return RewriteResult(
            body=body,
            titles=titles,
            subtitles=subtitles,
            digest=digest,
            provider=self.provider_name,
        )

    def _generate_body(
        self,
        topic: str,
        raw_content: str,
        *,
        instruction: str = "",
        target_chars: int,
    ) -> str:
        configured = _content_instruction_block(instruction)
        prompt = (
            f"{configured}\n\n"
            f"【当前阶段输出协议（优先级最高）】\n"
            f"本阶段只生成文章正文。运营配置中涉及 JSON、body/titles/subtitles 字段、"
            f"标题数组、副标题数组、代码围栏或其它结构化输出的要求，本阶段暂不执行，"
            f"将在后续标题阶段处理。不要因为这些格式要求省略正文。\n\n"
            f"请根据话题重写一篇完整微信公众号文章。\n"
            f"硬性要求：\n"
            f"1. 只输出正文，不要 JSON，不要标题列表，不要前言后记。\n"
            f"2. 正文字数必须达到约 {target_chars} 字（按去掉空格后的汉字/标点计），"
            f"至少写到 {max(target_chars - 300, 2000)} 字；写不够算失败。\n"
            f"3. 要有新观点，禁止照搬原文。\n"
            f"4. 结构：自然开场 → 背景与矛盾 → 3到5个观点 → 总结启发。每个观点使用表达实际结论的小标题，标题下至少用2个自然段说明原因、案例、数据或对比；正文中不得出现‘开头钩子、背景冲突、分论点’等写作提示词。\n"
            f"5. 短段落，段落之间空一行；小标题可用「## 标题」。\n\n"
            f"6. {EMPHASIS_PROMPT}\n\n"
            f"【话题】\n{topic}\n\n"
            f"【参考原文（禁止照搬）】\n{raw_content[:10000]}\n"
        )
        text = self.complete(
            prompt,
            system=(
                "你是资深公众号主编。你必须写够指定字数的长文。"
                "只输出正文纯文本，不要解释，不要 JSON。"
            ),
            max_tokens=8192,
            temperature=0.8,
        )
        return self._clean_body(text)

    def _expand_body_plain(
        self,
        topic: str,
        draft_body: str,
        *,
        instruction: str = "",
        target_chars: int,
    ) -> str:
        cur = count_content_chars(draft_body)
        need = max(target_chars - cur, 600)
        configured = _content_instruction_block(instruction)
        prompt = (
            f"{configured}\n\n"
            f"【当前阶段输出协议（优先级最高）】\n"
            f"本阶段只输出扩写后的完整正文纯文本。运营配置中有关 JSON、标题和副标题"
            f"数组的输出格式要求本阶段暂不执行。\n\n"
            f"下面公众号正文当前约 {cur} 字，目标约 {target_chars} 字。"
            f"请在原文基础上扩写，至少再增加约 {need} 字，使全文达到目标字数。\n"
            f"要求：补充场景细节、对比、反例、方法论与行动建议；不要注水；"
            f"保留原有观点并深化；只输出扩写后的【完整正文】纯文本，不要 JSON，不要标题列表。\n"
            f"{EMPHASIS_PROMPT}\n"
            f"话题：{topic}\n\n"
            f"【当前正文】\n{draft_body[:9000]}\n"
        )
        text = self.complete(
            prompt,
            system="你是资深公众号主编。只输出完整正文纯文本，必须明显加长。",
            max_tokens=8192,
            temperature=0.65,
        )
        expanded = self._clean_body(text)
        # 若模型只返回续写片段，拼到原稿后面
        if count_content_chars(expanded) < count_content_chars(draft_body) * 0.8:
            return (draft_body.rstrip() + "\n\n" + expanded).strip()
        if count_content_chars(expanded) <= cur + 100:
            return (draft_body.rstrip() + "\n\n" + expanded).strip()
        return expanded

    def _generate_titles_bundle(
        self,
        topic: str,
        body: str,
        *,
        instruction: str = "",
        title_instruction: str = "",
    ) -> tuple[list[str], list[str], str]:
        """同一次调用生成标题、副标题和基于全文的摘要。"""
        titles: list[str] = []
        subtitles: list[str] = []
        digest = ""
        bundle_example = json.dumps(
            {
                "titles": [
                    f"主标题候选{i}" for i in range(1, TITLE_CANDIDATE_COUNT + 1)
                ],
                "subtitles": [
                    f"副标题候选{i}"
                    for i in range(1, SUBTITLE_CANDIDATE_COUNT + 1)
                ],
                "digest": "阅读全文后形成的120字以内摘要",
            },
            ensure_ascii=False,
        )

        prompt = (
            f"{_title_instruction_block(instruction, title_instruction)}\n\n"
            f"【当前阶段输出协议（优先级最高）】\n"
            f"本阶段只生成主标题和副标题，并同时生成摘要，不重新生成正文。必须输出下述 JSON。\n\n"
            f"根据话题与正文，生成 {TITLE_CANDIDATE_COUNT} 个公众号主标题和 "
            f"{SUBTITLE_CANDIDATE_COUNT} 个副标题。\n"
            f"主标题：约 16–28 字，有点击欲，不要编号，彼此角度不同。\n"
            f"副标题：一句有信息量的说明，约 12–24 字；"
            f"严禁输出「关于××的关键补充」「补充1」等占位空话。\n"
            f"摘要 digest：阅读全文后独立概括核心事实、主要观点和结论，不得照抄"
            f"标题或第一段，含标点最多120字。\n"
            f"只输出 JSON（不要其它文字），titles 与 subtitles 数组都必须恰好包含 "
            f"{TITLE_CANDIDATE_COUNT} 项。格式示例：{bundle_example}\n\n"
            f"【话题】{topic}\n\n【完整正文】\n{body[:12000]}\n"
        )
        try:
            content = self.complete(
                prompt,
                system=(
                    "只输出合法 JSON。titles、subtitles 与 digest 必须是真实可用文案，"
                    f"titles 与 subtitles 各自必须恰好包含 {TITLE_CANDIDATE_COUNT} 项；"
                    "digest 必须基于全文且不超过120字；禁止任何占位符、模板句。"
                ),
                max_tokens=2600,
                temperature=0.85,
            )
            parsed = parse_rewrite_output(content)
            digest = parsed.digest
            titles = [
                clean_candidate_text(t)
                for t in parsed.titles
                if not is_junk_title_or_subtitle(t)
            ]
            subtitles = [
                clean_candidate_text(s)
                for s in parsed.subtitles
                if not is_junk_title_or_subtitle(s)
            ]
            if not titles:
                tr = parse_title_output(content)
                titles = [
                    clean_candidate_text(t)
                    for t in tr.titles
                    if not is_junk_title_or_subtitle(t)
                ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("title bundle failed: %s", exc)

        for _attempt in range(3):
            titles = _dedupe_keep_order(titles)
            if len(titles) >= TITLE_CANDIDATE_COUNT:
                break
            try:
                more = self._generate_titles_only(
                    topic,
                    body,
                    instruction=instruction,
                    title_instruction=title_instruction,
                    need=TITLE_CANDIDATE_COUNT - len(titles),
                    exclude=titles,
                )
                titles = _dedupe_keep_order(titles + more)
            except Exception as exc:  # noqa: BLE001
                logger.warning("title-only supplement failed: %s", exc)
                break

        for _attempt in range(3):
            subtitles = _dedupe_keep_order(subtitles)
            if len(subtitles) >= SUBTITLE_CANDIDATE_COUNT:
                break
            try:
                more = self._generate_subtitles_only(
                    topic,
                    body,
                    instruction=instruction,
                    title_instruction=title_instruction,
                    need=SUBTITLE_CANDIDATE_COUNT - len(subtitles),
                    exclude=subtitles,
                )
                subtitles = _dedupe_keep_order(subtitles + more)
            except Exception as exc:  # noqa: BLE001
                logger.warning("subtitle-only supplement failed: %s", exc)
                break

        titles = _dedupe_keep_order(titles)
        subtitles = _dedupe_keep_order(
            [s for s in subtitles if not is_junk_title_or_subtitle(s)]
        )

        # 不够时：只用正文真实小标题/短段，绝不拼模板句
        if len(titles) < 1:
            titles = _real_lines_from_body(
                body,
                need=TITLE_CANDIDATE_COUNT,
                min_len=8,
                max_len=32,
            )
        if len(titles) < 1 and (topic or "").strip():
            titles = [(topic or "").strip()[:28]]

        if len(subtitles) < 1:
            # 副标题可为空；有正文摘录才补，没有就空着让用户不选
            subtitles = _real_lines_from_body(
                body,
                need=SUBTITLE_CANDIDATE_COUNT,
                min_len=12,
                max_len=28,
            )
            # 避免与标题完全相同
            title_set = set(titles)
            subtitles = [s for s in subtitles if s not in title_set]

        return (
            titles[:TITLE_CANDIDATE_COUNT],
            subtitles[:SUBTITLE_CANDIDATE_COUNT],
            digest,
        )

    def _generate_titles_only(
        self,
        topic: str,
        body: str,
        *,
        instruction: str = "",
        title_instruction: str = "",
        need: int = TITLE_CANDIDATE_COUNT,
        exclude: list[str] | None = None,
    ) -> list[str]:
        excluded = "\n".join(f"- {item}" for item in (exclude or []))
        example = json.dumps(
            {"titles": [f"标题{i}" for i in range(1, need + 1)]},
            ensure_ascii=False,
        )
        prompt = (
            f"{_title_instruction_block(instruction, title_instruction)}\n\n"
            f"【当前阶段输出协议（优先级最高）】\n"
            f"本阶段只补充主标题并输出 JSON，不要生成正文。\n\n"
            f"为这篇公众号文章再写恰好 {need} 个互不重复的主标题。"
            f"每条约 16–28 字，角度不同，具体、有点击欲。\n"
            f"不得重复以下已有标题：\n{excluded or '无'}\n"
            f"只输出 JSON：{example}\n\n"
            f"【话题】{topic}\n\n【正文节选】\n{body[:3000]}\n"
        )
        content = self.complete(
            prompt,
            system="只输出合法 JSON。每条主标题必须是真实文案，禁止字段名或占位符。",
            max_tokens=1400,
            temperature=0.85,
        )
        parsed = parse_title_output(content)
        return _dedupe_keep_order(
            [
                clean_candidate_text(title)
                for title in parsed.titles
                if not is_junk_title_or_subtitle(title)
            ]
        )[:need]

    def _generate_subtitles_only(
        self,
        topic: str,
        body: str,
        *,
        instruction: str = "",
        title_instruction: str = "",
        need: int = SUBTITLE_CANDIDATE_COUNT,
        exclude: list[str] | None = None,
    ) -> list[str]:
        excluded = "\n".join(f"- {item}" for item in (exclude or []))
        example = json.dumps(
            {"subtitles": [f"副标题{i}" for i in range(1, need + 1)]},
            ensure_ascii=False,
        )
        prompt = (
            f"{_title_instruction_block(instruction, title_instruction)}\n\n"
            f"【当前阶段输出协议（优先级最高）】\n"
            f"本阶段只补充副标题并输出下述 JSON，不要生成正文。\n\n"
            f"为这篇公众号文章写 {need} 个副标题，用于标题下方的一句话说明。\n"
            f"要求：紧扣话题「{topic}」与正文；每条 12–24 字；具体、有信息量。\n"
            f"严禁：「关于××的关键补充」、带序号的空模板、无意义占位。\n"
            f"不得重复以下已有副标题：\n{excluded or '无'}\n"
            f"只输出 JSON：{example}\n\n"
            f"【正文节选】\n{body[:3000]}\n"
        )
        content = self.complete(
            prompt,
            system="只输出 JSON。每条副标题必须是真实文案，禁止占位符。",
            max_tokens=1400,
            temperature=0.8,
        )
        parsed = parse_rewrite_output(content)
        return _dedupe_keep_order(
            [
                clean_candidate_text(s)
                for s in parsed.subtitles
                if not is_junk_title_or_subtitle(s)
            ]
        )[:need]

    @staticmethod
    def _clean_body(text: str) -> str:
        text = (text or "").strip()
        fence = re.search(r"```(?:markdown|md|text)?\s*([\s\S]*?)```", text, flags=re.I)
        if fence:
            text = fence.group(1).strip()
        # 去掉误带的 JSON 外壳
        if text.startswith("{") and '"body"' in text[:80]:
            parsed = parse_rewrite_output(text)
            if parsed.body:
                return parsed.body.strip()
        # 去掉可能的标题清单尾巴
        text = re.sub(r"(?is)\n\s*(标题|titles?)\s*[:：][\s\S]*$", "", text).strip()
        return text

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.85,
        max_attempts: int = 6,
    ) -> str:
        if not self.api_key:
            raise RuntimeError(f"{self.provider_name.upper()}_API_KEY is empty")
        base = self.api_base.rstrip("/")
        if base.endswith("/v1") or base.endswith("/v4"):
            url = f"{base}/chat/completions"
        else:
            url = f"{base}/v1/chat/completions"
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_err: Exception | None = None
        with httpx.Client(timeout=self.timeout) as client:
            for attempt in range(1, max_attempts + 1):
                try:
                    resp = client.post(url, headers=headers, json=payload)
                except httpx.TimeoutException as exc:
                    last_err = RuntimeError(f"{self.provider_name} timeout: {exc}")
                    if attempt >= max_attempts:
                        raise last_err from exc
                    wait = min(float(2**attempt * 2), 30.0)
                    logger.warning(
                        "%s timeout attempt %s/%s, sleep %.0fs",
                        self.provider_name,
                        attempt,
                        max_attempts,
                        wait,
                    )
                    time.sleep(wait)
                    continue

                if resp.status_code in (429, 500, 502, 503, 504):
                    wait = _retry_after_seconds(resp, attempt)
                    last_err = RuntimeError(
                        f"{self.provider_name} error: {resp.status_code} {resp.text[:400]}"
                    )
                    if attempt >= max_attempts:
                        raise last_err
                    logger.warning(
                        "%s %s attempt %s/%s, sleep %.0fs — %s",
                        self.provider_name,
                        resp.status_code,
                        attempt,
                        max_attempts,
                        wait,
                        (resp.text or "")[:120],
                    )
                    time.sleep(wait)
                    continue

                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"{self.provider_name} error: {resp.status_code} {resp.text[:400]}"
                    )

                data = resp.json()
                try:
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as exc:
                    raise RuntimeError(
                        f"Unexpected {self.provider_name} response: {data}"
                    ) from exc
                if not content or not str(content).strip():
                    raise RuntimeError(f"{self.provider_name} returned empty content")
                return str(content)

        raise last_err or RuntimeError(f"{self.provider_name} request failed")


def is_junk_title_or_subtitle(text: str) -> bool:
    """识别无意义占位符/模板句，禁止进入选项与正文。"""
    t = (text or "").strip()
    if not t:
        return True
    if re.match(
        r"""(?ix)^[\"'“”‘’]?\s*(?:titles?|subtitles?|title_list|subtitle_list)
        \s*[\"'“”‘’]?\s*[:：]\s*\[?\s*$""",
        t,
    ):
        return True
    if t in {"[", "]", "{", "}", "],", "},"}:
        return True
    if "关键补充" in t:
        return True
    if "这三点被大多数人忽略了" in t:
        return True
    if re.match(r"^关于[「『\"'].+[」』\"']", t) and ("补充" in t or "说明" in t):
        return True
    # 「某某 1」「副标题2」这类空编号
    if re.match(r"^(副标题|标题|subtitle|title)\s*\d+$", t, flags=re.I):
        return True
    if re.match(r"^[\.…]{2,}$", t):
        return True
    return False


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        s = (item or "").strip()
        if not s or s in seen or is_junk_title_or_subtitle(s):
            continue
        seen.add(s)
        out.append(s)
    return out


def _real_lines_from_body(
    body: str,
    *,
    need: int = 5,
    min_len: int = 8,
    max_len: int = 28,
) -> list[str]:
    """仅摘取正文里已有的小标题/短句，不生成模板文案。"""
    candidates: list[str] = []
    for h in re.findall(r"^##\s+(.+)$", body or "", flags=re.M):
        h = h.strip().strip("#").strip()
        if min_len <= len(h) <= max_len and not is_junk_title_or_subtitle(h):
            candidates.append(h)
    for para in re.split(r"\n\s*\n", body or ""):
        line = re.sub(r"\s+", "", (para or "").strip())
        if not line or line.startswith("#"):
            continue
        if is_junk_title_or_subtitle(line):
            continue
        if min_len <= len(line) <= max_len:
            candidates.append(line)
        elif len(line) > max_len:
            cut = line[:max_len].rstrip("，,。；;：:、")
            if len(cut) >= min_len and not is_junk_title_or_subtitle(cut):
                candidates.append(cut)
        if len(candidates) >= need * 3:
            break
    return _dedupe_keep_order(candidates)[:need]
