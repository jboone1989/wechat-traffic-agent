from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "run_content_agent.py"


def test_persistent_content_entrypoint_composes_frameworks_and_network():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert "build_content_application" in text
    assert "ContentNetworkService" in text
    assert "network_service.start()" in text
    assert "execution_lock" in text
    assert 'add_argument("--once"' in text
    assert 'add_argument("--health"' in text
    assert "publish_mode=manual" in text
    assert "live" not in text.lower()
