"""Create a copy-ready, local content package with no external side effects."""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from .models import AudienceProfile, ContentPackage, TietuDraft


class ContentPackager:
    def create(
        self,
        draft: TietuDraft,
        audience: AudienceProfile,
        output_root: Path,
        *,
        today: date | None = None,
    ) -> ContentPackage:
        target = output_root / (today or date.today()).isoformat()
        target.mkdir(parents=True, exist_ok=True)
        markdown = target / "tietu.md"
        json_file = target / "tietu.json"
        preview = target / "preview.html"
        markdown.write_text(_markdown(draft, audience), encoding="utf-8")
        json_file.write_text(
            draft.model_dump_json(indent=2), encoding="utf-8"
        )
        preview.write_text(_preview(draft), encoding="utf-8")
        return ContentPackage(
            root=target,
            markdown=markdown,
            json_file=json_file,
            preview_html=preview,
        )


def _markdown(draft: TietuDraft, audience: AudienceProfile) -> str:
    sources = "\n".join(f"- {url}" for url in draft.source_urls) or "- 无外部事实引用"
    return f"""# 题图内容包

## 复制到公众号

标题：{draft.title}

文字海报：

{draft.poster_text}

描述/互动句：

{draft.interaction_hook}

## 本轮实验记录

- 人群：{audience.people}
- 场景：{audience.scene}
- 持续需求：{audience.recurring_need}
- 目标：{audience.objective.value}
- 选题：{draft.topic}
- 痛点：{draft.pain_point}
- 观点：{draft.viewpoint}
- 情绪角度：{draft.angle.value}
- 选择理由：{draft.rationale}

## 样本来源

{sources}

## 手动发布检查

- [ ] 标题不超过 20 字
- [ ] 文字海报只有一个痛点和一个观点
- [ ] 没有照搬样本文案
- [ ] 没有恐慌、孤独收割或强诱导互动
- [ ] 在公众号题图编辑器中选择“文字海报”生成题图
- [ ] 预览无误后再保存或发表
"""


def _preview(draft: TietuDraft) -> str:
    lines = "".join(f"<p>{html.escape(line)}</p>" for line in draft.lines)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{html.escape(draft.title)}</title>
<style>body{{font-family:system-ui;background:#f4f1ec;color:#25221f}}main{{max-width:520px;margin:48px auto;padding:44px 34px;background:#fff;border-radius:18px;box-shadow:0 12px 40px #0001}}h1{{font-size:26px}}p{{font-size:20px;line-height:1.8;margin:10px 0}}footer{{margin-top:28px;color:#766;font-size:16px}}</style></head>
<body><main><h1>{html.escape(draft.title)}</h1>{lines}<footer>{html.escape(draft.interaction_hook)}</footer></main></body></html>"""
