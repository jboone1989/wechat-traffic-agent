from pathlib import Path

from wechat_traffic_agent.channels.web import StaticWebsiteConfig, assess_adsense_readiness
from wechat_traffic_agent.site_cli import _load_artifacts


ROOT = Path(__file__).resolve().parents[1]


def test_launch_corpus_meets_code_readiness_gate():
    artifacts = _load_artifacts(ROOT / "content" / "starter")
    config = StaticWebsiteConfig(
        site_name="Content Research Lab",
        base_url="https://content.example.org",
        description="Independent evidence-aware research and explainers.",
        contact_email="hello@content.example.org",
        publisher_name="Content Research Lab",
        adsense_client="ca-pub-1234567890123456",
        indexable=True,
    )

    report = assess_adsense_readiness(config, artifacts)

    assert len(artifacts) >= 10
    assert report.code_ready is True
    assert report.ready_for_adsense_connection is True
    assert report.external_dependencies == ()
    assert all(report.checks.values())


def test_example_production_config_stays_blocked_until_external_inputs_arrive():
    config_path = ROOT / "site" / "production.example.json"
    config = StaticWebsiteConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    artifacts = _load_artifacts(ROOT / "content" / "starter")

    report = assess_adsense_readiness(config, artifacts)

    assert report.code_ready is True
    assert report.ready_for_adsense_connection is False
    assert "production_https_domain" in report.external_dependencies
    assert "public_contact_email" in report.external_dependencies
    assert "adsense_publisher_id" in report.external_dependencies
