"""Deterministic near-copy guard: learning a pattern ≠ copying the text.

Three escalating checks between analyzer output and the source sample:
high title similarity (char-bigram Jaccard), reused contiguous body
fragments, and lifted sentence patterns from the sample title. All
threshold-based and deterministic so tests need no LLM.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

TITLE_SIMILARITY_THRESHOLD = 0.6
FRAGMENT_CHARS = 12  # 正文中连续复用 ≥12 字视为片段照搬
SENTENCE_PATTERN_CHARS = 10  # 与样本标题连续重合 ≥10 字视为句式照搬


@dataclass(frozen=True)
class NearCopyVerdict:
    is_near_copy: bool
    reasons: tuple[str, ...]


def _squash(text: str) -> str:
    return "".join(text.split()).casefold()


def _bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[i : i + 2] for i in range(len(value) - 1)}


def _similarity(a: str, b: str) -> float:
    grams_a, grams_b = _bigrams(a), _bigrams(b)
    union = len(grams_a | grams_b)
    if union == 0:
        return 0.0
    return len(grams_a & grams_b) / union


def _longest_common(a: str, b: str) -> int:
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    best = 0
    for char_a in a:
        current = [0] * (len(b) + 1)
        for index, char_b in enumerate(b, start=1):
            if char_a == char_b:
                current[index] = previous[index - 1] + 1
                best = max(best, current[index])
        previous = current
    return best


def near_copy_verdict(
    *,
    produced_texts: Sequence[str],
    source_titles: Sequence[str],
    source_bodies: Sequence[str],
) -> NearCopyVerdict:
    """Flag produced copy that leans on the sample instead of learning from it."""
    reasons: list[str] = []
    for produced in produced_texts:
        squashed = _squash(produced)
        if not squashed:
            continue
        for title in source_titles:
            target = _squash(title)
            if not target:
                continue
            similarity = _similarity(squashed, target)
            if similarity >= TITLE_SIMILARITY_THRESHOLD:
                reasons.append(
                    f"标题与样本标题高度相似（字符对相似度 {similarity:.2f}）"
                )
            elif _longest_common(squashed, target) >= SENTENCE_PATTERN_CHARS:
                reasons.append("文案照搬了样本标题的连续句式")
        for body in source_bodies:
            target = _squash(body)
            if not target:
                continue
            if _longest_common(squashed, target) >= FRAGMENT_CHARS:
                reasons.append("文案复用了样本正文的连续长片段")
    unique = tuple(dict.fromkeys(reasons))
    return NearCopyVerdict(is_near_copy=bool(unique), reasons=unique)
