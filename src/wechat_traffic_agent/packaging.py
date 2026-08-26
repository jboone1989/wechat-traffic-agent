"""Create a copy-ready, local content package with no external side effects."""
from __future__ import annotations

import html
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .experiments import Experiment, ExperimentLedger
from .models import AudienceProfile, ContentPackage, TietuDraft
from .objectives import ObjectiveMetric, policy_for


class ContentPackager:
    def __init__(self, ledger: ExperimentLedger | None = None) -> None:
        self.ledger = ledger or ExperimentLedger()

    def create(
        self,
        draft: TietuDraft,
        audience: AudienceProfile,
        output_root: Path,
        *,
        source_refs: tuple[dict[str, Any], ...],
        strategy: dict[str, Any],
        created_at: datetime,
        runtime_run_id: str,
        today: date | None = None,
    ) -> ContentPackage:
        experiment_id, target = self.ledger.allocate(output_root, today or date.today())
        try:
            target.mkdir(parents=False, exist_ok=False)  # 槽位冲突直接失败，绝不覆盖
        except FileExistsError:
            raise RuntimeError(
                f"实验槽位 {experiment_id} 已被占用，拒绝覆盖"
            ) from None
        markdown = target / "tietu.md"
        json_file = target / "tietu.json"
        experiment_file = target / "experiment.json"
        preview = target / "preview.html"
        try:
            markdown.write_text(_markdown(draft, audience), encoding="utf-8")
            json_file.write_text(draft.model_dump_json(indent=2), encoding="utf-8")
            preview.write_text(_preview(draft), encoding="utf-8")
            # experiment.json 最后写：文件存在即代表槽位完整（完成标记）
            experiment = Experiment(
                experiment_id=experiment_id,
                created_at=created_at,
                audience=audience,
                objective=audience.objective,
                strategy=strategy,
                draft=draft,
                source_refs=source_refs,
                runtime_run_id=runtime_run_id,
            )
            experiment_file.write_text(experiment.model_dump_json(indent=2), encoding="utf-8")
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)  # 不留半写入槽位
            raise
        return ContentPackage(
            root=target,
            markdown=markdown,
            json_file=json_file,
            experiment_file=experiment_file,
            preview_html=preview,
        )


_METRIC_LABELS = {
    ObjectiveMetric.READS: "阅读数",
    ObjectiveMetric.FOLLOWS: "新增关注",
    ObjectiveMetric.DISCUSSIONS: "评论数",
    ObjectiveMetric.SHARES: "分享数",
    ObjectiveMetric.CONVERSIONS: "后端转化数",
}


def _markdown(draft: TietuDraft, audience: AudienceProfile) -> str:
    sources = "\n".join(f"- {url}" for url in draft.source_urls) or "- 无外部事实引用"
    policy = policy_for(audience.objective)
    primary = _METRIC_LABELS[policy.primary_metric]
    secondary = "、".join(_METRIC_LABELS[metric] for metric in policy.secondary_metrics)
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

## 回填指标（反馈学习用）

- 主指标：{primary}
- 次要指标：{secondary}
- 拿不到的指标在 history.json 里留 null，不要填 0

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
