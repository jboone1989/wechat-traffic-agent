from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from wechat_traffic_agent.editorial_intervention import (
    EditorDecision,
    bind_editorial_intervention,
    agent_candidate_owns_outcome,
)
from wechat_traffic_agent.learning import TrafficLearner
from wechat_traffic_agent.models import (
    ContentObjective,
    EmotionAngle,
    PerformanceObservation,
    TietuDraft,
)
from wechat_traffic_agent.objectives import policy_for
from wechat_traffic_agent.strategy_learning import StrategyLearner


BASE = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)


def _draft(*, title: str = "别让副业偷走你的下班") -> TietuDraft:
    return TietuDraft(
        title=title,
        lines=(
            "你盯着阅读量，不是在复盘，",
            "是在等一个数字证明今晚没白熬。",
            "今晚先写清楚你在替谁说话。",
        ),
        interaction_hook="你做内容最累的，是没流量，还是不知道为谁写？",
        angle=EmotionAngle.GENTLE_REBELLION,
        topic="做内容的人正在被流量绑架",
        pain_point="深夜用更新数量对抗流量焦虑",
        viewpoint="增加发布数量之前，先弄清楚内容在替哪个具体的人说话。",
        source_urls=("https://x.com/WolfMoneyNotes/status/2077624635105980561",),
        rationale="命中副业创作者深夜看数据的场景。",
    )


def _obs(post_id: str, *, reads: int, angle: str = "gentle_rebellion") -> PerformanceObservation:
    return PerformanceObservation(
        post_id=post_id,
        angle=angle,
        experiment_id=f"{post_id}-exp",
        variable_under_test="title_pattern",
        treatment_value="反差式",
        audience_key="silver",
        objective=ContentObjective.READS,
        observation_window="24h",
        reads=reads,
        published_at=BASE,
    )


def test_episode_records_original_published_diff_decision_and_versions() -> None:
    original = _draft()
    published = _draft(title="下班先写清你在替谁说话")
    bound = bind_editorial_intervention(
        experiment_id="2026-08-27-exp-01",
        post_id="post-edited",
        original_draft=original,
        published_content=published,
        editor_decision=EditorDecision.PUBLISH_WITH_EDITS,
        policy_version="policy-v3",
        model_version="composer-2026-08",
        config_version="cfg-12",
    )
    assert bound.experiment_id == "2026-08-27-exp-01"
    assert bound.post_id == "post-edited"
    assert bound.original_draft == original
    assert bound.published_content == published
    assert "title" in bound.edit_diff
    assert bound.editor_decision is EditorDecision.PUBLISH_WITH_EDITS
    assert bound.policy_version == "policy-v3"
    assert bound.model_version == "composer-2026-08"
    assert bound.config_version == "cfg-12"
    with pytest.raises((TypeError, ValidationError)):
        bound.edit_diff = ""


def test_publish_as_is_keeps_agent_candidate_as_outcome_owner() -> None:
    original = _draft()
    bound = bind_editorial_intervention(
        experiment_id="2026-08-27-exp-02",
        post_id="post-as-is",
        original_draft=original,
        published_content=original,
        editor_decision=EditorDecision.PUBLISH_AS_IS,
        policy_version="policy-v3",
        model_version="composer-2026-08",
        config_version="cfg-12",
    )
    assert bound.edit_diff == ""
    assert agent_candidate_owns_outcome(bound) is True


def test_edited_publish_is_not_attributed_entirely_to_the_agent_candidate() -> None:
    original = _draft()
    published = _draft(title="下班先写清你在替谁说话")
    bound = bind_editorial_intervention(
        experiment_id="2026-08-27-exp-03",
        post_id="post-edited",
        original_draft=original,
        published_content=published,
        editor_decision=EditorDecision.PUBLISH_WITH_EDITS,
        policy_version="policy-v3",
        model_version="composer-2026-08",
        config_version="cfg-12",
    )
    assert agent_candidate_owns_outcome(bound) is False

    as_of = BASE + timedelta(hours=30)
    clean = _obs("post-clean", reads=400)
    history = [
        clean,
        _obs("post-edited", reads=9000),
    ]
    interventions = (bound,)
    expected = policy_for(ContentObjective.READS).reward(clean)
    strategy = StrategyLearner(min_samples=1).estimates(
        history, as_of=as_of, editorial_interventions=interventions
    )
    assert len(strategy) == 1
    assert strategy[0].samples == 1
    assert strategy[0].scored_samples == 1
    assert strategy[0].avg_reward == pytest.approx(expected)

    traffic = TrafficLearner().estimates(
        history,
        ContentObjective.READS,
        audience_key="silver",
        observation_window="24h",
        as_of=as_of,
        editorial_interventions=interventions,
    )
    rebellion = next(item for item in traffic if item.angle is EmotionAngle.GENTLE_REBELLION)
    assert rebellion.samples == 1
    assert rebellion.scored_samples == 1
    assert rebellion.reward == pytest.approx(expected)
