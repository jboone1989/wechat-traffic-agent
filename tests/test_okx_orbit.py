import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from wechat_traffic_agent.okx_orbit import OKXOrbitPublisher, OrbitPublishResult, OrbitSessionUnavailable, _post_id_from_url

NOW = datetime(2026, 9, 16, 16, 0, tzinfo=timezone.utc)


def _draft(root: Path, cycle: str = "v2-test", *, fmt: str = "SHORT_POST", text: str = "BTC moved while the causal driver remains unresolved. What evidence would separate the competing explanations?") -> Path:
    folder = root / "content/continuous/cycles" / cycle
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "social_expression_drafts.json").write_text(json.dumps({
        "schema_version": "content-social-expression-drafts.v2",
        "status": "DRAFT_READY", "cycle": cycle, "publication_authority": False,
        "drafts": {"okx": {"channel": "OKX", "format": fmt, "intent": "QUESTION",
                    "text": text, "style_observation_ids": [], "rationale": "Bounded public question."}},
    }), encoding="utf-8")
    return folder


class _Transport:
    def __init__(self): self.calls = []
    async def publish_short_post(self, *, text: str) -> OrbitPublishResult:
        self.calls.append(text)
        return OrbitPublishResult(post_id="87428678410784", url="https://www.okx.com/orbit/insight/87428678410784")


class _NoSession:
    async def publish_short_post(self, *, text: str) -> OrbitPublishResult:
        raise OrbitSessionUnavailable("expired")


def test_okx_orbit_publisher_is_double_gated(tmp_path):
    _draft(tmp_path)
    disabled = asyncio.run(OKXOrbitPublisher(state_root=tmp_path, enabled=False, writes_enabled=True).after_content_cycle(cycle="v2-test", now=NOW))
    read_only = asyncio.run(OKXOrbitPublisher(state_root=tmp_path, enabled=True, writes_enabled=False).after_content_cycle(cycle="v2-test", now=NOW))
    assert disabled["status"] == "DISABLED"
    assert read_only["status"] == "WRITE_DISABLED"


def test_okx_orbit_publisher_posts_persisted_draft_once_and_records_receipt(tmp_path):
    folder = _draft(tmp_path)
    transport = _Transport()
    publisher = OKXOrbitPublisher(state_root=tmp_path, transport=transport, enabled=True, writes_enabled=True)
    first = asyncio.run(publisher.after_content_cycle(cycle="v2-test", now=NOW))
    second = asyncio.run(publisher.after_content_cycle(cycle="v2-test", now=NOW))
    receipt = json.loads((folder / "okx_orbit_publication.json").read_text())
    assert first["status"] == "POSTED"
    assert second["status"] == "ALREADY_POSTED"
    assert len(transport.calls) == 1
    assert receipt["post_id"] == "87428678410784"
    assert receipt["url"].endswith("/87428678410784")
    assert receipt["publication_authority"] is True
    assert len(receipt["draft_sha256"]) == 64


def test_okx_orbit_publisher_refuses_article_and_missing_session(tmp_path):
    _draft(tmp_path, cycle="article", fmt="ARTICLE", text="x" * 1200)
    article = asyncio.run(OKXOrbitPublisher(state_root=tmp_path, transport=_Transport(), enabled=True, writes_enabled=True).after_content_cycle(cycle="article", now=NOW))
    _draft(tmp_path, cycle="session")
    session = asyncio.run(OKXOrbitPublisher(state_root=tmp_path, transport=_NoSession(), enabled=True, writes_enabled=True).after_content_cycle(cycle="session", now=NOW))
    assert article == {"status": "UNSUPPORTED_FORMAT", "cycle": "article", "format": "ARTICLE"}
    assert session["status"] == "SESSION_UNAVAILABLE"
    assert not (tmp_path / "content/continuous/cycles/session/okx_orbit_publication.json").exists()


def test_okx_orbit_receipt_is_bound_to_exact_draft(tmp_path):
    folder = _draft(tmp_path)
    publisher = OKXOrbitPublisher(state_root=tmp_path, transport=_Transport(), enabled=True, writes_enabled=True)
    asyncio.run(publisher.after_content_cycle(cycle="v2-test", now=NOW))
    payload = json.loads((folder / "social_expression_drafts.json").read_text())
    payload["drafts"]["okx"]["text"] += " changed"
    (folder / "social_expression_drafts.json").write_text(json.dumps(payload), encoding="utf-8")
    value = asyncio.run(publisher.after_content_cycle(cycle="v2-test", now=NOW))
    assert value["status"] == "RECEIPT_DRAFT_MISMATCH"


def test_public_orbit_post_and_insight_urls_are_verifiable():
    assert _post_id_from_url("https://www.okx.com/orbit/post/87555650159168") == "87555650159168"
    assert _post_id_from_url("https://www.okx.com/orbit/insight/87545920759584") == "87545920759584"
    assert _post_id_from_url("https://www.okx.com/orbit") is None
