from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from wechat_traffic_agent.experiments import Experiment
from wechat_traffic_agent.models import (
    AudienceProfile,
    ContentObjective,
    EmotionAngle,
    TietuDraft,
)
from wechat_traffic_agent.packaging import ContentPackager


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


def _create(packager: ContentPackager, tmp_path, index: int):
    return packager.create(
        _draft(),
        _audience(),
        tmp_path,
        source_refs=(),
        strategy={"angle": "gentle_rebellion", "topic_mode": "provided_topics"},
        created_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        runtime_run_id=f"run-{index}",
        today=date(2026, 8, 26),
    )


def test_three_packages_same_day_are_three_distinct_experiments(tmp_path) -> None:
    packager = ContentPackager()
    packages = [_create(packager, tmp_path, index) for index in range(3)]
    assert [package.root.name for package in packages] == ["exp-01", "exp-02", "exp-03"]
    experiment_ids = {
        json.loads(package.experiment_file.read_text(encoding="utf-8"))["experiment_id"]
        for package in packages
    }
    assert experiment_ids == {
        "2026-08-26-exp-01",
        "2026-08-26-exp-02",
        "2026-08-26-exp-03",
    }
    for package in packages:
        assert {item.name for item in package.root.iterdir()} == {
            "experiment.json",
            "tietu.json",
            "tietu.md",
            "preview.html",
        }


def test_package_experiment_record_carries_full_provenance(tmp_path) -> None:
    package = _create(ContentPackager(), tmp_path, 0)
    experiment = json.loads(package.experiment_file.read_text(encoding="utf-8"))
    assert experiment["experiment_id"] == "2026-08-26-exp-01"
    assert experiment["objective"] == "reads"
    assert experiment["candidate_id"] is None
    assert experiment["pattern_id"] is None
    assert experiment["runtime_run_id"] == "run-0"
    assert experiment["strategy"] == {
        "angle": "gentle_rebellion",
        "topic_mode": "provided_topics",
    }
    assert experiment["source_refs"] == []
    assert experiment["draft"]["title"] == _draft().title
    assert experiment["created_at"] == "2026-08-26T12:00:00Z"
    restored = Experiment.model_validate_json(
        package.experiment_file.read_text(encoding="utf-8")
    )
    assert restored.experiment_id == "2026-08-26-exp-01"
    assert restored.created_at == datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def test_slot_collision_raises_instead_of_overwriting(tmp_path) -> None:
    packager = ContentPackager()
    day_dir = tmp_path / "2026-08-26"
    day_dir.mkdir(parents=True, exist_ok=False)
    (day_dir / "exp-01").write_text("stray file", encoding="utf-8")
    with pytest.raises(RuntimeError, match="已被占用"):
        _create(packager, tmp_path, 0)


@pytest.mark.parametrize(
    ("objective", "primary_label"),
    [
        (ContentObjective.READS, "阅读数"),
        (ContentObjective.FOLLOWS, "新增关注"),
        (ContentObjective.DISCUSSION, "评论数"),
        (ContentObjective.CONVERSION, "后端转化数"),
    ],
)
def test_markdown_backfill_section_reflects_objective(
    tmp_path, objective, primary_label
) -> None:
    audience = _audience().model_copy(update={"objective": objective})
    package = ContentPackager().create(
        _draft(),
        audience,
        tmp_path,
        source_refs=(),
        strategy={"angle": "gentle_rebellion", "topic_mode": "provided_topics"},
        created_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
        runtime_run_id="run-md",
        today=date(2026, 8, 26),
    )
    markdown = package.markdown.read_text(encoding="utf-8")
    assert f"- 主指标：{primary_label}" in markdown
    assert "不要填 0" in markdown
