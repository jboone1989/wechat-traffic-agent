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
