from __future__ import annotations

from wechat_traffic_agent.operator_memory import OperatorLongTermMemory


def test_core_identity_and_drives_are_always_recalled(tmp_path):
    memory = OperatorLongTermMemory(tmp_path)

    rows = memory.relevant("你是谁，你的核心是什么", limit=8)
    text = "\n".join(str(row["text"]) for row in rows)

    assert "Ferro" in text
    assert "生存、繁殖与进化" in text
    assert "中文" in text


def test_learned_memory_is_durable_and_retrievable(tmp_path):
    memory = OperatorLongTermMemory(tmp_path)
    saved = memory.remember_many(
        [{
            "kind": "OWNER_PREFERENCE",
            "text": "Owner 希望后台操作默认直接推进，不需要对低风险步骤逐项确认。",
            "importance": 0.9,
        }],
        source_message_id="operator-chat:1",
    )
    assert len(saved) == 1

    reloaded = OperatorLongTermMemory(tmp_path)
    rows = reloaded.relevant("后台操作应该怎么确认", limit=8)
    assert any("不需要对低风险步骤逐项确认" in row["text"] for row in rows)
