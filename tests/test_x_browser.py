from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from wechat_traffic_agent.x_browser import (
    XBrowserPublicationUnverified, XBrowserPublishResult, XBrowserPublisher, _post_id_from_url,
)
from wechat_traffic_agent.x_social import XHistoryLedger

NOW = datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc)


def _draft(root: Path, cycle: str = "v2-browser", *, fmt: str = "LONG_POST", text: str | None = None) -> Path:
    folder = root / "content/continuous/cycles" / cycle
    folder.mkdir(parents=True, exist_ok=True)
    value = text or ("A premium X post can be longer than 280 characters while remaining bound to one persisted cognition. " * 4)
    (folder / "social_expression_drafts.json").write_text(json.dumps({
        "schema_version": "content-social-expression-drafts.v2", "status": "DRAFT_READY", "cycle": cycle,
        "drafts": {"x": {"channel": "X", "format": fmt, "intent": "OBSERVATION", "text": value,
                         "style_observation_ids": [], "rationale": "Persisted channel draft."}},
    }), encoding="utf-8")
    (folder / "cognition.json").write_text(json.dumps({
        "worth_saying": True, "expression_kind": "THOUGHT", "what_changed": "Observed evidence changed the active question."
    }), encoding="utf-8")
    (folder / "mind_plan.json").write_text(json.dumps({"goal": {"inquiry_refs": ["inq-x"]}}), encoding="utf-8")
    return folder


class _Transport:
    def __init__(self, *, duplicate: bool = False):
        self.calls: list[str] = []
        self.duplicate = duplicate

    async def publish(self, *, text: str) -> XBrowserPublishResult:
        self.calls.append(text)
        return XBrowserPublishResult(
            post_id="2100431447047741762",
            url="https://x.com/caini56006962/status/2100431447047741762",
            handle="caini56006962", duplicate_found=self.duplicate,
        )


class _Unverified:
    async def publish(self, *, text: str) -> XBrowserPublishResult:
        raise XBrowserPublicationUnverified("could not verify public URL")


def test_tinyfish_x_publisher_is_double_gated(tmp_path: Path):
    _draft(tmp_path)
    disabled = asyncio.run(XBrowserPublisher(state_root=tmp_path, enabled=False, writes_enabled=True).after_content_cycle(cycle="v2-browser", now=NOW))
    read_only = asyncio.run(XBrowserPublisher(state_root=tmp_path, enabled=True, writes_enabled=False).after_content_cycle(cycle="v2-browser", now=NOW))
    assert disabled["status"] == "DISABLED"
    assert read_only["status"] == "WRITE_DISABLED"


def test_premium_long_post_is_published_once_and_bound_to_draft(tmp_path: Path):
    folder = _draft(tmp_path)
    draft = json.loads((folder / "social_expression_drafts.json").read_text())["drafts"]["x"]
    assert len(draft["text"]) > 280
    transport = _Transport()
    publisher = XBrowserPublisher(state_root=tmp_path, transport=transport, enabled=True, writes_enabled=True)
    first = asyncio.run(publisher.after_content_cycle(cycle="v2-browser", now=NOW))
    second = asyncio.run(publisher.after_content_cycle(cycle="v2-browser", now=NOW))
    receipt = json.loads((folder / "x_browser_publication.json").read_text())
    assert first["status"] == "POSTED"
    assert second["status"] == "ALREADY_POSTED"
    assert len(transport.calls) == 1
    assert receipt["post_id"] == "2100431447047741762"
    assert receipt["publication_authority"] is True
    assert len(receipt["draft_sha256"]) == 64
    rows = XHistoryLedger(tmp_path).recent()
    assert len(rows) == 1 and rows[0]["event_type"] == "EXPRESSION"
    assert rows[0]["text"] == draft["text"]
    assert rows[0]["inquiry_refs"] == ["inq-x"]


def test_duplicate_reconciliation_records_receipt_without_second_write(tmp_path: Path):
    folder = _draft(tmp_path)
    transport = _Transport(duplicate=True)
    result = asyncio.run(XBrowserPublisher(state_root=tmp_path, transport=transport, enabled=True, writes_enabled=True).after_content_cycle(cycle="v2-browser", now=NOW))
    receipt = json.loads((folder / "x_browser_publication.json").read_text())
    assert result["status"] == "POSTED"
    assert receipt["duplicate_found"] is True
    assert receipt["url"].endswith("/2100431447047741762")


def test_receipt_mismatch_fails_closed(tmp_path: Path):
    folder = _draft(tmp_path)
    publisher = XBrowserPublisher(state_root=tmp_path, transport=_Transport(), enabled=True, writes_enabled=True)
    asyncio.run(publisher.after_content_cycle(cycle="v2-browser", now=NOW))
    payload = json.loads((folder / "social_expression_drafts.json").read_text())
    payload["drafts"]["x"]["text"] += " changed"
    (folder / "social_expression_drafts.json").write_text(json.dumps(payload), encoding="utf-8")
    result = asyncio.run(publisher.after_content_cycle(cycle="v2-browser", now=NOW))
    assert result["status"] == "RECEIPT_DRAFT_MISMATCH"


def test_unverified_browser_write_does_not_create_fake_receipt_or_history(tmp_path: Path):
    folder = _draft(tmp_path)
    result = asyncio.run(XBrowserPublisher(state_root=tmp_path, transport=_Unverified(), enabled=True, writes_enabled=True).after_content_cycle(cycle="v2-browser", now=NOW))
    assert result["status"] == "PUBLICATION_UNVERIFIED"
    assert not (folder / "x_browser_publication.json").exists()
    assert XHistoryLedger(tmp_path).recent() == []


def test_article_is_not_silently_posted_as_feed_text(tmp_path: Path):
    _draft(tmp_path, cycle="article", fmt="ARTICLE", text="x" * 1200)
    result = asyncio.run(XBrowserPublisher(state_root=tmp_path, transport=_Transport(), enabled=True, writes_enabled=True).after_content_cycle(cycle="article", now=NOW))
    assert result == {"status": "UNSUPPORTED_FORMAT", "cycle": "article", "format": "ARTICLE"}


def test_public_x_status_url_parser():
    assert _post_id_from_url("https://x.com/caini56006962/status/2100431447047741762") == "2100431447047741762"
    assert _post_id_from_url("https://x.com/home") is None
