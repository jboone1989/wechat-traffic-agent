from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from wechat_traffic_agent.experiments import Experiment, ExperimentLedger
from wechat_traffic_agent.models import (
    AudienceProfile,
    ContentObjective,
    EmotionAngle,
    TietuDraft,
)


def _audience() -> AudienceProfile:
    return AudienceProfile(
        people="独自在大城市工作的职场人",
        scene="工作日深夜回家的十分钟",
        recurring_need="被理解并获得一个不消耗自己的提醒",
    )


def _draft() -> TietuDraft:
    return TietuDraft(
        title="别让副业偷走你的下班",
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


def test_ledger_allocates_next_free_slot_without_overwrite(tmp_path) -> None:
    ledger = ExperimentLedger()
    today = date(2026, 8, 26)
    ids: list[str] = []
    for _ in range(3):
        experiment_id, target = ledger.allocate(tmp_path, today)
        target.mkdir(parents=False, exist_ok=False)  # 必须能成功创建全新目录
        ids.append(experiment_id)
    assert ids == ["2026-08-26-exp-01", "2026-08-26-exp-02", "2026-08-26-exp-03"]


def test_ledger_continues_after_existing_slots(tmp_path) -> None:
    ledger = ExperimentLedger()
    today = date(2026, 8, 26)
    day_dir = tmp_path / today.isoformat()
    for name in ("exp-01", "exp-02", "exp-03"):
        (day_dir / name).mkdir(parents=True, exist_ok=False)
    experiment_id, target = ledger.allocate(tmp_path, today)
    assert experiment_id == "2026-08-26-exp-04"
    target.mkdir(parents=False, exist_ok=False)  # 续跑不覆盖历史槽位


def test_experiment_is_frozen_and_round_trips() -> None:
    experiment = Experiment(
        experiment_id="2026-08-26-exp-01",
        created_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        audience=_audience(),
        objective=ContentObjective.READS,
        strategy={"angle": "gentle_rebellion", "topic_mode": "provided_topics"},
        draft=_draft(),
        source_refs=(),
        runtime_run_id="run-0",
    )
    restored = Experiment.model_validate_json(experiment.model_dump_json())
    assert restored == experiment
    assert restored.candidate_id is None
    assert restored.pattern_id is None
    with pytest.raises((TypeError, ValidationError)):
        experiment.experiment_id = "mutated"  # frozen：历史不可变
