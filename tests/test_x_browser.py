from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from wechat_traffic_agent.x_browser import (
    XBrowserPublicationUnverified, XBrowserPublishResult, XBrowserPublisher, _post_id_from_url,
)
from wechat_traffic_agent.x_social import XHistoryLedger
from wechat_traffic_agent.x_browser_mentions import browser_mention_post

NOW = datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc)


def _draft(root: Path, cycle: str = "v2-browser", *, fmt: str = "LONG_POST", text: str | None = None) -> Path:
    folder = root / "content/continuous/cycles" / cycle
    folder.mkdir(parents=True, exist_ok=True)
    value = text or ("A premium X post can be longer than 280 characters while remaining bound to one persisted cognition. " * 4)
    (folder / "social_expression_drafts.json").write_text(json.dumps({
        "schema_version": "content-social-expression-drafts.v2", "status": "DRAFT_READY", "cycle": cycle,
        "x_strategy": {
            "schema_version": "ferro-l1-x-strategy.v1",
            "status": "SELECTED",
            "post_timing": "POST_NOW",
            "x_format": "LONG_POST" if fmt == "LONG_POST" else "SHORT_POST",
            "exploration_mode": "EXPLOIT",
            "angle": "EVIDENCE_FIRST",
            "interaction_mode": "ORIGINAL",
            "provider": "TEST",
            "model": "test",
        },
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


class _GrowthMission:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def publication_binding(self, *, cycle: str) -> dict:
        return {"status": "BOUND", "bound": True, "cycle": cycle}

    def start_experiment_from_publication(
        self, *, cycle: str, publication_receipt_id: str, post_id: str, now: datetime
    ) -> dict:
        self.calls.append({
            "cycle": cycle,
            "publication_receipt_id": publication_receipt_id,
            "post_id": post_id,
            "now": now,
        })
        return {
            "status": "STARTED" if len(self.calls) == 1 else "ALREADY_RUNNING",
            "experiment_id": "mission-exp-browser",
        }


async def _baseline_observer(*, cycle: str, now: datetime) -> dict:
    return {"status": "RECORDED", "cycle": cycle, "observed_at": now.isoformat()}


def test_x_publisher_is_double_gated(tmp_path: Path):
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
    growth = _GrowthMission()
    publisher = XBrowserPublisher(
        state_root=tmp_path,
        transport=transport,
        enabled=True,
        writes_enabled=True,
        growth_mission=growth,
        mission_baseline_observer=_baseline_observer,
    )
    first = asyncio.run(publisher.after_content_cycle(cycle="v2-browser", now=NOW))
    second = asyncio.run(publisher.after_content_cycle(cycle="v2-browser", now=NOW))
    receipt = json.loads((folder / "x_browser_publication.json").read_text())
    assert first["status"] == "POSTED"
    assert second["status"] == "ALREADY_POSTED"
    assert len(transport.calls) == 1
    assert receipt["post_id"] == "2100431447047741762"
    assert receipt["publication_authority"] is True
    assert len(receipt["draft_sha256"]) == 64
    assert first["mission_experiment_transition"]["status"] == "STARTED"
    assert second["mission_experiment_transition"]["status"] == "ALREADY_RUNNING"
    assert growth.calls == [
        {
            "cycle": "v2-browser",
            "publication_receipt_id": "x-browser-publication:v2-browser:2100431447047741762",
            "post_id": "2100431447047741762",
            "now": NOW,
        },
        {
            "cycle": "v2-browser",
            "publication_receipt_id": "x-browser-publication:v2-browser:2100431447047741762",
            "post_id": "2100431447047741762",
            "now": NOW,
        },
    ]
    rows = XHistoryLedger(tmp_path).recent()
    assert len(rows) == 1 and rows[0]["event_type"] == "EXPRESSION"
    assert rows[0]["text"] == draft["text"]
    assert rows[0]["inquiry_refs"] == ["inq-x"]
    assert rows[0]["lineage"]["mission_experiment_transition"]["status"] == "STARTED"


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
    growth = _GrowthMission()
    result = asyncio.run(
        XBrowserPublisher(
            state_root=tmp_path,
            transport=_Unverified(),
            enabled=True,
            writes_enabled=True,
            growth_mission=growth,
            mission_baseline_observer=_baseline_observer,
        ).after_content_cycle(cycle="v2-browser", now=NOW)
    )
    assert result["status"] == "PUBLICATION_UNVERIFIED"
    assert not (folder / "x_browser_publication.json").exists()
    assert XHistoryLedger(tmp_path).recent() == []
    assert growth.calls == []


def test_article_is_not_silently_posted_as_feed_text(tmp_path: Path):
    _draft(tmp_path, cycle="article", fmt="ARTICLE", text="x" * 1200)
    result = asyncio.run(XBrowserPublisher(state_root=tmp_path, transport=_Transport(), enabled=True, writes_enabled=True).after_content_cycle(cycle="article", now=NOW))
    assert result == {"status": "INVALID_ARTICLE_TITLE", "cycle": "article"}


def test_public_x_status_url_parser():
    assert _post_id_from_url("https://x.com/caini56006962/status/2100431447047741762") == "2100431447047741762"
    assert _post_id_from_url("https://x.com/home") is None


def test_browser_mention_post_normalizes_incoming_status_and_skips_self():
    row = browser_mention_post(
        text="  A public question   about Ferro  ", href="/other_agent/status/2100486738342764999",
        own_handle="caini56006962",
    )
    assert row == {
        "id": "2100486738342764999", "text": "A public question about Ferro",
        "username": "other_agent", "url": "https://x.com/other_agent/status/2100486738342764999",
    }
    assert browser_mention_post(text="self", href="/caini56006962/status/2100486738342765000", own_handle="caini56006962") is None
    assert browser_mention_post(text="bad", href="/home", own_handle="caini56006962") is None


def test_browser_post_does_not_require_growth_approval_or_baseline(tmp_path: Path):
    _draft(tmp_path)
    transport = _Transport()
    growth = _GrowthMission()
    growth.publication_guardrail = lambda: pytest.fail("Retired hard stop must not be consulted")
    result = asyncio.run(XBrowserPublisher(state_root=tmp_path, transport=transport,
        enabled=True, writes_enabled=True, growth_mission=growth).after_content_cycle(cycle="v2-browser", now=NOW))
    assert result["status"] == "POSTED"
    assert result["publication"]["mission_baseline"]["status"] == "MEASUREMENT_UNAVAILABLE"
    assert len(transport.calls) == 1
    assert len(growth.calls) == 1
    assert (tmp_path / "content/continuous/cycles/v2-browser/x_browser_publication.json").exists()


def test_browser_baseline_error_is_recorded_without_veto(tmp_path: Path):
    _draft(tmp_path)
    transport = _Transport()
    async def broken_observer(**kwargs):
        raise RuntimeError("Follower display unavailable")
    result = asyncio.run(XBrowserPublisher(state_root=tmp_path, transport=transport,
        enabled=True, writes_enabled=True, growth_mission=_GrowthMission(),
        mission_baseline_observer=broken_observer).after_content_cycle(cycle="v2-browser", now=NOW))
    assert result["status"] == "POSTED"
    assert result["publication"]["mission_baseline"]["status"] == "MEASUREMENT_FAILED"
    assert len(transport.calls) == 1


def test_l1_hold_prevents_browser_publication(tmp_path: Path):
    folder = _draft(tmp_path, cycle="hold", fmt="SHORT_POST", text="A grounded short post that should be held.")
    payload = json.loads((folder / "social_expression_drafts.json").read_text())
    payload["x_strategy"]["post_timing"] = "HOLD"
    (folder / "social_expression_drafts.json").write_text(json.dumps(payload))
    transport = _Transport()

    result = asyncio.run(
        XBrowserPublisher(
            state_root=tmp_path,
            transport=transport,
            enabled=True,
            writes_enabled=True,
        ).after_content_cycle(cycle="hold", now=NOW)
    )

    assert result["status"] == "SKIPPED"
    assert result["reason"] == "L1_X_STRATEGY"
    assert transport.calls == []


def test_native_publisher_requires_quality_receipt_for_the_exact_research_draft(tmp_path):
    import hashlib
    for kind in ("missing", "stale", "passed"):
        folder = _draft(tmp_path, cycle=kind)
        text = json.loads((folder / "social_expression_drafts.json").read_text())["drafts"]["x"]["text"]
        (folder / "learning_result.json").write_text(json.dumps({"cycle": kind, "progress": "LEARNED"}))
        if kind != "missing":
            (folder / "x_longform_learning_quality_0.json").write_text(json.dumps({
                "passed": True, "learning_cycle": kind,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest() if kind == "passed" else "stale-draft"}))
        transport = _Transport()
        result = asyncio.run(XBrowserPublisher(state_root=tmp_path, transport=transport,
            enabled=True, writes_enabled=True).after_content_cycle(cycle=kind, now=NOW))
        assert result["status"] == ("POSTED" if kind == "passed" else "HELD_BY_LEARNING_QUALITY")
        assert transport.calls == ([text] if kind == "passed" else [])
