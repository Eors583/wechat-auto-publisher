from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Topic:
    topic: str
    source: str = "manual"
    meta: dict[str, Any] = field(default_factory=dict)


def from_manual(topic: str, meta: dict[str, Any] | None = None) -> Topic:
    return Topic(topic=topic.strip(), source="manual", meta=meta or {})


def from_keyword_file(path: str, index: int = 0) -> Topic:
    with open(path, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        raise ValueError(f"No keywords found in {path}")
    if index < 0 or index >= len(lines):
        raise IndexError(f"Keyword index {index} out of range (0..{len(lines)-1})")
    return Topic(topic=lines[index], source="keyword", meta={"file": path, "index": index})
