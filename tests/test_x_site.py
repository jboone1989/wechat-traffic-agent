from wechat_traffic_agent.channels.agent_home_experience import agent_home_script, render_agent_home
from wechat_traffic_agent.channels.agent_web import ContentAgentWebChannel
from wechat_traffic_agent.channels.web import StaticWebsiteConfig


def _config():
    return StaticWebsiteConfig(
        site_name="Content Research Lab", base_url="https://livingruntime.com",
        description="A persistent Content Agent.", contact_email="hello@example.com",
    )


def test_home_has_no_legacy_x_navigation_or_duplicate_x_section():
    channel = ContentAgentWebChannel(_config())
    header = channel._header()
    home = render_agent_home(_config(), "", "", persona={"name": "Ferro"})

    assert 'href="https://x.com/caini56006962"' not in header
    assert '>X ↗</a>' not in header
    assert 'id="x-life"' not in home
    assert "Follow Ferro on X" not in home
    assert "X_CREDITS_DEPLETED" not in home
    assert "twitter-timeline" not in home
    script = agent_home_script()
    assert "platform.twitter.com/widgets.js" not in script
    assert "data-x-embed" not in script


def test_privacy_page_has_no_stale_x_embed_disclosure():
    pages = ContentAgentWebChannel(_config())._static_pages()
    body = pages["/privacy/"][1]
    assert "X embed" not in body
    assert "official X timeline" not in body
    assert "First-party audience measurement" in body
