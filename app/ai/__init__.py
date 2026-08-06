from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

TITLE_CANDIDATE_COUNT = 10
SUBTITLE_CANDIDATE_COUNT = 10

EMPHASIS_PROMPT = (
    "【重点加粗规则】只加粗核心观点、关键数据和行动建议，使用 Markdown "
    "**文字** 标记；每次只加粗一句中的关键短语，禁止整段加粗；"
    "禁止连续多个段落都出现加粗。不要加粗普通叙述、过渡句或小标题。"
)


@dataclass
class RewriteResult:
    body: str
    titles: list[str] = field(default_factory=list)
    subtitles: list[str] = field(default_factory=list)
    provider: str = ""
    raw: str = ""


@dataclass
class TitleResult:
    titles: list[str] = field(default_factory=list)
    provider: str = ""
    raw: str = ""


def build_rewrite_user_prompt(topic: str, raw_content: str, instruction: str) -> str:
    return (
        f"{instruction}\n\n{EMPHASIS_PROMPT}\n\n"
        "【标题候选硬性协议】最终结构化结果中的 titles 必须包含恰好 "
        f"{TITLE_CANDIDATE_COUNT} 个互不重复的主标题，subtitles 必须包含恰好 "
        f"{SUBTITLE_CANDIDATE_COUNT} 个互不重复的副标题。不得把 JSON 字段名、"
        "数组括号、引号或逗号当成标题内容。\n\n"
        f"【话题】\n{topic}\n\n"
        f"【原始内容（仅作参考，禁止照搬）】\n{raw_content[:12000]}\n"
    )


def build_title_user_prompt(body: str, instruction: str) -> str:
    return (
        f"{instruction}\n\n"
        f"【硬性数量】必须返回恰好 {TITLE_CANDIDATE_COUNT} 个互不重复的主标题。"
        "不得把 JSON 字段名、数组括号、引号或逗号当成标题内容。\n\n"
        f"【文章正文】\n{body[:8000]}\n"
    )


def parse_rewrite_output(text: str) -> RewriteResult:
    data = _extract_json_object(text)
    if data:
        body = normalize_model_body(
            str(data.get("body") or data.get("content") or data.get("article") or "")
        )
        titles = _as_str_list(data.get("titles") or data.get("title_list") or [])
        subtitles = _as_str_list(data.get("subtitles") or data.get("subtitle_list") or [])
        # 允许仅返回 titles/subtitles（无 body）的 JSON，避免被当成解析失败
        if body or titles or subtitles:
            return RewriteResult(
                body=body,
                titles=titles[:TITLE_CANDIDATE_COUNT],
                subtitles=subtitles[:SUBTITLE_CANDIDATE_COUNT],
                raw=text,
            )

    body = normalize_model_body(text)
    titles = _extract_loose_array(text, ("titles", "title_list"))
    subtitles = _extract_loose_array(text, ("subtitles", "subtitle_list"))
    if not titles:
        titles = _extract_numbered_section(text, ("标题", "title"))
    if not subtitles:
        subtitles = _extract_numbered_section(text, ("副标题", "subtitle"))
    # Strip title sections from body if present
    body = re.sub(r"(?is)(标题|titles?)\s*[:：]?[\s\S]*$", "", body).strip() or normalize_model_body(text)
    return RewriteResult(
        body=body,
        titles=titles[:TITLE_CANDIDATE_COUNT],
        subtitles=subtitles[:SUBTITLE_CANDIDATE_COUNT],
        raw=text,
    )


def normalize_model_body(text: str) -> str:
    """Restore line breaks when a provider double-escapes structured text."""
    value = (text or "").strip()
    literal_breaks = value.count(r"\n") + value.count(r"\r\n")
    actual_breaks = value.count("\n")
    if literal_breaks and (
        actual_breaks == 0
        or literal_breaks >= max(2, actual_breaks * 2)
    ):
        value = value.replace(r"\r\n", "\n").replace(r"\n", "\n")
    return value.strip()


def enforce_emphasis_rules(text: str) -> str:
    """Keep short emphasis spans, but remove whole-paragraph and consecutive emphasis."""
    parts = re.split(r"(\n\s*\n)", text or "")
    previous_paragraph_emphasized = False
    marker = re.compile(r"\*\*([^*\n]+)\*\*")
    for index in range(0, len(parts), 2):
        paragraph = parts[index]
        matches = list(marker.finditer(paragraph))
        if not matches:
            previous_paragraph_emphasized = False
            continue
        plain = marker.sub(lambda match: match.group(1), paragraph)
        plain_length = len(re.sub(r"\s+", "", re.sub(r"^#{1,6}\s+", "", plain)))

        def clean(match: re.Match[str]) -> str:
            content = match.group(1).strip()
            emphasized_length = len(re.sub(r"\s+", "", content))
            if emphasized_length > 40:
                return content
            if plain_length and emphasized_length / plain_length >= 0.65:
                return content
            return f"**{content}**"

        cleaned = marker.sub(clean, paragraph)
        has_valid_emphasis = bool(marker.search(cleaned))
        if previous_paragraph_emphasized and has_valid_emphasis:
            cleaned = marker.sub(lambda match: match.group(1).strip(), cleaned)
            has_valid_emphasis = False
        parts[index] = cleaned
        previous_paragraph_emphasized = has_valid_emphasis
    return "".join(parts).strip()


def parse_title_output(text: str) -> TitleResult:
    data = _extract_json_object(text)
    if data:
        titles = _as_str_list(data.get("titles") or data.get("title_list") or [])
        if titles:
            return TitleResult(titles=titles[:TITLE_CANDIDATE_COUNT], raw=text)
    titles = _extract_loose_array(text, ("titles", "title_list"))
    if not titles:
        titles = _extract_numbered_section(text, ("标题", "title"))
    if not titles:
        titles = _as_str_list(
            [
                ln.strip(" -•\t")
                for ln in text.splitlines()
                if ln.strip() and len(ln.strip()) >= 6
            ]
        )
    return TitleResult(titles=titles[:TITLE_CANDIDATE_COUNT], raw=text)


def quality_check(
    result: RewriteResult,
    raw_content: str,
    *,
    min_body_chars: int = 400,
    max_similarity: float = 0.72,
    required_titles: int = 1,
    required_subtitles: int = 0,
) -> None:
    body = (result.body or "").strip()
    # 按去空白后的字符数统计（更接近中文“字数”）
    body_len = len(re.sub(r"\s+", "", body))
    if body_len < min_body_chars:
        raise ValueError(f"正文不足 {min_body_chars} 字：当前只有 {body_len} 字")
    if not result.titles:
        raise ValueError("Rewrite missing titles")
    if len(result.titles) < required_titles:
        raise ValueError(
            f"主标题候选不足 {required_titles} 个：当前只有 {len(result.titles)} 个"
        )
    if len(result.subtitles) < required_subtitles:
        raise ValueError(
            f"副标题候选不足 {required_subtitles} 个：当前只有 {len(result.subtitles)} 个"
        )
    sim = _jaccard_similarity(body, raw_content)
    if sim > max_similarity:
        raise ValueError(f"Rewrite too similar to source: similarity={sim:.2f}")


def _as_str_list(value: Any) -> list[str]:
    return clean_candidate_list(value)


def clean_candidate_list(value: Any, *, limit: int | None = None) -> list[str]:
    """Normalize, deduplicate and optionally cap model candidate strings."""

    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        clean = clean_candidate_text(str(item))
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        output.append(clean)
        if limit is not None and len(output) >= limit:
            break
    return output


def clean_candidate_text(value: str) -> str:
    """Remove JSON/list syntax accidentally returned as candidate content."""

    text = str(value or "").strip().strip("`").strip()
    text = text.replace(r"\"", '"').replace(r"\'", "'").strip()
    text = re.sub(r"^\s*(?:\d+[\.\)、]|[-•])\s*", "", text).strip()
    if re.match(
        r"""(?ix)^[\"'“”‘’]?\s*(?:titles?|subtitles?|title_list|subtitle_list)
        \s*[\"'“”‘’]?\s*[:：]\s*\[?\s*$""",
        text,
    ):
        return ""
    if text in {"[", "]", "{", "}", "],", "},"}:
        return ""
    text = text.rstrip().rstrip(",，").rstrip()
    quote_pairs = (
        ('"', '"'),
        ("'", "'"),
        ("“", "”"),
        ("‘", "’"),
        ("「", "」"),
        ("『", "』"),
    )
    for left, right in quote_pairs:
        if text.startswith(left) and text.endswith(right) and len(text) > 2:
            text = text[len(left) : -len(right)].strip()
            break
    return text.rstrip(",，").strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.I)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        logger.debug("Failed to parse JSON from model output")
        return None


def _extract_loose_array(text: str, keys: tuple[str, ...]) -> list[str]:
    """Recover a candidate array from truncated or non-strict model JSON."""

    key_pattern = "|".join(re.escape(key) for key in keys)
    start = re.search(
        rf"""(?ix)[\"'“”‘’]?\s*(?:{key_pattern})\s*[\"'“”‘’]?
        \s*[:：]\s*\[""",
        text or "",
    )
    if not start:
        return []
    tail = (text or "")[start.end() :]
    end_positions = [position for position in (tail.find("]"),) if position >= 0]
    next_field = re.search(
        r"""(?im)^\s*[\"'“”‘’]?\s*
        (?:body|content|article|titles?|subtitles?|title_list|subtitle_list)
        \s*[\"'“”‘’]?\s*[:：]""",
        tail,
        flags=re.X,
    )
    if next_field:
        end_positions.append(next_field.start())
    block = tail[: min(end_positions)] if end_positions else tail

    quoted_pattern = re.compile(
        r'"((?:\\.|[^"\\])*)"'
        r"|“([^”\r\n]+)”"
        r"|‘([^’\r\n]+)’"
        r"|「([^」\r\n]+)」"
        r"|『([^』\r\n]+)』"
        r"|'((?:\\.|[^'\\])*)'"
    )
    quoted_values = [
        next(group for group in match.groups() if group is not None)
        for match in quoted_pattern.finditer(block)
    ]
    if quoted_values:
        return _as_str_list(quoted_values)

    # Some models omit string quotes but still put one candidate on each line.
    return _as_str_list(
        [
            line
            for line in block.splitlines()
            if clean_candidate_text(line)
        ]
    )


def _extract_numbered_section(text: str, keywords: tuple[str, ...]) -> list[str]:
    pattern = "|".join(re.escape(k) for k in keywords)
    section = re.search(
        rf"(?is)(?:{pattern})\s*[:：]?\s*(?:\n|\r\n)([\s\S]{{0,1200}}?)(?:\n\s*\n|(?:副标题|subtitles?)|$)",
        text,
    )
    block = section.group(1) if section else text
    items = re.findall(
        r"(?:^|\n)\s*(?:\d+[\.\)、]|[-•])\s*(.+)",
        block,
    )
    return _as_str_list(items)


def _jaccard_similarity(a: str, b: str) -> float:
    ta = set(_tokenize(a))
    tb = set(_tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _tokenize(text: str) -> list[str]:
    # Character bigrams work reasonably for Chinese without extra deps.
    cleaned = re.sub(r"\s+", "", text.lower())
    if len(cleaned) < 2:
        return list(cleaned)
    return [cleaned[i : i + 2] for i in range(len(cleaned) - 1)]
