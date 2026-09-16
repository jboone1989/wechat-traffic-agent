from pathlib import Path

from wechat_traffic_agent.channels.agent_home_experience import _x_social, agent_home_script
from wechat_traffic_agent.channels.agent_web import ContentAgentWebChannel
from wechat_traffic_agent.channels.web import StaticWebsiteConfig


def _config():
    return StaticWebsiteConfig(
        site_name="Content Research Lab", base_url="https://livingruntime.com",
        description="A persistent Content Agent.", contact_email="hello@example.com",
    )


def test_home_x_section_has_identity_static_fallback_and_lazy_official_embed():
    html = _x_social({
        "connected": True, "status": "OK", "handle": "agent", "name": "Agent",
        "avatar_url": "https://pbs.twimg.com/profile_images/a.jpg", "url": "https://x.com/agent",
        "recent_activity": [{"text": "A thought linked to cognition.", "expression_kind": "THOUGHT",
                             "url": "https://x.com/agent/status/42"}],
    })
    assert "Follow the agent on X" in html
    assert "@agent" in html
    assert "A thought linked to cognition." in html
    assert "twitter-timeline" in html
    assert 'data-dnt="true"' in html
    script = agent_home_script()
    assert "IntersectionObserver" in script
    assert "platform.twitter.com/widgets.js" in script


def test_privacy_page_discloses_lazy_x_embed():
    pages = ContentAgentWebChannel(_config())._static_pages()
    body = pages["/privacy/"][1]
    assert "X embed" in body
    assert "lazily load the official X timeline" in body
    assert "do-not-track" in body
