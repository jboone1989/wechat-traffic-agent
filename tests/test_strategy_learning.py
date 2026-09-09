from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from wechat_traffic_agent.learning import TrafficLearner
from wechat_traffic_agent.models import (
    ContentObjective,
    EmotionAngle,
    PerformanceObservation,
)
from wechat_traffic_agent.strategy_learning import StrategyLearner


BASE = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)


def _obs(
    post_id: str,
    *,
    angle: str = "relief",
    variable: str | None = None,
    treatment: str | None = None,
    audience_key: str | None = "silver",
    objective: ContentObjective = ContentObjective.READS,
    window: str | None = "24h",
    reads: int | None = 500,
    follows: int | None = None,
    comments: int | None = None,
    shares: int | None = None,
    conversions: int | None = None,
    minutes_offset: int = 0,
    impressions: int | None = None,
) -> PerformanceObservation:
    return PerformanceObservation(
        post_id=post_id,
        angle=angle,
        experiment_id=f"{post_id}-exp",
        variable_under_test=variable,
        treatment_value=treatment,
        audience_key=audience_key,
        objective=objective,
        observation_window=window,
        reads=reads,
        follows=follows,
        comments=comments,
        shares=shares,
        conversions=conversions,
        impressions=impressions,
        published_at=BASE + timedelta(minutes=minutes_offset),
    )


def test_observation_rejects_unknown_window_and_accepts_documented_ones() -> None:
    for window in ("24h", "48h", "7d"):
        PerformanceObservation(post_id="x", angle="relief", observation_window=window)
    with pytest.raises(ValidationError):
        PerformanceObservation(
            post_id="x", angle="relief", observation_window="2h"  # 未文档化窗口，禁止入库
        )


def test_windows_and_audiences_never_merge_into_one_context() -> None:
    learner = StrategyLearner()
    history = [
        _obs("a1", variable="title_pattern", treatment="反差式", reads=900),
        _obs("a2", variable="title_pattern", treatment="数字式", audience_key="programmer"),
        _obs("a3", variable="title_pattern", treatment="反差式", window="7d", reads=50),
    ]
    estimates = learner.estimates(history)
    keys = {estimate.context_key for estimate in estimates}
    # 同一变量但不同 (audience, objective, window) 的观察必须住在不同 context
    assert keys == {
        "(silver, reads, 24h)",
        "(programmer, reads, 24h)",
        "(silver, reads, 7d)",
    }


def test_silver_nostalgia_success_does_not_leak_to_programmer_context() -> None:
    learner = StrategyLearner(min_samples=1)
    history = [
        # 银发人群：nostalgia 高分
        _obs("s1", variable="emotion", treatment="nostalgia", reads=5000),
        _obs("s2", variable="interaction_hook", treatment="晒图征集", reads=4800),
    ]
    contexts = learner.contexts(history)
    assert ("silver", "reads", "24h") in contexts
    assert ("programmer", "reads", "24h") not in contexts

    silver = [e for e in learner.estimates(history) if e.context_key == "(silver, reads, 24h)"]
    nostalgia = next(e for e in silver if e.variable == "emotion")
    assert nostalgia.samples == 1
    assert nostalgia.avg_reward is not None and nostalgia.avg_reward > 0


def test_different_objectives_do_not_share_reward() -> None:
    learner = StrategyLearner()
    history = [
        _obs("r1", variable="emotion", treatment="relief", reads=1000),
        _obs(
            "d1",
            variable="emotion",
            treatment="relief",
            objective=ContentObjective.DISCUSSION,
            comments=30,
        ),
    ]
    estimates = learner.estimates(history)
    keys = {e.context_key for e in estimates}
    assert keys == {"(silver, reads, 24h)", "(silver, discussion, 24h)"}
    by_key = {e.context_key: e for e in estimates}
    # reads context 的评论数不可见：d1 的 comments 不进入 reads 计分
    assert by_key["(silver, reads, 24h)"].scored_samples == 1
    assert by_key["(silver, discussion, 24h)"].scored_samples == 1


def test_missing_primary_metric_is_not_zero_but_unscored() -> None:
    learner = StrategyLearner()
    history = [
        _obs("ok1", variable="opening_hook", treatment="悬念前置", reads=300),
        _obs("no-metric", variable="opening_hook", treatment="提问开场", reads=None),
    ]
    estimates = learner.estimates(history)
    by_treatment = {e.value: e for e in estimates}
    assert by_treatment["提问开场"].samples == 1
    assert by_treatment["提问开场"].scored_samples == 0
    assert by_treatment["提问开场"].avg_reward is None  # UNKNOWN 保持未知
    assert by_treatment["悬念前置"].scored_samples == 1


def test_no_comparable_baseline_lift_is_unknown_not_zero() -> None:
    learner = StrategyLearner()
    history = [_obs("only", variable="emotion", treatment="relief", reads=900)]
    estimate = learner.estimates(history)[0]
    assert estimate.scored_samples == 1
    assert estimate.avg_reward is not None
    assert estimate.lift_vs_trailing_baseline is None
    assert estimate.smoothed_lift is None


def test_trailing_baseline_flattens_account_growth_constant_series_gives_zero_lift() -> None:
    learner = StrategyLearner()
    # 账号增长后同样 500 阅读，价值更高：收益按与近期中位数的差值衡量。
    # 恒定序列里每个点都等于 trailing 中位数 ⇒ lift 全为 0。
    # 序列第一点没有可比基线，不进入 lift 平均（UNKNOWN，不是 0.0）。
    history = [
        _obs(f"c{i}", variable="information_delta", treatment="反常识数据", reads=500, minutes_offset=i * 60)
        for i in range(5)
    ]
    estimates = learner.estimates(history)
    target = estimates[0]
    assert target.lift_vs_trailing_baseline == pytest.approx(0.0)
    assert target.smoothed_lift == pytest.approx(0.0)


def test_recommend_does_not_treat_unknown_baseline_as_neutral_zero() -> None:
    """First-in-context high reward has no trailing baseline.

    Coercing that UNKNOWN lift to 0.0 makes it look neutral and beat later
    treatments whose true lift vs the high opener is negative.
    """
    learner = StrategyLearner(min_samples=1)
    from wechat_traffic_agent.planner import VARIABLES

    history = [
        _obs("first", variable="emotion", treatment="relief", reads=5000, minutes_offset=0),
        _obs("t", variable="title_pattern", treatment="反差式", reads=100, minutes_offset=60),
        _obs("o", variable="opening_hook", treatment="悬念前置", reads=100, minutes_offset=120),
        _obs("i", variable="information_delta", treatment="反常识数据", reads=100, minutes_offset=180),
        _obs("h", variable="interaction_hook", treatment="晒图征集", reads=100, minutes_offset=240),
    ]
    decision = learner.recommend(
        history,
        candidate_variables=VARIABLES,
        audience_key="silver",
        observation_window="24h",
    )
    assert decision.exploration is False
    assert decision.variable.value != "emotion"


def test_recommend_explores_below_minimum_then_exploits_best_lift() -> None:
    learner = StrategyLearner(min_samples=2)
    from wechat_traffic_agent.planner import VARIABLES

    # 历史：只有 title_pattern 积累了 2 个高于最小样本的实验且 lift 为正
    history = []
    for index in range(3):
        history.append(
            _obs(
                f"t{index}",
                variable="title_pattern",
                treatment="反差式",
                reads=600 + index * 100,
                minutes_offset=index * 60,
            )
        )
    decision = learner.recommend(
        history,
        candidate_variables=VARIABLES,
        audience_key="silver",
        observation_window="24h",
    )
    # 其余四个变量都低于最小样本量 → 继续探索（按枚举顺序取第一个）
    assert decision.variable.value == "opening_hook"

    # 把所有变量都喂满（含 opening_hook）之后，推荐转向最高平滑 lift 的变量
    saturated = list(history)
    offsets = {
        "opening_hook": 100,
        "emotion": 200,
        "information_delta": 300,
        "interaction_hook": 400,
    }
    for name, base in offsets.items():
        for index in range(2):
            saturated.append(
                _obs(
                    f"{name}-{index}",
                    variable=name,
                    treatment=f"{name}-t",
                    reads=410 - index * 10,  # 平稳序列 ⇒ lift≈0
                    minutes_offset=base + index * 60,
                )
            )
    winning_decision = learner.recommend(
        saturated,
        candidate_variables=VARIABLES,
        audience_key="silver",
        observation_window="24h",
    )
    assert winning_decision.variable.value == "title_pattern"


def test_recommend_does_not_mix_observation_windows() -> None:
    """利用阶段：48h 窗口的高 lift 不得把 24h 窗口的排序带偏。"""
    learner = StrategyLearner(min_samples=3)
    from wechat_traffic_agent.planner import VARIABLES

    history = []
    minute = 0
    # 24h 窗口：五个变量各 3 个平稳样本，lift 全为 0。
    for name in (v.value for v in VARIABLES):
        for index in range(3):
            history.append(
                _obs(
                    f"24h-{name}-{index}",
                    variable=name,
                    treatment=f"{name}-t",
                    reads=500,
                    minutes_offset=minute,
                )
            )
            minute += 30
    # 48h 窗口：emotion 表现极好——污染源。
    for index, reads in enumerate((400, 600, 1000)):
        history.append(
            _obs(
                f"48h-emotion-{index}",
                variable="emotion",
                treatment="emotion-t-48h",
                reads=reads,
                window="48h",
                minutes_offset=minute,
            )
        )
        minute += 30

    decision = learner.recommend(
        history,
        candidate_variables=VARIABLES,
        audience_key="silver",
        observation_window="24h",
    )
    # 只看 24h：全员零 lift 并列 ⇒ 按枚举顺序取 title_pattern；
    # 若窗口被混算，emotion 会凭 48h 的虚高 lift 胜出。
    assert decision.variable.value == "title_pattern"
    assert decision.exploration is False


def test_recommend_exploration_counts_only_requested_window() -> None:
    """探索计数：其他窗口的样本不得当作本窗口的已探索证据。"""
    learner = StrategyLearner(min_samples=3)
    from wechat_traffic_agent.planner import VARIABLES

    history = []
    minute = 0
    # title_pattern 在 24h 只有 2 个样本（< min_samples），另有 5 个在 48h。
    for index in range(2):
        history.append(
            _obs(
                f"24h-title-{index}",
                variable="title_pattern",
                treatment=f"title-t-{index}",
                reads=500,
                minutes_offset=minute,
            )
        )
        minute += 30
    for index, reads in enumerate((400, 450, 520, 600, 700)):
        history.append(
            _obs(
                f"48h-title-{index}",
                variable="title_pattern",
                treatment=f"title-t-48h-{index}",
                reads=reads,
                window="48h",
                minutes_offset=minute,
            )
        )
        minute += 30
    # 其余四个变量在 24h 各补满；emotion 抬升，是「本应的利用赢家」。
    flat = ("opening_hook", "information_delta", "interaction_hook")
    for name in flat:
        for index in range(3):
            history.append(
                _obs(
                    f"24h-{name}-{index}",
                    variable=name,
                    treatment=f"{name}-t",
                    reads=500,
                    minutes_offset=minute,
                )
            )
            minute += 30
    for index, reads in enumerate((500, 700, 1100)):
        history.append(
            _obs(
                f"24h-emotion-{index}",
                variable="emotion",
                treatment="emotion-t",
                reads=reads,
                minutes_offset=minute,
            )
        )
        minute += 30

    decision = learner.recommend(
        history,
        candidate_variables=VARIABLES,
        audience_key="silver",
        observation_window="24h",
    )
    # 跨窗口混算会把 title_pattern 视为已探索并进入利用（选中 emotion）；
    # 正确口径下 title_pattern 在 24h 欠探索 → 继续探索它。
    assert decision.variable.value == "title_pattern"
    assert decision.exploration is True


def test_legacy_angle_recommendation_still_works_alongside() -> None:
    # PR6 不移除旧学习器：emotion 维度继续由它驱动写作角度。
    learner = TrafficLearner()
    assert isinstance(learner.recommend([], ContentObjective.READS), EmotionAngle)


def test_strategy_learner_angle_hint_matches_traffic_learner_direction() -> None:
    learner = StrategyLearner()
    # 每个角度给足探索样本后进入利用阶段；relief 阅读最高被推荐。
    history = [
        PerformanceObservation(post_id=f"{angle}-{index}", angle=angle, reads=reads)
        for angle, reads in (
            ("resonance", 20),
            ("relief", 900),
            ("nostalgia", 15),
            ("gentle_rebellion", 18),
            ("lonely_night", 22),
        )
        for index in range(2)
    ]
    angle = learner.recommended_angle(history, objective=ContentObjective.READS)
    assert angle is EmotionAngle.RELIEF
