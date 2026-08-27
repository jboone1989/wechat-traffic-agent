from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from wechat_traffic_agent.experiments import Experiment
from wechat_traffic_agent.learning import TrafficLearner
from wechat_traffic_agent.models import (
    AudienceProfile,
    ContentObjective,
    PerformanceObservation,
    TietuDraft,
)
from wechat_traffic_agent.planner import (
    VARIABLES,
    VARIABLE_SURFACES,
    ExperimentPlanner,
    ExperimentStrategy,
    ExperimentVariable,
    plan_experiment,
    run_chain,
    scan,
)

from _stubs import StubRuntime


def _audience(objective: ContentObjective = ContentObjective.READS) -> AudienceProfile:
    return AudienceProfile(
        people="通勤上班族",
        scene="地铁早晚高峰",
        recurring_need="想找回可支配的时间",
        objective=objective,
    )


_VALID_PATTERN = {
    "target_audience": "被通勤消耗的都市上班族",
    "scene": "早晚高峰地铁上刷手机",
    "core_desire": "想把通勤时间变成可支配时间",
    "pain_or_tension": "每天两小时被白白浪费又无力改变",
    "title_pattern": "反差句式「小账号，大爆发」并用具体倍数制造好奇缺口",
    "opening_hook": "先给一个反常识观察，再承诺三步拆出可复用结构",
    "curiosity_gap": "不直接说结论，把关键数字留到第二屏",
    "first_screen_promise": "读完你能拿走一张可迁移的结构清单",
    "emotional_trigger": "身份认同：说的就是我",
    "information_delta": "给出一个没人统计过的互动比例事实",
    "conflict_structure": "小体量 vs 大结果的落差",
    "pacing": "短句推进，每两句换一次信息密度",
    "payoff": "结尾交付清单式总结并点名下一步动作",
    "interaction_trigger": "以低门槛问题邀请读者报出自己的处境",
    "comment_demands": "评论区大量求模板和经验贴",
    "save_reason": "结构清单可反复对照使用",
    "share_reason": "替读者说出了身份认同的潜台词",
    "reusable_elements": ["反差式标题", "两屏内兑现承诺"],
    "platform_specific_elements": ["热榜位次曝光加成"],
    "observed_facts": [
        {"statement": "评论区反复出现『说的就是我』", "evidence_refs": ["hot:weibo:weibo-5"]},
    ],
    "hypotheses": [
        {"statement": "可能因身份认同促进分享", "evidence_refs": ["hot:weibo:weibo-5"]},
    ],
    "confidence": 0.7,
}


def _pattern(candidate_id: str = "weibo:weibo-5"):
    from wechat_traffic_agent.viral_pattern import ViralPattern

    return ViralPattern.model_validate({**_VALID_PATTERN, "candidate_id": candidate_id})


_DRAFT = {
    "title": "通勤两小时去哪了",
    "lines": [
        "你以为在赶路，其实在替别人加班",
        "真正属于你的时间只剩十四分钟",
        "先从承认这件事开始改变",
    ],
    "interaction_hook": "你的通勤时间是多久？评论区聊聊",
    # 空历史时学习者按探索规则给出确定推荐角；钉角度的测试都用它。
    "angle": TrafficLearner().recommend([], ContentObjective.READS).value,
    "topic": "通勤时间支配",
    "pain_point": "通勤吞噬个人时间",
    "viewpoint": "夺回通勤的第一步是把它的去向看清楚",
    "source_urls": [],
    "rationale": "沿用反差句式重写本人群版本",
}


def _draft_json(**overrides) -> str:
    return json.dumps({**_DRAFT, **overrides}, ensure_ascii=False)


def _expected_empty_history_angle() -> str:
    return _DRAFT["angle"]


def test_variable_rotation_is_deterministic_and_single() -> None:
    assert len(VARIABLES) == 5
    assert [v.value for v in VARIABLES] == [
        "title_pattern",
        "opening_hook",
        "emotion",
        "information_delta",
        "interaction_hook",
    ]
    # 轮转确定性：同一偏移永远拿到同一个变量（7 % 5 == 2）
    assert ExperimentPlanner.select_variable(0) is ExperimentVariable.TITLE_PATTERN
    assert ExperimentPlanner.select_variable(2) is ExperimentVariable.EMOTION
    assert ExperimentPlanner.select_variable(6) is ExperimentVariable.OPENING_HOOK
    assert ExperimentPlanner.select_variable(7) is ExperimentVariable.EMOTION
    assert set(VARIABLE_SURFACES) == {v.value for v in VARIABLES}


def test_strategy_records_baseline_and_treatment_pair() -> None:
    pattern = _pattern()
    draft = TietuDraft.model_validate(_DRAFT)
    strategy = ExperimentStrategy.build(
        pattern, draft, variable=ExperimentVariable.TITLE_PATTERN
    )
    assert strategy.pattern_id == "pat:weibo:weibo-5"
    assert strategy.variable_under_test is ExperimentVariable.TITLE_PATTERN
    assert strategy.baseline_value == pattern.title_pattern
    assert strategy.treatment_value == draft.title

    hook_strategy = ExperimentStrategy.build(
        pattern, draft, variable=ExperimentVariable.INTERACTION_HOOK
    )
    assert hook_strategy.baseline_value == pattern.interaction_trigger
    assert hook_strategy.treatment_value == draft.interaction_hook


def test_plan_composes_draft_and_pins_recommended_angle() -> None:
    stub = StubRuntime([_draft_json()])
    planned = asyncio.run(
        plan_experiment(
            stub,
            _pattern(),
            _audience(),
            history=[],
            variable=ExperimentVariable.TITLE_PATTERN,
        )
    )
    assert isinstance(planned.strategy, ExperimentStrategy)
    # 非 emotion 变量：角度必须钉死为学习者推荐值
    assert planned.draft.angle.value == _expected_empty_history_angle()
    task = stub.tasks[0]
    assert "单变量实验" in task
    assert "title_pattern" in task
    assert "source_urls 必须是空数组" in task


def test_plan_rejects_fabricated_source_urls() -> None:
    bad = dict(_DRAFT, source_urls=["https://invented.example.com/post"])
    stub = StubRuntime([json.dumps(bad, ensure_ascii=False)])
    with pytest.raises(ValueError, match="unsupported"):
        asyncio.run(
            plan_experiment(
                stub, _pattern(), _audience(), history=[], variable=ExperimentVariable.TITLE_PATTERN
            )
        )


def test_plan_rejects_angle_drift_on_non_emotion_variables() -> None:
    drifted = dict(_DRAFT)
    drifted["angle"] = "nostalgia" if _expected_empty_history_angle() != "nostalgia" else "lonely_night"
    stub = StubRuntime([json.dumps(drifted, ensure_ascii=False)])
    with pytest.raises(ValueError, match="angle drift|drifted"):
        asyncio.run(
            plan_experiment(
                stub, _pattern(), _audience(), history=[], variable=ExperimentVariable.TITLE_PATTERN
            )
        )


def test_plan_emotion_variable_may_change_the_angle() -> None:
    swapped = dict(_DRAFT, angle="relief")
    stub = StubRuntime([json.dumps(swapped, ensure_ascii=False)])
    planned = asyncio.run(
        plan_experiment(
            stub, _pattern(), _audience(), history=[], variable=ExperimentVariable.EMOTION
        )
    )
    assert planned.draft.angle.value == "relief"  # 角度本身就是本轮 treatment
    assert planned.strategy.variable_under_test is ExperimentVariable.EMOTION
    assert planned.strategy.baseline_value == _pattern().emotional_trigger


def test_plan_near_copy_guard_rejects_copied_title() -> None:
    copied = _draft_json(title="通勤效率观察日记")
    stub = StubRuntime([copied])
    with pytest.raises(ValueError, match="near-copy"):
        asyncio.run(
            plan_experiment(
                stub,
                _pattern(),
                _audience(),
                history=[],
                variable=ExperimentVariable.TITLE_PATTERN,
                sample_titles=("跨平台同题：通勤效率观察",),
            )
        )


def test_planner_uses_history_for_recommendation() -> None:
    # 五个角度都已有样本量后 learner 进入利用阶段；relief 得分最高被推荐。
    def _row(angle_value: str, reads: int, index: int) -> PerformanceObservation:
        return PerformanceObservation(
            post_id=f"{angle_value}-{index}", angle=angle_value, reads=reads
        )

    history = [
        _row(angle, reads, index)
        for angle, reads in (
            ("resonance", 20),
            ("relief", 800),
            ("nostalgia", 15),
            ("gentle_rebellion", 18),
            ("lonely_night", 22),
        )
        for index in range(2)
    ]
    recommended = TrafficLearner().recommend(history, ContentObjective.READS)
    assert recommended.value == "relief"
    stub = StubRuntime([json.dumps({**_DRAFT, "angle": "relief"}, ensure_ascii=False)])
    planned = asyncio.run(
        plan_experiment(
            stub,
            _pattern(),
            _audience(),
            history=history,
            variable=ExperimentVariable.TITLE_PATTERN,
            learner=TrafficLearner(),
        )
    )
    assert planned.draft.angle.value == "relief"


def test_scan_offline_defaults_to_fixture_provider() -> None:
    report = asyncio.run(scan(platforms=("weibo",), top_n=5))
    assert report.platforms[0].source == "fixture"
    assert 1 <= len(report.candidates) <= 5


def test_analyze_report_and_json_round_trip(tmp_path: Path) -> None:
    from wechat_traffic_agent.planner import analyze_report, load_scan

    report = asyncio.run(scan(platforms=("weibo",), top_n=5))
    scan_file = tmp_path / "scan.json"
    scan_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    restored = load_scan(scan_file)
    assert restored.candidates == report.candidates  # ScanReport JSON 可无损往返

    refs = {c.candidate_id: sorted(c.evidence_refs)[0] for c in restored.candidates[:2]}
    responses = []
    for cid in list(refs)[:2]:
        body = json.loads(json.dumps(_VALID_PATTERN))
        body["observed_facts"] = [
            {"statement": "样本进入平台热榜前十", "evidence_refs": [refs[cid]]}
        ]
        body["hypotheses"] = [{"statement": "可能由身份认同驱动传播", "evidence_refs": [refs[cid]]}]
        responses.append(json.dumps(body, ensure_ascii=False))
    stub = StubRuntime(responses)
    patterns = asyncio.run(analyze_report(stub, restored, top_k=2))
    assert [p.candidate_id for p in patterns] == [c.candidate_id for c in restored.candidates[:2]]


def test_run_chain_produces_immutable_experiment(tmp_path: Path) -> None:
    report = asyncio.run(scan(platforms=("weibo",), top_n=6))
    first_ref = sorted(report.candidates[0].evidence_refs)[0]

    def _pattern_response() -> str:
        body = json.loads(json.dumps(_VALID_PATTERN))
        body["observed_facts"] = [
            {"statement": "样本进入平台热榜前十", "evidence_refs": [first_ref]}
        ]
        body["hypotheses"] = [
            {"statement": "可能由身份认同驱动传播", "evidence_refs": [first_ref]}
        ]
        return json.dumps(body, ensure_ascii=False)

    # 队列顺序即阶段顺序：先 analyze（top_k=1），后 create（一篇草稿）
    stub = StubRuntime([_pattern_response(), _draft_json()])
    planned_package = asyncio.run(
        run_chain(
            stub,
            _audience(),
            output_root=tmp_path,
            platforms=("weibo",),
            hot_provider=None,
            reader=None,
            top_n=6,
            top_k=1,
            variable=None,
        )
    )
    assert planned_package is not None
    experiment = Experiment.model_validate_json(
        planned_package.experiment_file.read_text(encoding="utf-8")
    )
    assert experiment.strategy["variable_under_test"] == "title_pattern"
    assert experiment.strategy["baseline_value"]
    assert experiment.strategy["treatment_value"] == experiment.draft.title
    assert experiment.pattern_id == "pat:" + report.candidates[0].candidate_id
    assert experiment.candidate_id == report.candidates[0].candidate_id  # 溯源到来源候选
    assert experiment.audience.objective is ContentObjective.READS


def test_package_planned_propagates_candidate_id(tmp_path: Path) -> None:
    from wechat_traffic_agent.planner import PlannedExperiment, package_planned

    pattern = _pattern()
    draft = TietuDraft.model_validate(_DRAFT)
    planned = PlannedExperiment(
        draft=draft,
        strategy=ExperimentStrategy.build(
            pattern, draft, variable=ExperimentVariable.TITLE_PATTERN
        ),
    )
    package = package_planned(planned, _audience(), tmp_path, pattern=pattern)
    experiment = json.loads(package.experiment_file.read_text(encoding="utf-8"))
    # create 命令只有 pattern 没有 candidate_ref：从 pattern 兜底取候选 id
    assert experiment["candidate_id"] == pattern.candidate_id

    package_with_ref = package_planned(
        planned,
        _audience(),
        tmp_path / "ref",
        pattern=pattern,
        candidate_ref={"candidate_id": "bilibili:BV1xx", "other": "field"},
    )
    experiment_from_ref = json.loads(
        package_with_ref.experiment_file.read_text(encoding="utf-8")
    )
    assert experiment_from_ref["candidate_id"] == "bilibili:BV1xx"


def test_cli_parser_routes_subcommands_and_keeps_legacy_mode() -> None:
    from wechat_traffic_agent.__main__ import build_parser

    scan_args = build_parser().parse_args(["scan", "--top-n", "7"])
    assert scan_args.command == "scan"
    assert scan_args.top_n == 7

    create_args = build_parser().parse_args(
        ["create", "--patterns", "patterns.json", "--audience", "a.json"]
    )
    assert create_args.command == "create"
    assert create_args.patterns == Path("patterns.json")

    legacy = build_parser().parse_args(["--audience", "a.json", "--topics", "t.json"])
    assert legacy.command is None  # 无子命令 → 旧链路



def test_feedback_history_drives_variable_selection_when_unset() -> None:
    from datetime import datetime, timedelta, timezone

    from wechat_traffic_agent.models import PerformanceObservation
    from wechat_traffic_agent.strategy_learning import StrategyLearner

    def _obs(post_id, variable, reads, offset):
        return PerformanceObservation(
            post_id=post_id,
            angle="relief",
            variable_under_test=variable,
            treatment_value=f"{variable}-t",
            audience_key="silver",
            objective=ContentObjective.READS,
            observation_window="24h",
            reads=reads,
            published_at=datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
            + timedelta(minutes=offset),
        )

    history = [
        _obs("t0", "title_pattern", 500, 0),
        _obs("t1", "title_pattern", 650, 60),
        _obs("t2", "title_pattern", 800, 120),
        _obs("e0", "emotion", 400, 180),
        _obs("e1", "emotion", 400, 240),
    ]
    stub = StubRuntime([_draft_json()])
    planned = asyncio.run(
        plan_experiment(
            stub,
            _pattern(),
            _audience(),
            history=history,
            audience_key="silver",
            observation_window="24h",
            strategy_learner=StrategyLearner(min_samples=2),
        )
    )
    # 其余三个变量低于最小样本量 → 继续探索 opening_hook
    assert planned.strategy.variable_under_test is ExperimentVariable.OPENING_HOOK
    assert planned.selection_reason.startswith("feedback_history")


def test_feedback_without_window_key_falls_back_to_rotation() -> None:
    from datetime import datetime, timedelta, timezone

    def _obs(post_id, variable, offset):
        return PerformanceObservation(
            post_id=post_id,
            angle="relief",
            variable_under_test=variable,
            treatment_value=f"{variable}-t",
            audience_key="silver",
            objective=ContentObjective.READS,
            observation_window="24h",
            reads=600,
            published_at=datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
            + timedelta(minutes=offset),
        )

    history = [
        _obs("a0", "title_pattern", 0),
        _obs("a1", "title_pattern", 60),
        _obs("a2", "title_pattern", 120),
        _obs("b0", "emotion", 180),
        _obs("b1", "emotion", 240),
    ]
    stub = StubRuntime([_draft_json()])
    planned = asyncio.run(
        plan_experiment(stub, _pattern(), _audience(), history=history, audience_key="silver")
    )
    # 只给 audience_key 不给 observation_window：观察窗口不明，历史推断可能
    # 把 24h 和 7d 的结论混在一个口径里，宁可轮转。
    assert (
        planned.selection_reason == "rotation: no observation_window for scoped feedback"
    )
    assert planned.strategy.variable_under_test is ExperimentVariable.TITLE_PATTERN


def test_feedback_without_audience_key_never_infers_across_audiences() -> None:
    from datetime import datetime, timedelta, timezone

    def _obs(post_id, variable, offset):
        return PerformanceObservation(
            post_id=post_id,
            angle="relief",
            variable_under_test=variable,
            treatment_value=f"{variable}-t",
            audience_key="silver",
            objective=ContentObjective.READS,
            observation_window="24h",
            reads=600,
            published_at=datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
            + timedelta(minutes=offset),
        )

    history = [
        _obs("a0", "title_pattern", 0),
        _obs("a1", "title_pattern", 60),
        _obs("a2", "title_pattern", 120),
        _obs("b0", "emotion", 180),
        _obs("b1", "emotion", 240),
    ]
    stub = StubRuntime([_draft_json()])
    planned = asyncio.run(
        plan_experiment(stub, _pattern(), _audience(), history=history)
    )
    # 未提供 audience_key：宁可轮转也不把 silver 的结论泛化到任何其他人群
    assert (
        planned.selection_reason
        == "rotation: no audience_key/observation_window for scoped feedback"
    )
    assert planned.strategy.variable_under_test is ExperimentVariable.TITLE_PATTERN
