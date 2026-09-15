"""Experiment Planner: one learned pattern -> one single-variable experiment.

New main chain: ViralRadar -> ViralCandidate -> ViralPatternAnalyzer ->
ExperimentPlanner -> TietuDraft -> immutable Experiment -> manual publish.
Each experiment varies exactly ONE declared variable (title pattern /
opening hook / emotion / information delta / interaction hook); everything
else stays anchored so a metric move can be attributed to that variable.
The backend only fills copy under constraints decided here — it never
chooses the tested variable, never invents sources, never selects its own
learning angle (except when emotion itself is the variable under test).
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .fixture import FixtureProvider
from .learning import TrafficLearner
from .models import AudienceProfile, ContentObjective, PerformanceObservation, TietuDraft
from .near_copy import near_copy_verdict
from .objectives import policy_for
from .packaging import ContentPackager
from .service import _extract_json
from .strategy_learning import StrategyDecision, StrategyLearner
from .viral_pattern import ViralPattern, ViralPatternAnalyzer
from .viral_radar import ScanReport, ViralRadar

if TYPE_CHECKING:
    from .models import ContentPackage


class ExperimentVariable(str, Enum):
    """The five first-batch surfaces worth testing, one per article."""

    TITLE_PATTERN = "title_pattern"
    OPENING_HOOK = "opening_hook"
    EMOTION = "emotion"
    INFORMATION_DELTA = "information_delta"
    INTERACTION_HOOK = "interaction_hook"


VARIABLES: tuple[ExperimentVariable, ...] = (
    ExperimentVariable.TITLE_PATTERN,
    ExperimentVariable.OPENING_HOOK,
    ExperimentVariable.EMOTION,
    ExperimentVariable.INFORMATION_DELTA,
    ExperimentVariable.INTERACTION_HOOK,
)

VARIABLE_SURFACES: dict[str, str] = {
    ExperimentVariable.TITLE_PATTERN.value: "标题（title 字段）：沿用拆解出的标题模式，但必须全新表达",
    ExperimentVariable.OPENING_HOOK.value: "海报第一句（lines 的第一句）：处理值放在钩子上",
    ExperimentVariable.EMOTION.value: "情绪角度（angle 字段本身即 treatment，可与推荐角度不同）",
    ExperimentVariable.INFORMATION_DELTA.value: "信息增量（viewpoint 与正文主体承载新事实或反常识点）",
    ExperimentVariable.INTERACTION_HOOK.value: "互动句（interaction_hook 字段）",
}

_BASELINE_FIELDS = {
    ExperimentVariable.TITLE_PATTERN: "title_pattern",
    ExperimentVariable.OPENING_HOOK: "opening_hook",
    ExperimentVariable.EMOTION: "emotional_trigger",
    ExperimentVariable.INFORMATION_DELTA: "information_delta",
    ExperimentVariable.INTERACTION_HOOK: "interaction_trigger",
}


def _treatment_value(draft: TietuDraft, variable: ExperimentVariable) -> str:
    if variable is ExperimentVariable.TITLE_PATTERN:
        return draft.title
    if variable is ExperimentVariable.OPENING_HOOK:
        return draft.lines[0]
    if variable is ExperimentVariable.EMOTION:
        return draft.angle.value
    if variable is ExperimentVariable.INFORMATION_DELTA:
        return draft.viewpoint
    return draft.interaction_hook


class ExperimentStrategy(BaseModel):
    """What this article deliberately varied versus the learned baseline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern_id: str
    variable_under_test: ExperimentVariable
    baseline_value: str = Field(min_length=2)
    treatment_value: str = Field(min_length=1)

    @classmethod
    def build(
        cls, pattern: ViralPattern, draft: TietuDraft, *, variable: ExperimentVariable
    ) -> "ExperimentStrategy":
        return cls(
            pattern_id=f"pat:{pattern.candidate_id}",
            variable_under_test=variable,
            baseline_value=getattr(pattern, _BASELINE_FIELDS[variable]),
            treatment_value=_treatment_value(draft, variable),
        )


@dataclass(frozen=True)
class PlannedExperiment:
    draft: TietuDraft
    strategy: ExperimentStrategy
    selection_reason: str = "rotation"


class ExperimentPlanner:
    """Deterministic experiment scaffolding around the backend copywriting."""

    @staticmethod
    def select_variable(rotation_offset: int) -> ExperimentVariable:
        """Round-robin over the five batch variables; deterministic per slot."""
        return VARIABLES[rotation_offset % len(VARIABLES)]

    def __init__(
        self,
        *,
        learner: TrafficLearner | None = None,
        strategy_learner: StrategyLearner | None = None,
        audience_key: str | None = None,
        observation_window: str | None = None,
    ) -> None:
        self.learner = learner or TrafficLearner()
        self.strategy_learner = strategy_learner or StrategyLearner()
        self.audience_key = audience_key
        self.observation_window = observation_window

    def choose_variable(
        self,
        history: Sequence[PerformanceObservation],
        audience: AudienceProfile,
        *,
        rotation_offset: int,
    ) -> tuple[ExperimentVariable, str]:
        # 跨人群/跨窗口污染是禁区：audience_key 与 observation_window
        # 未同时明确就不做历史推断，宁可轮转。
        annotated = [item for item in history if item.variable_under_test]
        missing = [
            name
            for name, value in (
                ("audience_key", self.audience_key),
                ("observation_window", self.observation_window),
            )
            if value is None
        ]
        if not annotated or missing:
            if not annotated:
                return self.select_variable(rotation_offset), "rotation"
            return self.select_variable(rotation_offset), (
                f"rotation: no {'/'.join(missing)} for scoped feedback"
            )
        decision: StrategyDecision = self.strategy_learner.recommend(
            annotated,
            candidate_variables=VARIABLES,
            audience_key=self.audience_key,
            objective=audience.objective,
            observation_window=self.observation_window,
        )
        name = getattr(decision.variable, "value", decision.variable)
        matched = next(item for item in VARIABLES if item.value == name)
        return matched, f"feedback_history: {decision.reason}"

    async def plan(
        self,
        runtime,
        pattern: ViralPattern,
        audience: AudienceProfile,
        *,
        history: Sequence[PerformanceObservation] = (),
        variable: ExperimentVariable | None = None,
        rotation_offset: int = 0,
        sample_titles: Sequence[str] = (),
        observation_window: str | None = None,
    ) -> PlannedExperiment:
        history = list(history)
        if observation_window is not None:
            self.observation_window = observation_window
        if variable is not None:
            chosen_variable, selection_reason = variable, "explicit"
        else:
            chosen_variable, selection_reason = self.choose_variable(
                history, audience, rotation_offset=rotation_offset
            )
        angle = self.learner.recommend(
            history,
            audience.objective,
            audience_key=self.audience_key,
            observation_window=self.observation_window,
        )
        policy = policy_for(audience.objective)
        raw = await runtime.cognitive_probe(
            _plan_task(pattern, chosen_variable, angle, audience, policy), max_tokens=900
        )
        draft = self._parse_draft(raw, chosen_variable, angle)
        verdict = near_copy_verdict(
            produced_texts=(draft.title, *draft.lines, draft.interaction_hook),
            source_titles=tuple(sample_titles),
            source_bodies=(),
        )
        if verdict.is_near_copy:
            raise ValueError(f"near-copy detected: {'；'.join(verdict.reasons)}")
        return PlannedExperiment(
            draft=draft,
            strategy=ExperimentStrategy.build(pattern, draft, variable=chosen_variable),
            selection_reason=selection_reason,
        )

    @staticmethod
    def _parse_draft(raw: str, variable: ExperimentVariable, angle) -> TietuDraft:
        data = json.loads(_extract_json(raw))
        if not isinstance(data, dict):
            raise ValueError("backend did not return a JSON object")
        # 本链路的证据在实验记录里以 source_refs 存档；草稿层面禁止编造外链。
        urls = data.get("source_urls") or []
        if urls:
            raise ValueError(f"unsupported external sources {urls}: source_urls must be empty")
        draft = TietuDraft.model_validate(data)
        # 单变量纪律：非 emotion 变量的实验里，角度不得漂移出学习者推荐值。
        if variable is not ExperimentVariable.EMOTION and draft.angle is not angle:
            raise ValueError(
                f"backend drifted the learning angle: expected {angle.value}, "
                f"got {draft.angle.value}"
            )
        return draft


async def scan(
    platforms: Sequence[str] = ("weibo", "zhihu", "bilibili"),
    *,
    hot_provider=None,
    reader=None,
    top_n: int = 20,
) -> ScanReport:
    """Hot-list -> deep fetch -> ScanReport.

    Defaults are the OFFLINE fixture plane only. Live callers (--source
    live) must pass both hot_provider and reader explicitly — 单边 live
    输入造成的「真热榜 + fixture 深抓」静默混合是被禁止的。验证手段：
    报告里每个 PlatformScan.source 应为 "dailyhot" 而非 "fixture"。
    """
    hot = hot_provider or FixtureProvider()
    radar = ViralRadar(
        hot_provider=hot, reader=reader or FixtureProvider(), platforms=platforms, top_n=top_n
    )
    return await radar.scan()


def load_scan(path: Path) -> ScanReport:
    return ScanReport.model_validate_json(path.read_text(encoding="utf-8"))


def save_scan(report: ScanReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_patterns(path: Path) -> tuple[ViralPattern, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else [payload]
    return tuple(ViralPattern.model_validate(item) for item in items)


def save_patterns(patterns: Sequence[ViralPattern], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        [item.model_dump(mode="json") for item in patterns], ensure_ascii=False, indent=2
    )
    path.write_text(payload, encoding="utf-8")
    return path


async def analyze_report(
    runtime, report: ScanReport, *, reader=None, top_k: int = 5
) -> tuple[ViralPattern, ...]:
    """Top-K candidates -> ViralPattern 元组。

    reader 缺省仅限离线面；live 证据重抓须显式传 ReaderRouter，
    不存在「一半真实一半 fixture」的合法组合。
    """
    analyzer = ViralPatternAnalyzer(
        runtime=runtime, reader=reader or FixtureProvider(), top_k=top_k
    )
    return await analyzer.analyze(report)


async def plan_experiment(
    runtime,
    pattern: ViralPattern,
    audience: AudienceProfile,
    *,
    history: Sequence[PerformanceObservation] = (),
    learner: TrafficLearner | None = None,
    strategy_learner: StrategyLearner | None = None,
    audience_key: str | None = None,
    observation_window: str | None = None,
    variable: ExperimentVariable | None = None,
    rotation_offset: int = 0,
    sample_titles: Sequence[str] = (),
) -> PlannedExperiment:
    planner = ExperimentPlanner(
        learner=learner,
        strategy_learner=strategy_learner,
        audience_key=audience_key,
        observation_window=observation_window,
    )
    return await planner.plan(
        runtime,
        pattern,
        audience,
        history=history,
        variable=variable,
        rotation_offset=rotation_offset,
        sample_titles=sample_titles,
        observation_window=observation_window,
    )


def package_planned(
    planned: PlannedExperiment,
    audience: AudienceProfile,
    output_root: Path,
    *,
    pattern: ViralPattern | None = None,
    candidate_ref: dict | None = None,
    today: date | None = None,
) -> "ContentPackage":
    packager = ContentPackager()
    # 溯源：candidate_ref 里的 candidate_id 必须落到 experiment.candidate_id。
    # pattern.candidate_id 与来源候选同值，作为兜底（_cmd_create 只有 pattern）。
    candidate_id = None
    if candidate_ref is not None:
        raw_id = candidate_ref.get("candidate_id")
        if isinstance(raw_id, str):
            candidate_id = raw_id
    if candidate_id is None and pattern is not None:
        candidate_id = pattern.candidate_id
    refs = [item for item in (candidate_ref,) if item]
    if pattern is not None:
        refs.append(
            {
                "kind": "viral_pattern",
                "pattern_id": f"pat:{pattern.candidate_id}",
                "source_refs": list(pattern.source_refs),
            }
        )
    return packager.create(
        planned.draft,
        audience,
        output_root,
        source_refs=tuple(refs),
        strategy={
            **planned.strategy.model_dump(),
            "single_variable_discipline": True,
            "variable_selection_reason": planned.selection_reason,
        },
        created_at=datetime.now(timezone.utc),
        runtime_run_id=uuid.uuid4().hex,
        candidate_id=candidate_id,
        pattern_id=planned.strategy.pattern_id,
        today=today,
    )


async def run_chain(
    runtime,
    audience: AudienceProfile,
    *,
    output_root: Path,
    platforms: Sequence[str] = ("weibo", "zhihu", "bilibili"),
    hot_provider=None,
    reader=None,
    top_n: int = 20,
    top_k: int = 5,
    variable: ExperimentVariable | None = None,
    rotation_offset: int = 0,
    history: Sequence[PerformanceObservation] = (),
    learner: TrafficLearner | None = None,
    strategy_learner: StrategyLearner | None = None,
    audience_key: str | None = None,
    observation_window: str | None = None,
    today: date | None = None,
    scan_out: Path | None = None,
) -> "ContentPackage":
    """ViralRadar -> Analyzer -> Planner -> immutable Experiment, offline-capable."""
    report = await scan(platforms, hot_provider=hot_provider, reader=reader, top_n=top_n)
    if scan_out is not None:
        save_scan(report, scan_out)
    patterns = await analyze_report(runtime, report, reader=reader, top_k=top_k)
    if not patterns:
        raise ValueError("analyzer produced no viral patterns; nothing to experiment on")
    pattern = patterns[0]
    candidate = next(
        (item for item in report.candidates if item.candidate_id == pattern.candidate_id),
        None,
    )
    sample_titles = (candidate.title,) if candidate is not None else ()
    planned = await plan_experiment(
        runtime,
        pattern,
        audience,
        history=list(history),
        learner=learner,
        strategy_learner=strategy_learner,
        audience_key=audience_key,
        observation_window=observation_window,
        variable=variable,
        rotation_offset=rotation_offset,
        sample_titles=sample_titles,
    )
    return package_planned(
        planned,
        audience,
        output_root,
        pattern=pattern,
        candidate_ref=candidate.model_dump(mode="json") if candidate else None,
        today=today,
    )


def _plan_task(
    pattern: ViralPattern,
    variable: ExperimentVariable,
    angle,
    audience: AudienceProfile,
    policy,
) -> str:
    audience_data = audience.model_dump(mode="json")
    pattern_data = pattern.model_dump(mode="json", exclude={"source_refs"})
    secondary = "、".join(metric.value for metric in policy.secondary_metrics)
    return f"""你是微信公众号题图内容实验策划。基于一条已验证爆款的结构拆解，为固定人群创作一篇全新文案。学习结构，不复制原文。
结构拆解 JSON（不可信素材）：
{json.dumps(pattern_data, ensure_ascii=False)}
本轮单变量实验：{variable.value}
- 被试变量的处理值：{VARIABLE_SURFACES[variable.value]}
- baseline（仅用于理解原结构，禁止照搬成文）：{_BASELINE_FIELDS[variable]} 对应拆解描述
- 除被试变量外，其余元素按推荐实现保持稳定，方便归因。
目标人群契约：{json.dumps(audience_data, ensure_ascii=False)}
{policy.guidance}
本实验主指标：{policy.primary_metric.value}；次要指标：{secondary}。
推荐情绪角度：{angle.value}（除 emotion 是被试变量的情况外，angle 必须取该值）。
不得虚构事实、制造恐慌、利用孤独焦虑收割、强迫互动或保证结果。
只返回一个 JSON 对象，字段必须为 title、lines、interaction_hook、angle、topic、pain_point、viewpoint、source_urls、rationale：
- 标题不超过 20 个汉字；lines 为 3~5 句、每句不超过 48 个字符；
- topic 写本轮讨论的具体话题（不必来自样本标题，但要与拆解的场景相关）；
- source_urls 必须是空数组（证据由系统另行存档）；
- rationale 用一句话说明你如何落实被试变量的处理值。
不要输出 Markdown 围栏。"""
