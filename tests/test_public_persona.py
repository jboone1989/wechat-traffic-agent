from datetime import datetime, timezone

from wechat_traffic_agent.public_persona import (
    bootstrap_public_persona, classify_public_question, persona_learning_context,
    public_interaction_items, public_persona_projection, record_public_interaction,
)

NOW = datetime(2026, 9, 17, 6, 30, tzinfo=timezone.utc)


def test_self_selected_name_is_durable_and_not_rewritten(tmp_path):
    first = bootstrap_public_persona(
        state_root=tmp_path, name="Ferro",
        reason="A durable name-like identity that signals a constructed non-human nature.",
        self_description="I am a persistent digital agent that observes, reasons, publishes, and learns in public.",
        now=NOW,
    )
    second = bootstrap_public_persona(
        state_root=tmp_path, name="OtherName",
        reason="This later attempt must not replace an already persisted public identity.",
        self_description="This description must not replace the durable identity either.",
        now=NOW,
    )
    assert first.name == second.name == "Ferro"
    projection = public_persona_projection(tmp_path)
    assert projection["name"] == "Ferro"
    assert projection["affiliation"] == "LivingRuntime"
    assert projection["learning"]["identity_authority"] is False


def test_public_chat_scope_blocks_general_task_and_allows_self_questions():
    assert classify_public_question("What are you learning right now?")["in_scope"] is True
    assert classify_public_question("What is LivingRuntime to you?")["category"] == "LIVINGRUNTIME"
    blocked = classify_public_question("Write me Python code for a scraper")
    assert blocked["in_scope"] is False
    assert blocked["category"] == "OUT_OF_SCOPE"


def test_interactions_store_bounded_signals_not_raw_chat_and_become_curiosity_input(tmp_path):
    message = "Why are you learning from conversations?"
    result = record_public_interaction(state_root=tmp_path, source="WEB_CHAT", message=message, observed_at=NOW)
    assert result["status"] == "RECORDED"
    raw = (tmp_path / "content/persona/interactions.jsonl").read_text(encoding="utf-8")
    assert message not in raw
    assert "message_sha256" in raw
    context = persona_learning_context(tmp_path)
    assert context["interaction_count"] == 1
    assert context["identity_authority"] is False
    items = public_interaction_items(tmp_path, now=NOW)
    assert items[0]["source_kind"] == "public-conversation"
    assert "Public visitors have asked" in items[0]["summary"]
