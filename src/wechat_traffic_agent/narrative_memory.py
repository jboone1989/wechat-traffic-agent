"""Assemble the bounded 'story so far' narrative block from existing mind + post history."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

STORY_BUDGET_CHARS = 600
RECENT_POST_LINES = 12
LINE_BUDGET_CHARS = 60


def build_story_so_far(mind, recent_published, *, budget=STORY_BUDGET_CHARS) -> str:
    sections = []
    projection = None
    if mind is not None:
        try:
            projection = mind.public_projection()
        except Exception as exc:
            logger.warning("story mind projection unavailable: %r", exc)
    if projection:
        positions = projection.get("positions") or {}
        open_lines = [
            f"- 未结立场：{row.get('proposition')}（认错条件：{row.get('falsifier') or '未写明'}）"
            for row in (positions.get("open") or ())[:3] if row.get("proposition")]
        if open_lines:
            sections.append("未结的 call：\n" + "\n".join(open_lines))
        resolution_lines = [
            f"- {row.get('status')}：{row.get('proposition')} → {row.get('resolution') or ''}"
            for row in (positions.get("recent_resolutions") or ())[:3] if row.get("proposition")]
        if resolution_lines:
            sections.append("最近的兑现/打脸：\n" + "\n".join(resolution_lines))
        themes = [row.get("name") for row in (projection.get("interest_terrain") or ())[:3]
                  if row.get("name")]
        if themes:
            sections.append("当前关注主线：" + "、".join(themes))
    lines = []
    for post in list(recent_published or ())[-RECENT_POST_LINES:]:
        provenance = post.get("observation_provenance") or {}
        genre = provenance.get("genre") or ("research" if post.get("research_provenance") else "观察")
        thesis = str(post.get("thesis") or post.get("body") or "")[:LINE_BUDGET_CHARS]
        day = str(post.get("published_at") or "")[:10]
        lines.append(f"- {day} [{genre}] {thesis}")
    if lines:
        sections.append("最近的帖子：\n" + "\n".join(lines))
    return "\n\n".join(sections)[:budget]
