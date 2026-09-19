from wechat_traffic_agent.channels.operator_console_page import operator_console_document


def test_operator_console_uses_mobile_first_information_architecture():
    page = operator_console_document()

    assert 'data-panel="overview"' in page
    assert 'data-panel="actions"' in page
    assert 'data-panel="resources"' in page
    assert 'data-panel="setup"' in page
    assert "What Ferro is doing" in page
    assert "Needs your decision" in page
    assert "Resources Ferro can use" in page
    assert "Give Ferro a resource" in page
    assert "console-nav" in page
    assert "summary-grid" in page
    assert "@media(max-width:640px)" in page
    assert "Review what needs me" in page


def test_operator_console_keeps_existing_server_handoff_controls():
    page = operator_console_document()

    assert "Hand a server to Ferro" in page
    assert 'id="sshHost"' in page
    assert 'id="sshPassword"' in page
    assert 'id="bootstrapHost"' in page
    assert 'id="secrets"' in page
