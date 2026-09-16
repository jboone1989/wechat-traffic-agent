import json
from pathlib import Path

from wechat_traffic_agent.social_writing import load_social_style_context


def corpus(path: Path) -> Path:
    payload = {
        "schema_version": "social-writing-observations.v1",
        "purpose": "STYLE_ONLY",
        "status": "DISCOVERED",
        "performance_claim": "UNVERIFIED",
        "observations": [
            {"observation_id": "short", "channel": "X", "format": "SHORT_POST",
             "source_url": "https://x.com/example/status/1",
             "features": ["Lead with the current state.", "Use an if-then condition."]},
            {"observation_id": "recap", "channel": "X", "format": "MARKET_RECAP",
             "source_url": "https://x.com/example/status/2",
             "features": ["Name the move first.", "End with what to watch next."]},
            {"observation_id": "article", "channel": "X", "format": "ARTICLE",
             "source_url": "https://x.com/example/article/3",
             "features": ["State a thesis.", "Include counter-risks before implications."]},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_thought_uses_social_post_shapes_but_not_article(tmp_path):
    value = load_social_style_context(corpus(tmp_path / "styles.json"), expression_kind="THOUGHT", market_pulse=True)
    assert value["purpose"] == "STYLE_ONLY"
    assert value["performance_claim"] == "UNVERIFIED"
    ids = {row["observation_id"] for row in value["observations"]}
    assert ids == {"short", "recap"}
    assert any("never as factual evidence" in rule for rule in value["rules"])


def test_longform_uses_article_shape_only(tmp_path):
    value = load_social_style_context(corpus(tmp_path / "styles.json"), expression_kind="LONGFORM", market_pulse=False)
    assert [row["observation_id"] for row in value["observations"]] == ["article"]


def test_missing_style_corpus_fails_open_without_inventing_learning(tmp_path):
    value = load_social_style_context(tmp_path / "missing.json", expression_kind="THOUGHT", market_pulse=True)
    assert value == {"purpose": "STYLE_ONLY", "status": "UNAVAILABLE", "observations": []}
