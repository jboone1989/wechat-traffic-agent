from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "run_content_agent.py"


def test_persistent_content_entrypoint_composes_frameworks_network_and_drafts():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "build_content_application" in text
    assert "ContentNetworkService" in text
    assert "network_service.start()" in text
    assert "execution_lock" in text
    assert "TradingResearchDraftWorker" in text
    assert "trading_draft_worker_enabled" in text
    assert "draft_worker.run_once" in text
    assert 'add_argument("--once"' in text
    assert 'add_argument("--health"' in text
    assert "app.status_observation()['publish_mode']" in text
    assert "live" not in text.lower()
