from wechat_traffic_agent.channels.operator_console_page import operator_console_document


def test_operator_console_uses_admin_information_architecture():
    page = operator_console_document()

    assert 'data-panel="dashboard"' in page
    assert 'data-panel="todos"' in page
    assert 'data-panel="resources"' in page
    assert 'data-panel="config"' in page
    assert "仪表盘" in page
    assert "待办事项" in page
    assert "资源管理" in page
    assert "配置管理" in page
    assert 'id="dashboardHealth"' in page
    assert 'id="dashboardActivity"' in page
    assert "@media(max-width:760px)" in page


def test_operator_console_keeps_existing_admin_controls():
    page = operator_console_document()

    assert "添加服务器" in page
    assert 'id="sshHost"' in page
    assert 'id="sshPassword"' in page
    assert 'id="bootstrapHost"' in page
    assert 'id="secrets"' in page
    assert 'id="localBrowserStart"' in page
    assert 'href="/operator/chat/"' in page
