import asyncio
from datetime import date, datetime, timezone
import json
from pathlib import Path

from wechat_traffic_agent.content_mind import ContentMind
from wechat_traffic_agent.daily_reflection import DailyReflectionService
from wechat_traffic_agent.human_assistance import HumanAssistanceService, WeComNotifier


class FakeNotifier:
    configured = True
    def __init__(self): self.messages = []
    def send_text(self, text):
        self.messages.append(text)
        return {"status": "SENT"}


class FakeRuntime:
    def __init__(self):
        self.prompts = []

    async def cognitive_probe(self, prompt, max_tokens):
        self.prompts.append((prompt, max_tokens))
        return (
            "昨天真正暴露的问题不是内容数量，而是认知链路的大量超时让行动没有形成外部结果。"
            "我不能把一次 BELIEF_CHANGED 当成整体学习成功。今天优先验证认知恢复是否稳定，"
            "然后只推进一个能产生可观测结果的实验，并在结果回来后再决定是否扩大。"
        )


def _cycle(root: Path, unix: int, status: str, *, topic="risk", calls=0, cognition=None, error_type=None):
    folder = root / "content/continuous/cycles" / f"v2-{unix}"
    folder.mkdir(parents=True, exist_ok=True)
    row = {
        "status": status,
        "mind_plan": {"required_topic": topic},
        "mind_experience": {"model_calls": calls},
    }
    if cognition:
        row["cognition"] = {"cognition_state": cognition}
    if error_type:
        row["error_type"] = error_type
    (folder / "result.json").write_text(json.dumps(row), encoding="utf-8")


def test_daily_reflection_uses_real_cycle_facts_and_marks_missing_resource_unknown(tmp_path):
    # 2026-09-16 04:00/05:00 UTC == 12:00/13:00 Asia/Shanghai.
    _cycle(tmp_path, 1789531200, "PUBLISHED", topic="risk", calls=8, cognition="BELIEF_CHANGED")
    _cycle(tmp_path, 1789534800, "HELD_BY_CHECKS", topic="risk", calls=6)
    _cycle(tmp_path, 1789538400, "ERROR", topic="crypto-market", calls=0, error_type="ValueError")
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    service = DailyReflectionService(state_root=tmp_path, mind=mind, notifier=WeComNotifier(webhook_url=None))
    result = service.generate(target_date=date(2026, 9, 16), now=datetime(2026, 9, 17, 1, tzinfo=timezone.utc))
    assert result["status"] == "GENERATED"
    assert result["facts"]["cycle_count"] == 3
    assert result["facts"]["statuses"] == {"ERROR": 1, "HELD_BY_CHECKS": 1, "PUBLISHED": 1}
    assert result["facts"]["model_calls"] == 14
    assert result["facts"]["cognition_states"]["BELIEF_CHANGED"] == 1
    assert result["facts"]["resource"]["status"] == "UNKNOWN"
    assert "不补写消费数字" in result["reflection_text"]
    assert result["delivery_status"] == "PENDING_CONFIGURATION"


def test_daily_reflection_uses_real_cognition_and_writes_back_memory(tmp_path):
    _cycle(
        tmp_path,
        1789531200,
        "ERROR",
        topic="durable-cognition",
        calls=3,
        cognition="BELIEF_CHANGED",
        error_type="TimeoutError",
    )
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    runtime = FakeRuntime()
    service = DailyReflectionService(
        state_root=tmp_path,
        mind=mind,
        runtime=runtime,
        notifier=WeComNotifier(webhook_url=None),
    )
    result = asyncio.run(service.generate_cognitive(
        target_date=date(2026, 9, 16),
        now=datetime(2026, 9, 17, 1, tzinfo=timezone.utc),
    ))
    assert result["status"] == "GENERATED"
    assert result["reflection_mode"] == "COGNITIVE"
    assert "fallback" not in result["reflection_text"]
    assert result["memory_writeback"]["status"] == "RECORDED"
    assert runtime.prompts and runtime.prompts[0][1] == 1600
    assert "Evidence packet:" in runtime.prompts[0][0]


def test_continuous_reflection_runs_per_trigger_and_is_idempotent(tmp_path):
    _cycle(
        tmp_path,
        1789531200,
        "ERROR",
        topic="durable-cognition",
        calls=2,
        cognition="BELIEF_CHANGED",
        error_type="TimeoutError",
    )
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    runtime = FakeRuntime()
    service = DailyReflectionService(
        state_root=tmp_path,
        mind=mind,
        runtime=runtime,
        notifier=WeComNotifier(webhook_url=None),
    )
    now = datetime(2026, 9, 17, 1, 15, tzinfo=timezone.utc)
    refs = (
        "task-result:VERIFY_PREDICTION:VERIFIED",
        "reflection-trigger:event:test-verified",
    )

    first = asyncio.run(service.reflect_now(now, relevant_refs=refs))
    second = asyncio.run(service.reflect_now(now, relevant_refs=refs))

    assert first["status"] == "REFLECTED"
    assert first["memory_writeback"]["status"] == "RECORDED"
    assert second["status"] == "ALREADY_REFLECTED"
    assert len(runtime.prompts) == 1
    assert runtime.prompts[0][1] == 1400
    assert "持续的、事件驱动的自我反思" in runtime.prompts[0][0]
    assert list((tmp_path / "application/reflections").glob("*.json"))


def test_daily_reflection_sends_once_when_private_notifier_is_configured(tmp_path):
    _cycle(tmp_path, 1789531200, "PUBLISHED", calls=2)
    mind = ContentMind(root=tmp_path, subject_id="content-agent")
    notifier = FakeNotifier()
    service = DailyReflectionService(state_root=tmp_path, mind=mind, notifier=notifier)
    first = service.generate(target_date=date(2026, 9, 16), now=datetime(2026, 9, 17, 1, tzinfo=timezone.utc))
    second = service.generate(target_date=date(2026, 9, 16), now=datetime(2026, 9, 17, 2, tzinfo=timezone.utc))
    assert first["delivery_status"] == "SENT"
    assert second["status"] == "ALREADY_GENERATED"
    assert len(notifier.messages) == 1


def test_human_assistance_queues_without_wecom_and_deduplicates(tmp_path):
    service = HumanAssistanceService(state_root=tmp_path, notifier=WeComNotifier(webhook_url=None))
    now = datetime(2026, 9, 17, 1, tzinfo=timezone.utc)
    first = service.request(kind="RESOURCE_SUPPORT", urgency="HIGH", summary="Budget low",
                            requested_action="Review budget", now=now, details={"available_funds": 1.0})
    second = service.request(kind="RESOURCE_SUPPORT", urgency="HIGH", summary="Budget low",
                             requested_action="Review budget", now=now, details={"available_funds": 1.0})
    assert first["request_id"] == second["request_id"]
    assert first["delivery_status"] == "PENDING_CONFIGURATION"
    assert len(list((tmp_path / "application/human_assistance").glob("*.json"))) == 1


def test_wecom_notifier_rejects_non_wecom_secret_endpoints():
    try:
        WeComNotifier("https://example.com/webhook?key=secret")
    except ValueError as exc:
        assert "WeCom" in str(exc)
    else:
        raise AssertionError("non-WeCom endpoint should be rejected")
