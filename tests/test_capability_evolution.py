from datetime import datetime, timezone
from types import SimpleNamespace

from wechat_traffic_agent.capability_evolution import ContentCapabilityEvolution


NOW = datetime(2026, 9, 18, 4, 0, tzinfo=timezone.utc)


class Runtime:
    def __init__(self):
        self.calls = []
        self.persisted = 0

    def invoke(self, capability_id, payload):
        self.calls.append((capability_id, dict(payload)))
        query = payload["query"]
        slug = "jev-ultrafast" if "browser" in query.lower() else "alternative"
        return SimpleNamespace(
            receipt_id=f"receipt:{len(self.calls)}",
            payload={
                "ok": True,
                "results": [
                    {
                        "title": f"{slug} maintained candidate",
                        "url": f"https://github.com/example/{slug}",
                        "summary": (
                            "Open source browser automation agent library"
                            if "browser" in query.lower()
                            else "Maintained async HTTP client or extraction library"
                        ),
                    }
                ],
            },
        )

    def persist(self):
        self.persisted += 1


def test_capability_evolution_discovers_challengers_but_never_replaces_from_search_claims(tmp_path):
    runtime = Runtime()
    evolution = ContentCapabilityEvolution(
        state_root=tmp_path,
        capability_runtime=runtime,
    )

    first = evolution.review(now=NOW, cycle=100)
    second = evolution.review(now=NOW, cycle=100)

    assert first["core_protected_count"] == 1
    protected = next(
        row for row in first["slots"]
        if row["capability_id"] == "subject_continuity"
    )
    assert protected["status"] == "CORE_PROTECTED"
    browser = next(
        row for row in first["slots"]
        if row["capability_id"] == "browser_automation"
    )
    assert browser["status"] == "CHALLENGERS_DISCOVERED"
    assert browser["challenger_count"] >= 1
    assert evolution.engine.slots["browser_automation"].current_provider == "playwright"
    assert not evolution.engine.replacements

    browser_second = next(
        row for row in second["slots"]
        if row["capability_id"] == "browser_automation"
    )
    assert browser_second["status"] == "NOT_DUE"


def test_capability_evolution_state_survives_restart(tmp_path):
    runtime = Runtime()
    first = ContentCapabilityEvolution(
        state_root=tmp_path,
        capability_runtime=runtime,
    )
    first.review(now=NOW, cycle=100)

    second = ContentCapabilityEvolution(
        state_root=tmp_path,
        capability_runtime=runtime,
    )

    assert second.engine.challengers
    assert second.engine.last_search_cycle["browser_automation"] == 100
    assert second.engine.slots["subject_continuity"].core_protected is True


def test_noise_search_results_do_not_become_challengers():
    noise = ContentCapabilityEvolution._challenger(
        "browser_automation",
        {
            "title": "OpenAI | Research & Deployment",
            "url": "https://openai.com/",
            "summary": "AI research company",
        },
        cycle=1,
        evidence_ref="receipt:noise",
    )
    assert noise is None


def test_qualified_github_library_result_becomes_challenger():
    challenger = ContentCapabilityEvolution._challenger(
        "browser_automation",
        {
            "title": "browser-use: browser automation for AI agents",
            "url": "https://github.com/browser-use/browser-use",
            "summary": "Open source browser automation agent library built around Chromium.",
        },
        cycle=1,
        evidence_ref="receipt:github",
    )
    assert challenger is not None
    assert challenger.provider == "github.com"
    assert "technical-source-qualified:v1" in challenger.evidence_refs


def test_remote_browser_service_requires_technical_signal():
    bad = ContentCapabilityEvolution._challenger(
        "remote_browser_service",
        {
            "title": "ChatGPT",
            "url": "https://chatgpt.com/",
            "summary": "Chat with an AI assistant",
        },
        cycle=1,
        evidence_ref="receipt:bad",
    )
    good = ContentCapabilityEvolution._challenger(
        "remote_browser_service",
        {
            "title": "Browserbase developer API",
            "url": "https://www.browserbase.com/docs",
            "summary": "Cloud browser automation API and SDK for agents.",
        },
        cycle=1,
        evidence_ref="receipt:good",
    )
    assert bad is None
    assert good is not None
