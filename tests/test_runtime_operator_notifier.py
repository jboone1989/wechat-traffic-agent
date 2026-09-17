from pathlib import Path
from wechat_traffic_agent.human_assistance import RuntimeMailboxNotifier, operator_notifier


def test_runtime_mailbox_notifier_publishes_without_holding_wecom_secret(tmp_path, monkeypatch):
    state = tmp_path / "runtime-state"
    state.mkdir()
    notifier = RuntimeMailboxNotifier(state)
    result = notifier.send_text("Ferro daily reflection\nI learned something.")
    assert result["status"] == "SENT"
    assert result["transport"] == "runtime-mailbox"
    notes = list((state / "mailbox" / "notifications").glob("note_*.json"))
    assert len(notes) == 1
    text = notes[0].read_text()
    assert "Ferro daily reflection" in text
    assert "WECOM" not in text


def test_operator_notifier_prefers_runtime_mailbox(tmp_path, monkeypatch):
    state = tmp_path / "runtime-state"
    state.mkdir()
    monkeypatch.setenv("CONTENT_OPERATOR_STATE_DIR", str(state))
    monkeypatch.setenv("CONTENT_WECOM_WEBHOOK_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake")
    assert isinstance(operator_notifier(), RuntimeMailboxNotifier)
